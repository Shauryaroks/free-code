# harness-orch

Orchestrator that runs agent CLIs **in parallel** across isolated git worktrees,
routing each step to the cheapest agent that passes its gate.

Waves are derived from `needs`. Conflicts are prevented, not merged: every step
declares the dirs it `owns`, concurrent steps must own disjoint paths, and
merge-back is a path-scoped `git checkout` so no merge algorithm ever runs.
`orch.py` (runner) · `panel.py` (local control panel) · `pipeline.json` (steps) · `test_orch.py` (self-check)
`.orch-stats.json` (learned pass rates) · `.orch-state.json` (live run state) · `.orch-stop` (touch to halt).

## Rules

- Git is allowed. Co-author trailers on commits are fine. Commit only when asked. GitHub account is **Shauryaroks** (never shaurya757).

## Running

    python3 panel.py &            # http://127.0.0.1:8765, stdlib only
    python3 orch.py pipeline.json
    python3 -m pytest test_orch.py -q

## Notes

- The target repo needs at least one commit before `git worktree add` will work.
- A step owning `.` (e.g. `review`) is legal only if it is ordered after everything — validation enforces this.
- Gemini CLI is dead for free users (2026-06-18). Antigravity CLI `agy` replaced it: `curl -fsSL https://antigravity.google/cli/install.sh | bash`. Free-tier CLIs in `AGENTS`: agy, codex, copilot, opencode, crush.
- Control panel is read-only over `.orch-state.json` plus one POST `/stop` that touches `.orch-stop`; orchestrator halts between waves. Binds 127.0.0.1 only.
- MCP servers are configured per-agent in that agent's own config, not here.
