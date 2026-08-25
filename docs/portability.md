# Running free-code on other platforms

Short version: **macOS and Linux work today. Windows works with two one-line fixes. Desktop-only
agents (Antigravity IDE, Cursor, Windsurf, Claude Desktop) cannot be orchestrated at all** — there
is nothing to call — but a "manual" step type can put a human at that seat.

## What the orchestrator actually needs

It is ~350 lines of stdlib Python that does four things: `git worktree`, spawn a CLI as a
subprocess, run a shell command as a gate, and serve one HTML page on localhost. Every one of those
exists on every OS. The portability question is entirely about the *agents*, not the runner.

### OS-specific bits in the code (audited 2026-08-25)

| line | what | Linux/macOS | Windows |
|---|---|---|---|
| `orch.py:87` `which` | agent availability | ok | **fails** — no `which`. Fix: `shutil.which(cmd)` (stdlib, all OS). One line. |
| `orch.py:142` `shell=True` | runs each step's `check` | `/bin/sh` | `cmd.exe`. Works, but the *check strings you write* are shell-specific: `test -f x && ...` is sh, not cmd. Write checks as `python3 -c ...` or `python3 -m pytest` and they run everywhere. |
| `orch.py:100` `PWD` env | keeps node CLIs in the worktree | needed | harmless (Windows uses `cd`); `--dir`/`-C`/`--add-dir` flags do the real work |
| worktree path `../.orch-wt-<id>` | sibling dir of the target | ok | ok (`pathlib`) |
| `panel.py` `127.0.0.1:8765` | control panel | ok | ok |
| `bench.sh` | benchmark loop | bash | not portable; rewrite in Python if Windows users need it |

Python ≥ 3.8 (`Path.unlink(missing_ok=True)`), git ≥ 2.5 (worktrees). No other dependencies.

## Agent availability by platform

| agent | tier | Linux | macOS | Windows | headless flag | notes |
|---|---|---|---|---|---|---|
| Claude Code | paid | ✓ | ✓ | ✓ | `-p` | native Windows since 2025 |
| Codex CLI | free (ChatGPT Free) | ✓ | ✓ | ✓ | `exec` | Windows native; sandbox is weaker there — we run `workspace-write` anyway |
| Antigravity CLI `agy` | free | ✓ | ✓ | **WSL only** as of Aug 2026 | `-p` | installer is a bash script |
| OpenCode | free (Zen) | ✓ | ✓ | ✓ | `run` | single Go/Bun binary |
| Copilot CLI | free (50 premium/mo) | ✓ | ✓ | ✓ | `-p` | |
| Crush | BYOK | ✓ | ✓ | ✓ | `run` | |

**On Windows, the practical route is WSL2**: every agent above works there, the repo is a normal
Linux path, and the panel is reachable from a Windows browser at `localhost:8765`. Native Windows
loses `agy` and needs the two fixes above.

## Desktop-only agents

Antigravity IDE, Cursor, Windsurf, Claude Desktop, Copilot in VS Code: these are **clients**. They
have no command that says "do this task, write the files, exit 0". The orchestrator cannot call them,
and there is no protocol trick around it — MCP goes the other way (the desktop app is the MCP client;
it calls servers, it doesn't accept tasks from one).

Three honest options:

1. **Use the vendor's CLI instead.** Most desktop products now ship one: Antigravity → `agy`,
   Cursor → `cursor-agent`, Copilot → `copilot`, Claude Desktop → `claude`. Same subscription, same
   models, usually the same quota. This is the answer for 90% of people.
2. **A `manual` agent.** A step routed to `manual` writes `.orch-task-<id>.md` (the prompt, the owned
   paths, the worktree location) and blocks until `.orch-done-<id>` appears. The human opens the
   worktree in whatever desktop app they like, does the work, touches the done file; the gate runs
   as usual. Cost is whatever you say it is; pass rate gets learned like any other agent. ~20 lines.
   This is also how a *senior reviewer* fits into a pipeline without being a model.
3. **Don't.** If the agent can't be scripted, it can't be a pipeline step. Say so rather than fake it.

What does *not* work: driving a desktop app via UI automation. It breaks on every update, can't run
three in parallel, and you can't tell when it's finished. Not worth building.

## Packaging

The runner is one file with no deps, so distribution is trivial:

- `pipx install free-code` (add a `pyproject.toml` with `orch:main` and `panel:main` entry points),
- or literally `curl -O orch.py`.

Don't bundle agents. Each one has its own installer, login, and terms of service; the tool must never
hold anyone's credentials (that is also what keeps it clear of every vendor's "no third-party
harness" clause — the user runs their own CLI under their own login on their own machine).

## Limits that don't go away with porting

- **Free tiers are per-person.** Nothing here can pool or share them; that's a ToS violation and not
  a feature.
- **Gates need the project's toolchain on the machine.** A Python repo needs pytest; a JS repo needs
  node. The orchestrator doesn't install anything.
- **One target repo per run**, worktrees as siblings. Network drives and case-insensitive filesystems
  (default macOS) work but ownership paths must match case exactly.
