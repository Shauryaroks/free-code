# Real-project proof on boltons — design

**Goal.** Prove the orchestrator core on a real codebase before building anything on top of it.
Success = (a) free tiers carry most steps, (b) parallel steps never conflict, (c) the output is
something a maintainer would merge.

## Target

Fresh clone of `mahmoud/boltons` (~17k lines, pure Python, 472 pytest tests in 4s, one module
per file with a matching `tests/test_<module>.py`) at `~/Projects/orch-target-boltons`, work on a
branch `orch-proof`. Three open, reproduced bugs in three different modules:

| step | issue | owns | spec given to the agent |
|---|---|---|---|
| `urlutils` | #309 | `boltons/urlutils.py`, `tests/test_urlutils.py` | `URL("http://u:pass?word@h:443")` and `.../pass/word@...` must parse; password preserved verbatim. Add regression tests. |
| `strutils` | #142 | `boltons/strutils.py`, `tests/test_strutils.py` | `camel2under('NSDecimalToUInt') == 'ns_decimal_to_uint'`; existing `camel2under` tests still pass. Add regression test. |
| `cacheutils` | #124 | `boltons/cacheutils.py`, `tests/test_cacheutils.py` | Add `cache_none=True` kwarg to `LRU`; when `False`, a `None` returned by `on_miss` is returned to the caller but not stored. Default preserves current behaviour. Add tests for both settings. |
| `review` | — | `.` (needs all three) | Review the three diffs for correctness and style consistent with the surrounding code; fix bugs only, no refactors. |

Task type for the three fixes is `backend` (route: codex → opencode → copilot → claude); `review` is claude-only.

## Gates

Each fix step's `check` has two parts, both deterministic, joined with `&&`:

1. **Repro must now pass** — a `python3 -c` one-liner asserting the exact behaviour above.
2. **Full suite green** — `python3 -m pytest -q`.

`review`'s gate is the full suite on the merged tree. That gate is what catches a fix that
depended on an out-of-bounds edit which merge-back discarded.

## Core changes required (all forced by this target)

1. **File-level `owns`.** boltons is one file per module. `overlaps()` already works on path
   prefixes; `merge_back` must handle files (`rmtree` on a file is a no-op today, so deletions
   of owned *files* would not propagate — use `os.remove`/`rmtree` by type).
2. **Out-of-bounds report.** Before merge-back, compute `git diff --name-only <base>..orch/<id>`
   in the worktree. Files not under any owned path are recorded in state as
   `steps[id].out_of_bounds` and logged. They are still discarded — that is the contract — but
   never silently.
3. **Change log.** Save each step's full diff to `.orch-runs/<step>.diff` (gitignored) before
   merge-back. The panel shows it per step, with out-of-bounds files listed separately.
4. **Per-agent timeout.** Cost-0 agents time out at 600s; paid at 1800s. A throttled free tier
   should cost ten minutes, not thirty, before the router learns.
5. **`--dry-run`.** Print waves, owners, and route order per step; spawn nothing; exit.

Tests added to `test_orch.py`: file-level overlap rejected / disjoint files accepted; merge_back
copies an owned file and drops an unowned one; out-of-bounds list is computed correctly;
dry-run exits before any agent call.

## Measurement

After the run, report:

- per step: which agent passed, attempts, seconds, out-of-bounds files;
- budget spent vs. 20; `.orch-stats.json` deltas;
- whether `review` passed on the merged tree (this is the "never conflict" evidence);
- for each diff: my read of whether the maintainer would merge it, with the specific reasons
  if not. Honest verdict; a green gate is not the same as mergeable.

## Out of scope

n8n-style editor, presets, long-lived server, LLM-generated pipelines, retry button. All noted as
next steps in the README discussion; none needed to run this proof.
