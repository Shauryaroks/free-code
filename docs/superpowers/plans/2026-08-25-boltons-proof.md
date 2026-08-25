# Boltons Real-Project Proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the orchestrator core able to run three parallel bug-fix steps on a real repo (boltons) with file-level ownership, a change log, and out-of-bounds reporting — then run it and measure.

**Architecture:** All runtime changes go in `orch.py` (single-file runner, stdlib only); the panel gains one route for diffs. Ownership stays path-prefix based; the only new concept is "changed files not under any owned path". Tests in `test_orch.py` use real temporary git repos where git behaviour is what's being tested, and monkeypatched stubs elsewhere.

**Tech Stack:** Python 3 stdlib, git, pytest. Agents: codex/opencode/agy (free), claude (paid).

## Global Constraints

- stdlib only — no new dependencies.
- No git merge algorithm ever runs; merge-back stays a path-scoped `git checkout`.
- Out-of-bounds writes are discarded (the contract) but must be recorded in state and log.
- Cost-0 agents time out at **600s**, paid at **1800s**.
- `.orch-runs/` is gitignored.
- Commit after each task. Trailers are fine.

---

### Task 1: File-level `owns` in merge_back

**Files:**
- Modify: `orch.py:208-216` (`merge_back`)
- Test: `test_orch.py`

**Interfaces:**
- Produces: `merge_back(repo, step, branch)` unchanged signature; now correct when an `owns` entry is a file.

- [ ] **Step 1: Write the failing test** (append to `test_orch.py`)

```python
import subprocess, pathlib


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    """Real git repo: one commit with a.py and b.py, then a branch that edits both and adds c.py."""
    r = tmp_path / "repo"; r.mkdir()
    _git(r, "init", "-q"); _git(r, "config", "user.email", "t@t"); _git(r, "config", "user.name", "t")
    (r / "a.py").write_text("a0\n"); (r / "b.py").write_text("b0\n")
    _git(r, "add", "-A"); _git(r, "commit", "-qm", "init")
    _git(r, "checkout", "-qb", "orch/x")
    (r / "a.py").write_text("a1\n"); (r / "b.py").write_text("b1\n"); (r / "c.py").write_text("c1\n")
    _git(r, "add", "-A"); _git(r, "commit", "-qm", "wip")
    _git(r, "checkout", "-q", "master")
    return r


def test_merge_back_copies_owned_file_and_drops_unowned(repo):
    step = {"id": "x", "task": "backend", "owns": ["a.py"]}
    orch.merge_back(str(repo), step, "orch/x")
    assert (repo / "a.py").read_text() == "a1\n"      # owned: merged
    assert (repo / "b.py").read_text() == "b0\n"      # unowned: untouched
    assert not (repo / "c.py").exists()               # unowned new file: dropped
    assert "x (backend)" in _git(repo, "log", "-1", "--format=%s")
```

Note: `git init` may default to `main` on some machines. If the fixture fails on `checkout master`, run `git config --global init.defaultBranch master` once, or change both occurrences to `main`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_orch.py::test_merge_back_copies_owned_file_and_drops_unowned -v`
Expected: FAIL — `a.py` still `a0` (rmtree on a file is a no-op, but checkout should still copy… actually checkout copies fine; the failure is on deletions). If it PASSES already, still do Step 3: the deletion path is untested by this test and is the real bug.

- [ ] **Step 3: Implement — remove by type, then checkout**

Replace `merge_back` in `orch.py`:

```python
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
        remove_path(pathlib.Path(repo) / d)
        subprocess.run(["git", "checkout", branch, "--", d], cwd=repo, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo)
    subprocess.run(["git", "commit", "-qm", f"{step['id']} ({step['task']})"], cwd=repo)
```

- [ ] **Step 4: Add a deletion test**

```python
def test_merge_back_propagates_owned_file_deletion(repo):
    _git(repo, "checkout", "-q", "orch/x"); (repo / "a.py").unlink()
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "rm a"); _git(repo, "checkout", "-q", "master")
    orch.merge_back(str(repo), {"id": "x", "task": "backend", "owns": ["a.py"]}, "orch/x")
    assert not (repo / "a.py").exists()
