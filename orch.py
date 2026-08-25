#!/usr/bin/env python3
"""Harness orchestrator: route pipeline steps to the cheapest agent that passes the gate.

State = the git repo. Handoff = the files on disk. That's the whole design.
"""
import json, os, pathlib, re, shutil, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).parent
STATS = ROOT / ".orch-stats.json"
STATE = ROOT / ".orch-state.json"    # live run state; panel.py reads it
STOP = ROOT / ".orch-stop"           # touch to halt after the current wave
RUNS = ROOT / ".orch-runs"           # per-step diffs from the last run
LOCK = threading.Lock()          # guards STATS/STATE + budget; agents run in threads
_state = {"status": "idle", "wave": 0, "budget": None, "steps": {}, "log": []}
EXHAUSTED = set()                # agents that hit a quota/rate limit this run; skipped thereafter
QUOTA_RE = re.compile(r"usage limit|rate limit|quota|too many requests|\b429\b", re.I)

# cost = relative $ per task. 0 = free tier (quota-limited, not free-forever).
# Free-tier slots verified Aug 2026. Gemini CLI stopped serving free users 2026-06-18;
# Antigravity CLI (agy) is its replacement and is headless.
AGENTS = {
    # {cwd} is substituted per run: agy and codex ignore the process cwd.
    "agy":      {"cmd": ["agy", "--add-dir", "{cwd}", "--dangerously-skip-permissions",
                         "--print-timeout", "30m", "-p"], "cost": 0},                  # Google free tier
    "codex":    {"cmd": ["codex", "exec", "-C", "{cwd}", "-s", "workspace-write",
                         "--skip-git-repo-check"], "cost": 0},                         # ChatGPT Free
    "copilot":  {"cmd": ["copilot", "--allow-all", "-s", "-p"], "cost": 0},            # Copilot Free
    "opencode": {"cmd": ["opencode", "run", "--dir", "{cwd}"], "cost": 0},              # Zen free models
    "crush":    {"cmd": ["crush", "run"], "cost": 0},                                  # BYOK
    "claude":   {"cmd": ["claude", "--dangerously-skip-permissions", "--output-format", "json", "-p"],
                 "cost": 3},
}

# Optional per-agent "model": passed as --model <name>. Leave unset for the CLI default.
# See `agy models`, `opencode models`, `codex --help`, `claude --help` for names.

# task type -> agents to try, cheapest first. Escalation order, not a fixed assignment.
# ponytail: hand-ordered priors; cost_per_pass() overrides them once stats exist.
ROUTES = {
    "frontend": ["agy", "opencode", "codex", "claude"],
    "backend":  ["codex", "agy", "opencode", "copilot", "claude"],
    "qa":       ["agy", "copilot", "crush", "claude"],
    "review":   ["claude"],          # quality gate: never cheap out on the judge
}


def emit(step_id=None, **fields):
    """Update live state and append to the timeline. Single writer, one file."""
    with LOCK:
        if step_id:
            _state["steps"].setdefault(step_id, {}).update(fields)
        else:
            _state.update(fields)
        msg = fields.get("msg")
        if msg:
            _state["log"].append(f"{time.strftime('%H:%M:%S')} {step_id or '-'}: {msg}")
            del _state["log"][:-200]          # ponytail: ring buffer, not a log db
        STATE.write_text(json.dumps(_state))


def stats():
    return json.loads(STATS.read_text()) if STATS.exists() else {}


def record(task, agent, ok):
  with LOCK:
    s = stats()
    k = f"{task}/{agent}"
    e = s.setdefault(k, {"pass": 0, "fail": 0})
    e["pass" if ok else "fail"] += 1
    STATS.write_text(json.dumps(s, indent=2))


def cost_per_pass(task, agent):
    """Expected spend to get one passing result. Lower wins.

    A free agent that fails is still cheap to retry, so it stays first until its
    pass rate drops far enough that paying up front is cheaper. That is the
    whole cost-vs-quality optimizer. Laplace prior so unseen agents get a turn.
    """
    e = stats().get(f"{task}/{agent}", {})
    p, f = e.get("pass", 0), e.get("fail", 0)
    rate = (p + 1) / (p + f + 2)
    return (AGENTS[agent]["cost"] + 1) / rate         # +1 = the wall-clock cost of any attempt


def available(agent):
    return subprocess.run(["which", AGENTS[agent]["cmd"][0]],
                          capture_output=True).returncode == 0


