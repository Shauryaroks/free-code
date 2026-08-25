# Boltons real-project proof — results

Spec: `docs/superpowers/specs/2026-08-25-boltons-proof-design.md`. Two full runs on a fresh clone of
`mahmoud/boltons` (472 tests). Three parallel bug-fix steps (issues #309, #142, #124), then `review`.

## Per-step results

| step | run 1 | run 2 | out-of-bounds |
|---|---|---|---|
| urlutils   | codex PASS 124s | codex PASS 79s  | none |
| strutils   | codex PASS 145s | codex PASS 112s | none |
| cacheutils | codex PASS 75s  | codex PASS 83s  | none |
| review     | claude PASS 84s | claude PASS 97s | none (run 1 false-positive, fixed) |

- Budget: 20 → **17** both runs. The only paid call was `review`. Every fix landed on a free tier, first attempt.
- Stats after both runs: `backend/codex 6/0`, `review/claude 2/0`. Escalation never triggered.
- Wall clock: ~4 min per run; wave 1 bounded by the slowest agent (145s), not the sum (344s).
- Run 2 final tree: 4 plain commits, **482 tests passing** (10 new), worktrees and branches cleaned up.

## Run 1 crash (orchestrator bug, fixed)

`review` owns `.`; `merge_back` ran `remove_path(repo/".")` and deleted the target repo. Root cause: `"."`
was never normalised, so it also failed to overlap anything in `validate_ownership` and produced a
false out-of-bounds warning on every file. Fixed in `5f869cd` with three tests. The smoke pipeline
had no root owner, so this only surfaced on the real target. All run-1 evidence survived in `.orch-runs/run1/`.

## Diff verdicts (would a maintainer merge it?)

**cacheutils (#124) — yes.** Minimal: `cache_none=True` kwarg on `LRI`/`LRU`, both docstrings updated,
parametrised tests over both classes and both settings. Nothing to change.

**strutils (#142) — yes, with a nit.** Correct on every case tried (`NSDecimalToUInt`, `ATest`,
`getHTTPResponseCode`, `A`). Run 1's regex broke `ATest`; `review` caught and fixed it. Run 2's regex was
simpler and needed no fix. Both runs also touched an unrelated line (added a trailing newline in
`ellipsize`) — in-bounds but out of scope; a maintainer would ask for it dropped.

**urlutils (#309) — no.** Both runs changed the authority regex; both times codex's version made
`http://host:80/p@q` parse `80/p` as a password; both times `review` caught it and added a
"digits followed by `/`" lookahead. That lookahead reintroduces the bug for any password that starts
with digits and contains `/`:

    URL('http://u:123/abc@h:443')  ->  host='u', port=123, password=''      (both runs)

All 128 urlutils tests pass either way. The honest upstream answer is probably "percent-encode the
password (RFC 3986)", not a regex change. Neither the gate nor a paid reviewer can tell you the task
was wrong.

## Success criteria

| criterion | verdict | deciding number |
|---|---|---|
| free tiers carry most steps | **pass** | 6/6 fixes on codex; 3 of 20 budget spent, all on review |
| parallel steps never conflict | **pass** | 0 out-of-bounds writes across 6 steps; merged tree green both runs; review gate passed on the merged tree |
| output is mergeable quality | **2 of 3** | cacheutils and strutils mergeable; urlutils has a latent bug two green gates and two reviews missed |

## What the run says about the design

1. The cost thesis holds on this target: the free tier did all the implementation work. But codex never
   failed, so the router's learning and escalation paths got **zero** exercise. Untested, not proven.
2. Gates are only as good as their author. `cache_none` had a precise, mechanical spec and came back
   perfect; `urlutils` had an ambiguous spec and came back subtly wrong twice. Prompt precision, not
   agent choice, was the dominant variable.
3. The `review` step earned its cost: it found a real regression in both runs. It also has the same blind
   spots as the workers — a reviewer is not a proof.
4. `.`-ownership was a latent catastrophe. Anything that computes a path from user input needs a
   root-owner test from day one.

## Not done

- Escalation, the stats-based re-ranking, and per-tier timeouts have never fired on a real run.
  Next proof should pick a task free tiers actually fail.
- Concurrent burst against one free tier (3× codex at once) did not rate-limit here; it may elsewhere.
