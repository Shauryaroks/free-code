# Bench + harness matrix — results

Target: `mahmoud/boltons` @ `967864f`, same three fixes (#309 urlutils, #142 strutils, #124 cacheutils)
+ `review`. Audit of every call: `2026-08-25-bench-audit.md`. Raw: `.orch-runs/bench/`, `.orch-runs/matrix/`.

Models actually used (from CLI output): codex = `gpt-5.6-terra`, claude = `claude-fable-5`.
agy and opencode ran their CLI defaults and don't report a name; pin with `"model": ...` in `AGENTS`
(`agy models`, `opencode models` list them) for reproducible runs.

## 1. Parallel vs serial (bench, 6 runs alternating)

| run | mode | wall | attempts | escalations | tokens | claude $ | outcome |
|---|---|---|---|---|---|---|---|
| 01 | parallel | **197s** | 4 | 0 | 114k | 0.98 | clean |
| 02 | serial | 348s | 4 | 0 | 86k | 0.82 | clean |
| 03 | parallel | 524s | 7 | 3 | 489k | 1.52 | clean; codex quota died mid-task, all 3 → opencode |
| 04 | serial | crash | — | — | — | — | opencode timed out on urlutils; orchestrator bug |
| 05 | parallel | crash | 5 | 2 | — | ~0.9 | urlutils opencode→codex(quota)→claude PASS; then timeout crash |
| 06 | serial | crash | — | — | — | — | as 04 |

**Speedup, clean pair: 1.77×** (348/197). Matches Amdahl with `review` (~90s) unparallelizable:
serial ≈ Σsteps + review, parallel ≈ max(step) + review. Adding steps to a wave makes parallel
better; it never approaches 3× while one serial reviewer sits at the end.

Per-step ranking by time is stable across every run and every agent: **cacheutils < strutils < urlutils**.

## 2. Which harness for which work (matrix, 3 steps × 5 agents, serial, from base)

| step | agy (free) | claude (paid) | codex (free) | opencode (free) | crush |
|---|---|---|---|---|---|
| cacheutils | PASS 174s | PASS **34s** 148k tk | quota | PASS 333s | no key |
| strutils | PASS 267s | PASS **35s** 108k tk | quota | PASS 463s | no key |
| urlutils | PASS 402s | PASS **109s** 321k tk | quota | **timeout 600s** | no key |

Codex had exhausted ChatGPT Free's allowance in the bench ("try again Sep 24th") before the matrix
ran; its 12/12 passes from the bench stand. Crush is BYOK and had no key — dropped from routes.

### Quality probes on the passing diffs (edge cases the gate doesn't test)

| step | agy | claude | opencode | shared blind spot |
|---|---|---|---|---|
| cacheutils | ok (120 lines) | ok (41 lines) | ok (71 lines) | none |
| strutils | `ATest`→wrong (17) | `ATest`→wrong (9) | `ATest`→wrong (28) | **all three** miss single-capital prefix |
| urlutils | `host:80/a@b` misparsed (56) | `u:123/abc@h` misparsed (31) | — | each fixes the spec cases, each breaks a different neighbour |

Diff size in parentheses (lines changed). Claude's diffs are consistently the smallest.

## 3. Ranking

**By reliability + speed on this work (gate pass, then median secs):**
1. claude — 3/3, 35s median, ~$0.30–1.00 per step, smallest diffs
2. codex — 12/12 in the bench at ~95s median, $0 — **until the quota dies for a month**
3. agy — 3/3, 267s median, $0. Slow but never failed. Never tried in the bench because it wasn't in the `backend` route (fixed).
4. opencode — 12/14 overall, 333–463s when it passes, cannot finish urlutils in 600s. Its tail shows it browsing "the upstream issue discussion" — spends time researching rather than editing.

**By cost per pass** (what the router optimises): codex > agy > opencode > claude — as long as codex
has quota. The router's learned table after everything: codex 12/0, agy 3/0, opencode 10/2, claude 4/0.
It will try codex first, get an instant quota error, skip it for the run, and fall to agy. Correct.

**By mergeable quality:** nobody wins urlutils; everybody ties on cacheutils; strutils is a shared
blind spot. Quality tracked the **spec precision**, not the harness.

## 4. What changed because of this

- Timeouts now escalate instead of crashing the wave (`1935553`).
- Quota-exhausted agents are skipped for the rest of the run and not scored as skill failures.
- `agy` added to the `backend` route; `crush` removed.
- Model capture (`model` in state/summary), `--model` per agent, per-run `summary.json`, `report.py`
  audit generator, `matrix.py`.

## 5. Open

- opencode default model needs pinning to a faster Zen model, or a longer timeout for it alone.
- Free-tier ceilings are the real constraint: ChatGPT Free lasted ~10 pipelines. A per-agent daily
  budget in stats would let the router *anticipate* exhaustion instead of discovering it.
- Concurrent burst (3× one agent at once) never rate-limited here; still unverified elsewhere.