def run_agent(agent, prompt, cwd):
    a = AGENTS[agent]
    t = time.time()
    cwd = str(pathlib.Path(cwd).resolve())
    cmd = [c.replace("{cwd}", cwd) for c in a["cmd"]]
    if a.get("model"):
        cmd += ["--model", a["model"]]
    # subprocess cwd= does not update $PWD; node/bun CLIs (opencode) trust $PWD and
    # will happily write into the orchestrator's own directory. Set both.
    env = {**os.environ, "PWD": cwd}
    # ponytail: free tiers throttle; don't let a stalled one cost 30 min before the router learns
    timeout = 600 if a["cost"] == 0 else 1800
    try:
        r = subprocess.run(cmd + [prompt], cwd=cwd, env=env, capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)   # codex exec slurps stdin
    except subprocess.TimeoutExpired as e:
        partial = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return False, f"{partial[-2000:]}\n[agent timed out after {timeout}s]", time.time() - t
    return r.returncode == 0, r.stdout + r.stderr, time.time() - t


def claude_result(out):
    """claude --output-format json prints a list of messages; the last 'result' one has usage."""
    try:
        j = json.loads(out)
    except ValueError:
        return {}
    if isinstance(j, list):
        j = next((m for m in reversed(j) if m.get("type") == "result"), {})
    return j if isinstance(j, dict) else {}


def parse_tokens(agent, out):
    """Best-effort token count from an agent's stdout. None if the CLI doesn't say."""
    if agent == "claude":
        u = claude_result(out).get("usage") or {}
        return sum(v for k, v in u.items() if k.endswith("tokens") and isinstance(v, int)) or None
    m = re.search(r"tokens used\s*\n?\s*([\d,]+)", out)
    return int(m.group(1).replace(",", "")) if m else None


def parse_model(agent, out):
    """Which model the CLI actually used, if it says."""
    if agent == "claude":
        mu = claude_result(out).get("modelUsage") or {}
        return next(iter(mu), None)
    m = re.search(r"^\s*model:\s*(\S+)", out, re.M)
    return m.group(1) if m else None


def gate(step, cwd):
    """Verdict on the work just done. Cheap deterministic check beats an LLM judge."""
    check = step.get("check")
    if not check:
        return True, "no gate"
    r = subprocess.run(check, shell=True, cwd=cwd, capture_output=True, text=True)
    out = r.stdout[-2000:] + r.stderr[-2000:]
    if r.returncode:            # name the gate: a silent non-zero teaches the next agent nothing
        out = f"gate `{check}` exited {r.returncode}\n{out}"
    return r.returncode == 0, out


def run_step(step, cwd, budget):
    task = step["task"]
    candidates = [a for a in ROUTES[task] if available(a)]
    if not candidates:
        raise SystemExit(f"no agent installed for {task}: wanted {ROUTES[task]}")
    # try in descending value-per-dollar; ties broken by declared route order
    candidates.sort(key=lambda a: cost_per_pass(task, a))
    prompt = step["prompt"]

    for agent in candidates:
        if agent in EXHAUSTED:
            print(f"  skip {agent}: quota exhausted this run")
            continue
        with LOCK:                       # reserve before spending, or two threads
            if AGENTS[agent]["cost"] > budget["left"]:   # both spend the last dollar
                print(f"  skip {agent}: over budget")
                continue
            budget["left"] -= AGENTS[agent]["cost"]
        print(f"  -> {agent} ({task})")
        emit(step["id"], status="running", agent=agent, msg=f"start on {agent}")
        ok, out, secs = run_agent(agent, prompt, cwd)
        RUNS.mkdir(exist_ok=True)
        (RUNS / f"{step['id']}.{agent}.out").write_text(out)
        tokens = parse_tokens(agent, out)
        model = AGENTS[agent].get("model") or parse_model(agent, out) or "default"
        passed, why = gate(step, cwd) if ok else (False, out[-2000:])
        if not passed and QUOTA_RE.search(out):
            EXHAUSTED.add(agent)         # not a skill signal: don't record it against the agent
            emit(step["id"], msg=f"{agent} hit its quota; skipping it for the rest of the run")
        else:
            record(task, agent, passed)
        print(f"     {'PASS' if passed else 'FAIL'} in {secs:.0f}s" + (f", {tokens} tokens" if tokens else ""))
        attempts = _state["steps"].get(step["id"], {}).get("attempts", 0) + 1
        emit(step["id"], status="passed" if passed else "escalating", secs=round(secs),
             tokens=tokens, model=model, attempts=attempts, tail=why[-1500:],
             msg=f"{'PASS' if passed else 'FAIL'} on {agent} ({secs:.0f}s, {tokens or '?'} tokens)")
        if passed:
            return agent
        # the redirect: feed the failure forward so the next tier isn't blind
        prompt = f"{step['prompt']}\n\nA previous attempt by {agent} failed. Output:\n{why}"
    emit(step["id"], status="failed", msg="all agents failed")
    raise SystemExit(f"all agents failed step {task}")


