# Goline — Upstream Sync Policy

How the fork stays mergeable with `godotengine/godot` indefinitely.
Standing rule: additive, opt-in, backward-compatible — syncs must never
silently diverge Goline from correct upstream behavior.

## Remotes

| Remote   | URL                                | Use                                    |
|----------|------------------------------------|----------------------------------------|
| `fork`   | `https://github.com/Apollo-Light/goline.git` | Our work: push/pull branches, PRs, tags |
| `origin` | `https://github.com/godotengine/godot.git`   | Upstream: **fetch-only, never push**     |

## State record

- Fork base: Godot 4.8-dev (`version.py`: major 4, minor 8, patch 0, status "dev").
- 2026-09-09: `master` is **96 behind / 19 ahead** of `origin/master`.
  All 19 ahead commits are Goline-only (CLI, docs, branding strings,
  sample game). Upstream HEAD: `9552dfb685` (2026-09-08).

## Cadence

Sync **monthly, or on each upstream minor/stable release** — whichever comes
first. Act sooner if the behind-count exceeds ~500 commits. Each sync is one
changeset, reviewed like any other change (stop-for-review applies).

## Method: merge, never rebase

- `git fetch origin master`, then `git merge origin/master` into fork
  `master` with a merge commit.
- Never rebase `master`: history is public (merged PRs #1/#2, pushed tags
  `goline-v0.1`).

## Trial procedure (every sync)

Run the sync on a throwaway branch first — never directly on `master`:

```powershell
git checkout master; git pull fork master
git checkout -b sync/trial-YYYYMMDD
git fetch origin master
git merge origin/master -m "sync: trial merge upstream master <date>"
python -m unittest discover -s goline/cli/tests   # must be green
# headless sample-game import with the stock binary must be clean:
& "<godot-bin>\Godot_v4.7.2-stable_win64.exe" --headless --editor `
  --path "goline\examples\sample_game" --quit-after 5
```

Only if all green: merge the trial into `master` (fast-forward or
`--no-ff`), push `master` to `fork`, delete the trial branch. If anything
is red, stop — fix forward on the trial branch, never force-push `master`.

## Conflict playbook

By construction, conflicts should not happen:

- `goline/`, `docs/goline/`, and the sample project are **fork-exclusive
  paths** — upstream never touches them, so they auto-merge.
- We touch **zero** engine/editor/renderer/scene/build files, so upstream
  engine work auto-merges too.
- If a future stage ever touches an engine file, that touch must be
  re-justified in the sync changeset, kept minimal and clearly marked
  (per project rule 2), and conflicts there resolve in favor of upstream
  unless the Goline behavior is intentional and documented.
- **Doc drift** (our docs referencing upstream files that moved) is not a
  merge conflict — fix it as a follow-up docs commit, same sync.

## Protected touch-list

Commits on this fork may only touch:

- `goline/**` (code, tests, benchmarks, examples, branding)
- `docs/goline/**` (roadmaps, architecture, policies)
- Explicitly marked engine seams, if a roadmap stage authorizes them
  (none exist as of v0.1)

Everything else (`editor/`, `scene/`, `servers/`, `platform/`, build files,
…) is off-limits. The PR diff audit in R1 (29 files, all inside the list
above) is the template for every future review.

## Rollback

- Before pushing `master`: `git reset --hard <pre-merge master SHA>`.
- After pushing: do not reset (history is public) — revert with
  `git revert -m 1 <merge SHA>` and push the revert.

## Trial log

- **2026-09-09 (R2):** trial branch `sync/trial-20260909` merged
  `origin/master` (`9552dfb685`, +96 commits) into v0.1 `master`.
  Merge was clean (no conflicts — see playbook). Suite: **184 OK**.
  Headless sample import: dock loads/unloads, zero errors.
  Trial branch deleted; procedure above validated. No follow-up drift found.