```

- [ ] **Step 5: Run all tests**

Run: `python3 -m pytest test_orch.py -q`
Expected: all pass (11).

- [ ] **Step 6: Commit**

```bash
git add orch.py test_orch.py
git commit -m "merge_back: support file-level owns, propagate file deletions"
```

---

### Task 2: Out-of-bounds detection and change log

**Files:**
- Modify: `orch.py:194-205` (`worktree` — return base sha), `orch.py:226-236` (`run_isolated`), `orch.py:11-12` (add `RUNS` path)
- Modify: `.gitignore`
- Test: `test_orch.py`

**Interfaces:**
- Produces: `out_of_bounds(step, files) -> list[str]` (pure); `changed_files(wt, base) -> list[str]`; `save_diff(wt, base, step_id) -> Path` writing `RUNS / f"{step_id}.diff"`; state field `steps[id].out_of_bounds` (list) and `steps[id].diff` (bool).
- `worktree()` now returns `(wt, branch, base_sha)`.

- [ ] **Step 1: Failing tests**

```python
def test_out_of_bounds_is_files_not_under_any_owned_path():
    step = {"owns": ["boltons/strutils.py", "tests/"]}
    files = ["boltons/strutils.py", "tests/test_strutils.py", "boltons/urlutils.py", "README.md"]
    assert orch.out_of_bounds(step, files) == ["boltons/urlutils.py", "README.md"]


def test_changed_files_and_save_diff(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "RUNS", tmp_path / "runs")
    _git(repo, "checkout", "-q", "orch/x")
    base = _git(repo, "rev-parse", "master").strip()
    assert sorted(orch.changed_files(str(repo), base)) == ["a.py", "b.py", "c.py"]
    p = orch.save_diff(str(repo), base, "x")
    assert p.name == "x.diff" and "+a1" in p.read_text()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test_orch.py -k "out_of_bounds or save_diff" -v`
Expected: FAIL with `AttributeError: module 'orch' has no attribute 'out_of_bounds'`.

- [ ] **Step 3: Implement**

Add after `STOP = ...` in `orch.py`:

```python
RUNS = ROOT / ".orch-runs"           # per-step diffs from the last run
```

Add after `overlaps()`:

```python
def out_of_bounds(step, files):
    """Changed files that no owned path covers. Discarded by merge_back; must be reported."""
    own = owned(step)
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
```

Note `"tests/"` ownership: `owned()` strips slashes, so `o.rstrip("/")` handles both.

Change `worktree()` to return the base sha — replace its last line:

```python
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()
    return str(wt), branch, base
```

Replace `run_isolated`:

```python
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
```

Update the existing `_stub_git` in `test_orch.py` so the stubbed `worktree` returns three values, and stub the new functions:

```python
    monkeypatch.setattr(orch, "worktree", lambda repo, s: (repo, f"orch/{s['id']}", "base"))
    monkeypatch.setattr(orch, "save_diff", lambda wt, base, sid: None)
    monkeypatch.setattr(orch, "changed_files", lambda wt, base: [])
```

Add to `.gitignore`: `.orch-runs/`

- [ ] **Step 4: Run all tests**

Run: `python3 -m pytest test_orch.py -q`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add orch.py test_orch.py .gitignore
git commit -m "record per-step diffs and report out-of-bounds writes"
```

---

### Task 3: Panel shows diff and out-of-bounds files

**Files:**
- Modify: `panel.py` (`do_GET`, step row template)

**Interfaces:**
- Consumes: `RUNS / f"{id}.diff"`, state fields `diff`, `out_of_bounds`.
- Produces: `GET /diff/<id>` → text/plain diff.

- [ ] **Step 1: Add the route**

In `panel.py`, add `RUNS = ROOT / ".orch-runs"` next to `STOP`, and change `do_GET`:

