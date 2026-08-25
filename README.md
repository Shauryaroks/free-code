# free-code

Run coding-agent CLIs **in parallel** across git worktrees, routing each step to the
**cheapest agent that passes its test gate**.

Claude Code, Codex, Antigravity (`agy`) and OpenCode work side by side on one repo.
Free tiers go first; the paid tier only sees work the free tiers couldn't finish.
~300 lines of Python, stdlib only.

## How it works

- **Waves** — steps declare `needs`; everything whose deps are met runs at once, each in its own `git worktree`.
- **Gates** — a step passes when its `check` command exits 0 (`npm test`, a build, a lint). No LLM judge: an agent can't declare itself done.
- **Escalation** — gate fails → next agent in the route gets the same prompt plus the failure output.
- **Cost-per-pass routing** — every agent's pass rate per task type is learned in `.orch-stats.json`. Expected spend to get one pass = `(cost+1) / pass_rate`; lowest wins. A free agent stays first until paying up front is genuinely cheaper.
- **Conflicts are prevented, not merged** — every step declares the dirs it `owns`; steps that can run concurrently must own disjoint paths (validated before anything runs). Merge-back is a path-scoped `git checkout`, so git never runs a merge algorithm.

## Run

```
python3 panel.py &            # local control panel, http://127.0.0.1:8765
python3 orch.py pipeline.json
python3 -m pytest test_orch.py -q
```

The target repo needs at least one commit. Agents run with permissions skipped inside their worktree — use a repo you're fine losing.

## Pipeline

```json
{
  "repo": "../my-app",
  "budget": 20,
  "steps": [
    {"id": "contract", "task": "backend",  "owns": ["contract"],
     "prompt": "Write contract/openapi.yaml for a todos API.",
     "check": "npx @redocly/cli lint contract/openapi.yaml"},
    {"id": "backend",  "task": "backend",  "needs": ["contract"], "owns": ["src/api"],
     "prompt": "Implement src/api against contract/openapi.yaml.", "check": "npm test -- api"},
    {"id": "frontend", "task": "frontend", "needs": ["contract"], "owns": ["src/ui"],
     "prompt": "Build the UI in src/ui against the contract.",   "check": "npm run build"},
    {"id": "qa",       "task": "qa",       "needs": ["backend", "frontend"], "owns": ["tests/e2e"],
     "prompt": "Write and run e2e tests.",                        "check": "npm run test:e2e"},
    {"id": "review",   "task": "review",   "needs": ["qa"], "owns": ["."],
     "prompt": "Fix correctness and security bugs only."}
  ]
}
```

`backend` and `frontend` run in parallel. `review` may own `.` because it's ordered after everything.

## Agents

| slot | CLI | tier |
|---|---|---|
| `agy` | Antigravity CLI (replaced Gemini CLI for free users, 2026-06-18) | free |
| `codex` | Codex CLI, ChatGPT Free sign-in | free |
| `opencode` | OpenCode on Zen free models | free |
| `copilot` | Copilot CLI | free (50 premium req/mo) |
| `claude` | Claude Code | paid |

Edit `AGENTS` and `ROUTES` in `orch.py` to add or reorder. `{cwd}` in a command is replaced with the worktree path — some CLIs ignore the process cwd.

## Control panel

`panel.py` serves one page that polls `.orch-state.json`: waves, per-step status and agent output, the cost-per-pass scoreboard, a log. One button: **stop after wave** (touches `.orch-stop`). Binds `127.0.0.1` only.

## Non-goals

Hosted compute, audit trails, a control plane. Nothing routes through anyone: the CLIs run on your machine with your own logins.
