#!/usr/bin/env python3
"""Harness matrix: run every root step of a pipeline with EVERY available agent, in isolation.
No merge-back. Answers "which harness is best at which kind of work".

    python3 matrix.py boltons.json            # results -> .orch-runs/matrix/<step>.<agent>.json
    python3 matrix.py --table                 # print the table from saved results
"""
import json, pathlib, subprocess, sys, time
import orch

OUT = pathlib.Path(".orch-runs/matrix")


def run_cell(repo, step, agent):
    wt, branch, base = orch.worktree(repo, {"id": f"mx-{step['id']}-{agent}"})
    t = time.time()
    try:
        ok, out, secs = orch.run_agent(agent, step["prompt"], wt)
    except subprocess.TimeoutExpired:
        ok, out, secs = False, "TIMEOUT", time.time() - t
    subprocess.run(["git", "add", "-A"], cwd=wt); subprocess.run(["git", "commit", "-qm", "mx"], cwd=wt)
    passed, why = orch.gate(step, wt) if ok else (False, out[-2000:])
    files = orch.changed_files(wt, base)
    cell = {"step": step["id"], "task": step["task"], "agent": agent,
            "model": orch.AGENTS[agent].get("model") or orch.parse_model(agent, out) or "default",
            "pass": passed, "secs": round(secs), "tokens": orch.parse_tokens(agent, out),
            "files": files, "out_of_bounds": orch.out_of_bounds(step, files), "tail": why[-800:]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{step['id']}.{agent}.json").write_text(json.dumps(cell, indent=1))
    (OUT / f"{step['id']}.{agent}.diff").write_text(
        subprocess.run(["git", "diff", f"{base}..HEAD"], cwd=wt, capture_output=True, text=True).stdout)
    orch.cleanup(repo, {"id": f"mx-{step['id']}-{agent}"}, branch)
    orch.record(step["task"], agent, passed)      # feeds the router too
    return cell


def table():
    cells = [json.loads(p.read_text()) for p in sorted(OUT.glob("*.json"))]
    agents = sorted({c["agent"] for c in cells}); steps = sorted({c["step"] for c in cells})
    print(f"{'step':<12}" + "".join(f"{a:>22}" for a in agents))
    for s in steps:
        row = f"{s:<12}"
        for a in agents:
            c = next((c for c in cells if c["step"] == s and c["agent"] == a), None)
            row += f"{'-':>22}" if not c else f"{('PASS' if c['pass'] else 'fail') + f' {c['secs']}s ' + (str(c['tokens'] or '?') + 'tk'):>22}"
        print(row)
    print("\nper agent: pass rate, median secs, model")
    import statistics
    for a in agents:
        cs = [c for c in cells if c["agent"] == a]
        print(f"  {a:<10} {sum(c['pass'] for c in cs)}/{len(cs)}  {statistics.median(c['secs'] for c in cs):.0f}s  {cs[0]['model']}")


def main():
    if "--table" in sys.argv:
        return table()
    pipe = json.loads(pathlib.Path(sys.argv[1]).read_text())
    repo = pipe["repo"]
    roots = [s for s in pipe["steps"] if not s.get("needs")]      # ponytail: only steps runnable from base
    agents = [a for a in orch.AGENTS if orch.available(a)]
    print(f"{len(roots)} steps x {len(agents)} agents = {len(roots) * len(agents)} cells (serial)")
    for s in roots:
        for a in agents:
            print(f"-> {s['id']} on {a} ...", end=" ", flush=True)
            c = run_cell(repo, s, a)
            print(f"{'PASS' if c['pass'] else 'FAIL'} {c['secs']}s {c['tokens'] or '?'} tokens  model={c['model']}")
    table()


if __name__ == "__main__":
    main()