```python
    def do_GET(self):
        if self.path in FILES:
            f = FILES[self.path]
            return self._send(f.read_text() if f.exists() else "{}")
        if self.path.startswith("/diff/"):
            sid = self.path[6:]
            f = RUNS / f"{sid}.diff"
            if "/" in sid or ".." in sid or not f.exists():     # no path tricks
                self.send_response(404); self.end_headers(); return
            return self._send(f.read_text(), "text/plain; charset=utf-8")
        self._send(PAGE, "text/html")
```

- [ ] **Step 2: Show it in the row**

In the `$('steps').innerHTML=` line, replace the status cell (the part from `<td class=${s.status}>` up to and including the closing `''}`) with:

```js
<td class=${s.status}>${s.status}${s.tail?`<details><summary>output</summary><pre>${esc(s.tail)}</pre></details>`:''}${s.diff?`<div><a href="/diff/${esc(id)}" target=_blank>diff</a></div>`:''}${(s.out_of_bounds||[]).length?`<div class=failed>out of bounds: ${esc(s.out_of_bounds.join(' '))}</div>`:''}
```

- [ ] **Step 3: Smoke test**

```bash
mkdir -p .orch-runs && printf 'diff --git a/x b/x\n+hi\n' > .orch-runs/t.diff
(python3 panel.py 8798 &) ; sleep 1
curl -s localhost:8798/diff/t | grep -c '+hi'          # expect 1
curl -s -o /dev/null -w '%{http_code}\n' localhost:8798/diff/../orch.py   # expect 404
pkill -f 'panel.py 8798'; rm .orch-runs/t.diff
```

- [ ] **Step 4: Commit**

```bash
git add panel.py
git commit -m "panel: per-step diff link and out-of-bounds list"
```

---

### Task 4: Per-agent timeout and `--dry-run`

**Files:**
- Modify: `orch.py:86-96` (`run_agent`), `orch.py:239-286` (`main`, `__main__`)
- Test: `test_orch.py`

**Interfaces:**
- Produces: `main(pipeline_path, dry_run=False)`; CLI `python3 orch.py pipeline.json --dry-run`.

- [ ] **Step 1: Failing tests**

```python
def test_free_agents_time_out_sooner(monkeypatch):
    seen = {}
    class R: returncode = 0; stdout = ""; stderr = ""
    def fake_run(cmd, **kw): seen[cmd[0]] = kw["timeout"]; return R()
    monkeypatch.setattr(orch.subprocess, "run", fake_run)
    orch.run_agent("opencode", "p", "."); orch.run_agent("claude", "p", ".")
    assert seen["opencode"] == 600 and seen["claude"] == 1800


def test_dry_run_spawns_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(orch, "run_isolated", lambda *a: (_ for _ in ()).throw(AssertionError("spawned")))
    steps = [
        {"id": "a", "task": "backend", "owns": ["x"], "prompt": "1"},
        {"id": "b", "task": "qa", "needs": ["a"], "owns": ["y"], "prompt": "2"},
    ]
    orch.main(_pipeline(tmp_path, steps), dry_run=True)
    out = capsys.readouterr().out
    assert "wave 1: a" in out and "wave 2: b" in out and "backend" in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test_orch.py -k "time_out or dry_run" -v`
Expected: both FAIL (`timeout` is 1800 for opencode; `main()` got unexpected kwarg).

- [ ] **Step 3: Implement**

In `run_agent`, replace the `subprocess.run(...)` call:

```python
    # ponytail: free tiers throttle; don't let a stalled one cost 30 min before the router learns
    timeout = 600 if a["cost"] == 0 else 1800
    r = subprocess.run(cmd + [prompt], cwd=cwd, env=env, capture_output=True, text=True,
                       timeout=timeout, stdin=subprocess.DEVNULL)   # codex exec slurps stdin
```

Change `main` signature to `def main(pipeline_path, dry_run=False):` and insert, right after `validate_ownership(steps)`:

```python
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
```

Replace the `__main__` block:

```python
if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else "pipeline.json", dry_run="--dry-run" in sys.argv)
```

- [ ] **Step 4: Run all tests**

Run: `python3 -m pytest test_orch.py -q`
Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add orch.py test_orch.py
git commit -m "per-tier agent timeouts; --dry-run prints waves and routes"
```

---

### Task 5: Target repo and `boltons.json` pipeline

**Files:**
- Create: `boltons.json`
- Create (outside repo): `~/Projects/orch-target-boltons` (clone)

- [ ] **Step 1: Clone and branch**

```bash
git clone -q https://github.com/mahmoud/boltons.git ~/Projects/orch-target-boltons
cd ~/Projects/orch-target-boltons && git checkout -qb orch-proof && python3 -m pytest -q 2>&1 | tail -1
```
Expected: `472 passed ...`

- [ ] **Step 2: Write the pipeline**

```json
{
  "repo": "../orch-target-boltons",
  "budget": 20,
  "steps": [
    {"id": "urlutils", "task": "backend",
     "owns": ["boltons/urlutils.py", "tests/test_urlutils.py"],
     "prompt": "Fix boltons issue #309 in boltons/urlutils.py: URL('http://username:password?@www.proxy.com:443') and URL('http://username:pass/word@www.proxy.com:443') currently raise URLParseError ('expected integer for port'). Special characters '?' and '/' inside the userinfo (credentials) part must not be treated as query/path separators; the password must be preserved verbatim (URL(...).password == 'password?' / 'pass/word'). Add regression tests to tests/test_urlutils.py. Edit only boltons/urlutils.py and tests/test_urlutils.py. Run python3 -m pytest -q before finishing; all tests must pass.",
     "check": "python3 -c \"from boltons.urlutils import URL; assert URL('http://username:password?@www.proxy.com:443').password=='password?'; assert URL('http://username:pass/word@www.proxy.com:443').password=='pass/word'; assert URL('http://username:password?@www.proxy.com:443').port==443\" && python3 -m pytest -q"},

    {"id": "strutils", "task": "backend",
     "owns": ["boltons/strutils.py", "tests/test_strutils.py"],
     "prompt": "Fix boltons issue #142 in boltons/strutils.py: camel2under('NSDecimalToUInt') returns 'ns_decimal_to_u_int' but should return 'ns_decimal_to_uint' -- a run of capitals followed by lowercase is one word, and a trailing run of capitals is one word. Existing behaviour must hold: camel2under('NSDecimalCompact')=='ns_decimal_compact', camel2under('BasicPHPTest')=='basic_php_test', camel2under('HTTPResponse')=='http_response'. Add a regression test to tests/test_strutils.py. Edit only boltons/strutils.py and tests/test_strutils.py. Run python3 -m pytest -q before finishing; all tests must pass.",
     "check": "python3 -c \"from boltons.strutils import camel2under as c; assert c('NSDecimalToUInt')=='ns_decimal_to_uint', c('NSDecimalToUInt'); assert c('NSDecimalCompact')=='ns_decimal_compact'; assert c('BasicPHPTest')=='basic_php_test'\" && python3 -m pytest -q"},

    {"id": "cacheutils", "task": "backend",
     "owns": ["boltons/cacheutils.py", "tests/test_cacheutils.py"],
     "prompt": "Implement boltons issue #124 in boltons/cacheutils.py: LRU currently stores whatever on_miss returns, including None. Add a keyword argument cache_none=True to LRU.__init__ (and to LRI if it shares the code path). When cache_none is False, a None returned by on_miss is returned to the caller but NOT stored in the cache; the next access calls on_miss again. Default True preserves current behaviour exactly. Document the parameter in the class docstring in the same style as the existing ones. Add tests for both settings to tests/test_cacheutils.py. Edit only boltons/cacheutils.py and tests/test_cacheutils.py. Run python3 -m pytest -q before finishing; all tests must pass.",
     "check": "python3 -c \"from boltons.cacheutils import LRU; n=[0]\ndef m(k): n[0]+=1; return None\nc=LRU(max_size=5,on_miss=m,cache_none=False); c['a']; c['a']; assert n[0]==2 and 'a' not in c, (n, dict(c)); d=LRU(max_size=5,on_miss=m); d['a']; d['a']; assert n[0]==3 and d['a'] is None\" && python3 -m pytest -q"},

    {"id": "review", "task": "review", "needs": ["urlutils", "strutils", "cacheutils"], "owns": ["."],
     "prompt": "Three bug fixes were just committed on this branch (git log -3). Review each diff for correctness, edge cases, and consistency with the surrounding code style. Fix real bugs only; do not refactor, rename, or reformat. Keep all tests passing (python3 -m pytest -q).",
     "check": "python3 -m pytest -q"}
  ]
}
```

- [ ] **Step 3: Validate and dry-run**

```bash
python3 orch.py boltons.json --dry-run
```
Expected: `wave 1: urlutils, strutils, cacheutils` with routes starting with a free agent, then `wave 2: review` with `route=['claude']`. Any ownership error means a typo in `owns`.

- [ ] **Step 4: Check each gate fails on the unfixed tree** (a gate that passes before the fix is worthless)

```bash
cd ~/Projects/orch-target-boltons
for id in urlutils strutils cacheutils; do
  python3 -c "import json; print(json.load(open('../harness-orch/boltons.json'))['steps'])" >/dev/null
  sh -c "$(python3 -c "import json; s=[x for x in json.load(open('../harness-orch/boltons.json'))['steps'] if x['id']=='$id'][0]; print(s['check'])")" >/dev/null 2>&1 && echo "$id: gate PASSES on unfixed tree — BAD" || echo "$id: gate fails as expected"
