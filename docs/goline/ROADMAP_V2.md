# Goline — Roadmap v0.2 track

> v0.1 is frozen in `docs/goline/ROADMAP.md` (Stages 0–9 DONE, shipped as
> `goline-v0.1` via PR #2). This document plans what comes after. Standing
> rules carry over unchanged: additive/opt-in only, `goline/` +
> `docs/goline/` isolation, hermetic tests, stop-for-review per phase.

## R1 — Ship PR #2 ✅ DONE

- Reviewed (suite 184 OK, headless sample import clean, live
  `--handover` + `--continue` smoke green, 29-file diff audit with zero
  upstream touches), merged as merge commit `f8782fcb20` (PR #1 precedent),
  tag `goline-v0.1` pushed, suite re-verified green on `master`, roadmap
  marked shipped (`e69928d985`).

## R2 — Upstream sync policy ✅ DONE

- Policy in `docs/goline/SYNC.md` (remotes, monthly/release cadence,
  merge-never-rebase, conflict playbook, protected touch-list, rollback).
- Validated, not just written: trial branch merged 96 upstream commits with
  zero conflicts, suite 184 OK on the merged tree, headless import clean.
  Committed as `6d86a60b9a`.

## R3 — Engine build track ⏸ DEFERRED (hardware)

- Goal: a real compiled Goline editor, unlocking the optional C++-embedded
  EditorPlugin variant + compiled-in branding.
- Blocked on this machine: needs VS Build Tools + SCons (~15 GB scratch,
  3–6 h compile, 8 GB RAM / ~1.5 GB free is the binding constraint).
- The addon route already covers stock binaries, so nothing else waits on
  this. Revisit when hardware allows; R4/R5 proceed independently.

## R4 — Product hardening (CI + release hygiene) — OPEN

- Goal: every future change checked by machines; releases routine.
- Scope: GitHub Actions — (1) `python -m unittest discover -s
  goline/cli/tests`, (2) headless `--editor --import` + `--quit-after`
  smoke of `sample_game`; release-notes convention per merge; `goline-vX.Y`
  tag scheme; sample-game growth (second scene exercising
  explain/edit/debug).
- Exit criteria: Actions green on `master` and on PRs; release checklist doc
  exists.

## R5 — Next AI capabilities (backlog, ordered) — OPEN

- Goal: deeper assistance on the proven foundation. Candidates in order:
  1. **In-editor session picker** — surface existing `--continue` in the
     dock (list `.goline/session.json`, resume/new).
  2. **Multi-file edit plans** — bounded plan-then-diff flow under the same
     policy gate.
  3. **In-editor review UI** — ASK/DENY approvals inside the dock instead of
     the terminal.
  4. **More providers** — drivers behind the same SPI (only OpenCode +
     Claude exist today).
- Exit criteria (per item): SPI-conformant driver/workflow + hermetic tests
  + docs note; item marked done here.

## Phase ordering

R1 → R2 done. R4 is the next candidate; R5 items are unblocked — preferred
order is R4 first so CI guards new features, each item its own changeset +
review. R3 whenever hardware allows (independent of all else).