def owned(step):
    """Paths (files or dirs) this step may write. "." means the whole tree."""
    return [d.strip("/") or "." for d in step["owns"]]


def ancestors(steps):
    """Transitive `needs` per step id."""
    by_id = {s["id"]: s for s in steps}
    memo = {}

    def walk(sid, seen):
        if sid in memo:
            return memo[sid]
        if sid in seen:
            raise SystemExit(f"dependency cycle at {sid}")
        out = set()
        for n in by_id[sid].get("needs", []):
            if n not in by_id:
                raise SystemExit(f"{sid} needs unknown step {n}")
            out |= {n} | walk(n, seen | {sid})
        memo[sid] = out
        return out

    return {s["id"]: walk(s["id"], set()) for s in steps}


def overlaps(a, b):
    if a == "." or b == ".":
        return True
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def out_of_bounds(step, files):
    """Changed files that no owned path covers. Discarded by merge_back; must be reported."""
    own = owned(step)
    if "." in own:
        return []
    return [f for f in files if not any(f == o or f.startswith(o.rstrip("/") + "/") for o in own)]


def changed_files(wt, base):
    r = subprocess.run(["git", "diff", "--name-only", f"{base}..HEAD"], cwd=wt,
                       capture_output=True, text=True)
    return [f for f in r.stdout.splitlines() if f]


def save_diff(wt, base, step_id):
    RUNS.mkdir(exist_ok=True)
    r = subprocess.run(["git", "diff", f"{base}..HEAD"], cwd=wt, capture_output=True, text=True)
    p = RUNS / f"{step_id}.diff"
    p.write_text(r.stdout)
    return p


def validate_ownership(steps):
    """Steps that can run concurrently must own disjoint paths.

    Ordered steps are exempt -- a review step legitimately owns the whole tree,
    it just never runs beside anything. Concurrency is decided by the `needs`
    graph, so that is what ownership is checked against.
    """
    deps = ancestors(steps)
    for i, a in enumerate(steps):
        for b in steps[i + 1:]:
            ordered = (a["id"] in deps[b["id"]]) or (b["id"] in deps[a["id"]])
            if ordered:
                continue
            for pa in owned(a):
                for pb in owned(b):
                    if overlaps(pa, pb):
                        raise SystemExit(
                            f"{a['id']} and {b['id']} can run concurrently but both "
                            f"own {pa if pa == pb else pa + ' / ' + pb}; "
                            f"add a needs edge or split the paths")


def worktree(repo, step):
    """Isolated checkout so parallel agents never share a working directory."""
    wt = pathlib.Path(repo).resolve().parent / f".orch-wt-{step['id']}"
    branch = f"orch/{step['id']}"
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo,
                   capture_output=True)
    subprocess.run(["git", "branch", "-D", branch], cwd=repo, capture_output=True)
    r = subprocess.run(["git", "worktree", "add", "-b", branch, str(wt)], cwd=repo,
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"worktree failed for {step['id']}: {r.stderr}")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()
    return str(wt), branch, base


def remove_path(p):
    """rm -rf for a file or a dir. Missing is fine."""
    p = pathlib.Path(p)
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.exists():
        p.unlink()


def merge_back(repo, step, branch):
    """Path-scoped checkout of only the owned paths (files or dirs). No merge
    algorithm runs, so no conflict can occur -- validate_ownership already proved
    concurrent owners are disjoint. Remove first so agent deletions propagate."""
    for d in owned(step):
        if d != ".":   # never rm -rf the repo itself. ponytail: root-owner deletions don't propagate
            remove_path(pathlib.Path(repo) / d)
        subprocess.run(["git", "checkout", branch, "--", d], cwd=repo, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo)
    subprocess.run(["git", "commit", "-qm", f"{step['id']} ({step['task']})"], cwd=repo)


def cleanup(repo, step, branch):
    wt = pathlib.Path(repo).resolve().parent / f".orch-wt-{step['id']}"
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo,
                   capture_output=True)
    subprocess.run(["git", "branch", "-D", branch], cwd=repo, capture_output=True)