done
```
Expected: three lines ending `gate fails as expected`.

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/harness-orch && git add boltons.json && git commit -m "boltons proof pipeline"
```

---

### Task 6: Run and measure

**Files:**
- Create: `docs/superpowers/results/2026-08-25-boltons-proof.md`

- [ ] **Step 1: Run with the panel up**

```bash
rm -f .orch-stats.json
(nohup python3 panel.py >/dev/null 2>&1 &)
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$HOME/.opencode/bin:$HOME/.antigravity/bin:$PATH"
python3 orch.py boltons.json 2>&1 | tee .orch-runs/run.log
```
Watch http://127.0.0.1:8765. Expected: wave 1 runs three agents at once; each step ends `PASS` or escalates; wave 2 `review` on claude.

- [ ] **Step 2: Collect evidence**

```bash
python3 - <<'PY'
import json
st = json.load(open('.orch-state.json')); sc = json.load(open('.orch-stats.json'))
for k, v in st['steps'].items():
    print(f"{k:<11} {v.get('status'):<9} agent={v.get('agent')} secs={v.get('secs')} oob={v.get('out_of_bounds')}")
print("budget left", st['budget']); print(json.dumps(sc, indent=1))
PY
cd ~/Projects/orch-target-boltons && git log --oneline -5 && python3 -m pytest -q | tail -1 && git diff HEAD~4 --stat
```

- [ ] **Step 3: Read the three diffs and judge them**

For each of `.orch-runs/{urlutils,strutils,cacheutils}.diff`: does it fix the issue as specified, does it change anything else, would the maintainer merge it (tests added, style matches, no stray files)? Write the honest verdict.

- [ ] **Step 4: Write the results doc**

`docs/superpowers/results/2026-08-25-boltons-proof.md` with: the per-step table from Step 2, budget spent, out-of-bounds warnings, whether `review`'s gate passed on the merged tree, the three verdicts, and one paragraph: what the run says about each success criterion (free tiers carry most steps / no conflicts / mergeable quality) — pass, fail, or inconclusive, with the number that decided it.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/results
git commit -m "results: boltons real-project proof"
```
