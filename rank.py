#!/usr/bin/env python3
"""Rank bench runs: python3 rank.py  (reads .orch-runs/bench/*/summary.json + state.json)"""
import json, pathlib, statistics as st

rows = []
for d in sorted(pathlib.Path(".orch-runs/bench").glob("*")):
    if not (d / "summary.json").exists():
        rows.append({"run": d.name, "mode": d.name.split("-", 1)[1], "wall": None, "status": "CRASH"}); continue
    s = json.loads((d / "summary.json").read_text()); state = json.loads((d / "state.json").read_text())
    steps = s["steps"]
    rows.append({
        "run": d.name, "mode": s["mode"], "wall": s["wall"], "status": state["status"],
        "step_secs": sum(v["secs"] or 0 for v in steps.values()),
        "tokens": sum(v["tokens"] or 0 for v in steps.values()),
        "attempts": sum(v["attempts"] or 0 for v in steps.values()),
        "escalations": sum((v["attempts"] or 1) - 1 for v in steps.values()),
        "oob": sum(len(v["out_of_bounds"] or []) for v in steps.values()),
        "spent": 20 - s["budget_left"],
        "agents": ",".join(f"{k}:{v['agent']}" for k, v in steps.items()),
    })

hdr = f"{'run':<12}{'status':<8}{'wall':>6}{'Σstep':>7}{'tokens':>9}{'tries':>6}{'esc':>5}{'oob':>5}{'$':>3}  agents"
print(hdr); print("-" * len(hdr))
for r in rows:
    if r["wall"] is None: print(f"{r['run']:<12}{r['status']:<8}"); continue
    print(f"{r['run']:<12}{r['status']:<8}{r['wall']:>6}{r['step_secs']:>7}{r['tokens']:>9}{r['attempts']:>6}{r['escalations']:>5}{r['oob']:>5}{r['spent']:>3}  {r['agents']}")

ok = [r for r in rows if r["wall"] is not None]
for mode in ("parallel", "serial"):
    m = [r for r in ok if r["mode"] == mode]
    if m:
        print(f"\n{mode:<9} n={len(m)} wall median={st.median(r['wall'] for r in m):.0f}s  "
              f"tokens median={st.median(r['tokens'] for r in m):.0f}  attempts={sum(r['attempts'] for r in m)}")
p = [r["wall"] for r in ok if r["mode"] == "parallel"]; q = [r["wall"] for r in ok if r["mode"] == "serial"]
if p and q:
    print(f"\nspeedup (serial/parallel, medians): {st.median(q)/st.median(p):.2f}x")