def run_isolated(repo, step, budget):
    wt, branch, base = worktree(repo, step)
    try:
        agent = run_step(step, wt, budget)
        subprocess.run(["git", "add", "-A"], cwd=wt)
        subprocess.run(["git", "commit", "-qm", f"wip {step['id']}"], cwd=wt)
        save_diff(wt, base, step["id"])
        oob = out_of_bounds(step, changed_files(wt, base))
        emit(step["id"], diff=True, out_of_bounds=oob,
             msg=(f"WARNING out-of-bounds writes discarded: {', '.join(oob)}" if oob
                  else "all writes in bounds"))
        return step, branch, agent
    except SystemExit as e:
        # ponytail: keep the failed worktree for inspection; next run recreates it
        print(f"  kept worktree for {step['id']}: {wt}")
        raise RuntimeError(f"{step['id']}: {e}") from e


def main(pipeline_path, dry_run=False, serial=False):
    pipeline = json.loads(pathlib.Path(pipeline_path).read_text())
    repo, steps = pipeline.get("repo", "."), pipeline["steps"]
    validate_ownership(steps)
    if dry_run:
        done, pending, wave = set(), list(steps), 0
        while pending:
            ready = [s for s in pending if set(s.get("needs", [])) <= done]
            if not ready:
                raise SystemExit(f"deadlock: {[s['id'] for s in pending]} have unmet needs")
            wave += 1
            print(f"wave {wave}: {', '.join(s['id'] for s in ready)}")
            for s in ready:
                route = [a for a in ROUTES[s["task"]] if available(a)]
                route.sort(key=lambda a: cost_per_pass(s["task"], a))
                print(f"  {s['id']:<12} {s['task']:<9} owns={s['owns']}  route={route}")
            done |= {s["id"] for s in ready}
            pending = [s for s in pending if s["id"] not in done]
        return
    budget = {"left": pipeline.get("budget", 20)}
    done, pending, wave = set(), list(steps), 0
    STOP.unlink(missing_ok=True)
    t0 = time.time()
    _state.update(status="running", wave=0, log=[], budget=budget["left"], mode="serial" if serial else "parallel",
                  steps={s["id"]: {"status": "pending", "task": s["task"],
                                   "needs": s.get("needs", []), "owns": s["owns"]} for s in steps})
    emit(msg="run started")

    while pending:
        if STOP.exists():
            emit(status="stopped", msg="stop requested; halting between waves")
            raise SystemExit("stopped by control panel")
        ready = [s for s in pending if set(s.get("needs", [])) <= done]
        if not ready:
            raise SystemExit(f"deadlock: {[s['id'] for s in pending]} have unmet needs")
        wave += 1
        emit(wave=wave, msg=f"wave {wave}: {', '.join(s['id'] for s in ready)}")
        print(f"\n=== wave {wave}: {', '.join(s['id'] for s in ready)} (parallel) ===")
        with ThreadPoolExecutor(max_workers=1 if serial else len(ready)) as pool:
            futures = [pool.submit(run_isolated, repo, s, budget) for s in ready]
            results, errors = [], []
            for f in futures:
                try:
                    results.append(f.result())
                except Exception as e:      # let the rest of the wave finish
                    errors.append(e)
        # merge serially in declared order: keeps commit history deterministic
        for step, branch, agent in sorted(results, key=lambda r: steps.index(r[0])):
            merge_back(repo, step, branch)
            cleanup(repo, step, branch)
            print(f"  merged {step['id']} <- {agent}")
            emit(step["id"], status="merged", msg=f"merged from {agent}")
            emit(budget=budget["left"])
            done.add(step["id"])
        pending = [s for s in pending if s["id"] not in done]
        if errors:
            emit(status="failed", msg="; ".join(str(e) for e in errors))
            raise SystemExit("wave failed: " + "; ".join(str(e) for e in errors))
    wall = round(time.time() - t0)
    emit(status="done", budget=budget["left"], wall=wall, msg=f"run complete in {wall}s")
    (RUNS / "summary.json").write_text(json.dumps({
        "mode": _state["mode"], "wall": wall, "budget_left": budget["left"],
        "steps": {k: {f: v.get(f) for f in ("agent", "model", "secs", "tokens", "attempts", "out_of_bounds")}
                  for k, v in _state["steps"].items()}}, indent=1))
    print(f"\ndone in {wall}s. budget left: {budget['left']}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else "pipeline.json", dry_run="--dry-run" in sys.argv,
         serial="--serial" in sys.argv)
