#!/usr/bin/env python3
"""Audit report for bench runs: who was called when, with which model, what they edited, who reviewed.

    python3 report.py > docs/superpowers/results/bench-audit.md
"""
import json, pathlib, re, sys

BENCH = pathlib.Path(".orch-runs/bench")


def files_in(diff_path):
    if not diff_path.exists():
        return []
    return re.findall(r"^diff --git a/(\S+)", diff_path.read_text(), re.M)


def run_report(d):
    summ = json.loads((d / "summary.json").read_text()) if (d / "summary.json").exists() else None
    state = json.loads((d / "state.json").read_text())
    out = [f"## {d.name}", ""]
    if not summ:
        out += [f"**CRASHED** — status `{state['status']}`. Last log lines:", "", "```",
                *state["log"][-6:], "```", ""]
        return out
    out += [f"mode **{summ['mode']}** · wall **{summ['wall']}s** · budget spent **{20 - summ['budget_left']}**", ""]
    out += ["### Calls", "", "| step | agent | model | attempts | secs | tokens | result |", "|---|---|---|---|---|---|---|"]
    for sid, s in summ["steps"].items():
        st = state["steps"][sid]
        out.append(f"| {sid} | {s['agent']} | {s.get('model') or '?'} | {s['attempts']} | {s['secs']} | {s['tokens'] or '?'} | {st['status']} |")
    out += ["", "### Files edited (from each step's diff)", ""]
    for sid in summ["steps"]:
        fs = files_in(d / f"{sid}.diff")
        oob = summ["steps"][sid]["out_of_bounds"] or []
        mark = lambda f: f"`{f}`" + (" **OUT OF BOUNDS (discarded)**" if f in oob else "")
        out.append(f"- **{sid}** ({summ['steps'][sid]['agent']}): " + (", ".join(mark(f) for f in fs) or "_nothing_"))
    rv = files_in(d / "review.diff")
    out += ["", "### Review", ""]
    out.append(f"Reviewer `{summ['steps'].get('review', {}).get('agent')}` changed: " +
               (", ".join(f"`{f}`" for f in rv) if rv else "_nothing — approved as-is_"))
    out += ["", "### Timeline", "", "```", *state["log"], "```", ""]
    return out


def main():
    dirs = sorted(p for p in BENCH.iterdir() if p.is_dir())
    print("# Bench audit\n")
    print(f"{len(dirs)} runs. Every agent call, in order, with what it touched.\n")
    for d in dirs:
        print("\n".join(run_report(d)))


if __name__ == "__main__":
    main()
