# Goline — Roadmap

The staged plan for turning the Godot Engine fork into **Goline**, an
AI-assisted game development engine.

> **Status:** Stage 0 is complete. All later stages are planned but
> **NOT IMPLEMENTED**. No upstream Godot code is modified before a stage
> explicitly authorizes it.

---

## Stage 0 — Godot repository foundation ✅

*Implemented.* Clone the official Godot Engine repository, verify it, and
establish the Goline foundation (charter, roadmap, architecture, AI rules).
No engine code is modified; the foundation is documentation only.

## Stage 1 — Goline identity and branding ✅

*Implemented.* Re-branded the engine's visible identity to **Goline** across
`version.py` (display `name`), the editor display strings (dock/game-workspace
window titles, About dialog, "About Goline" command + tooltip, version-copy
toast), and the Windows app-id/app-name prefixes. Additive brand assets live in
`goline/branding/` and `goline/assets/` (`goline_branding.hpp`, the G+L
monogram `icon.svg`). `short_name = "godot"` and the docs URL are intentionally
unchanged (keep data dirs and working docs links). Full details and the
intentional exclusions are in `docs/goline/IDENTITY.md`.

## Stage 2 — Goline editor integration — PARTIAL

- **Done (external-CLI model):** the agent-agnostic **CLI orchestration seam** —
  `goline/cli/goline_cli.py` (discovery of installed AI CLIs on `PATH`,
  interactive launch against the repo root, Windows `.ps1`/`.cmd` wrapper
  handling) plus `docs/goline/CLI_INTEGRATION.md` and a pure test suite
  (`goline/cli/tests/`). No upstream Godot code is touched; nothing needs an
  engine build.
- **Deferred:** the in-editor dock/toolbar surface (ARCHITECTURE §2) requires
  compiling an editor build with an EditorPlugin and is deliberately not done
  until a C++ toolchain exists. The CLI layer is exercised from the developer
  shell — the primary model for Goline anyway.

## Stage 3 — AI integration architecture — PARTIAL

- **Done (external-CLI reality):** grounded **context packs** in
  `goline/cli/context.py` — engine pack (`--context engine`: repo root, git
  state, tools, key dirs, `version.py` identity) and game pack
  (`--context game --project <dir>`: reads `project.godot`, bounded file list).
  `goline_cli.py` gained `--context`/`--print-context`; a full offline test
  suite guards `goline/cli/` (`test_context.py` + `test_goline_cli.py`, 22
  tests). This implements the ARCHITECTURE §5 "Project Context System" for the
  CLI path.
- **Done (permission/audit model, ARCHITECTURE §8/§9):** the safety boundary is
  `goline/cli/policy.py` and both context packs are truthful about repo state —
  e.g. `git_clean` reports `unknown` (not `yes`) when git is unavailable. Packs
  are grounded and scam-free: depth/count-bounded scans, no filesystem writes,
  every git/tool probe fails closed.

## Stage 4 — OpenCode integration — PARTIAL

- **Done:** a **ProviderDriver SPI** and normalized event vocabulary in
  `goline/cli/providers.py`, with concrete **OpenCode** (`opencode run
  --format json`, grounded via `--file`) and **Claude** (`claude -p`) drivers,
  plus a **`handover`** command (`--handover`) that resolves the workspace
  root, assembles a context pack, and dispatches the first prompt while
  streaming   normalized events — a lightweight port of T3 Code's provider model
  and `t3code handover`. `opencode` CLI `1.18.27` installed; discovered as a
  `file-edit` agent. **Live-validated end-to-end**: a real handover with a
  grounded engine-context pack was dispatched to a free model
  (`opencode/nemotron-3.5-lightning-free`) and returned the correct verdict
  ("Goline") with normalized events (`step.start`/`content.delta`/`done`) and
  exit 0. No provider login was required for the free tier.
- **Done:** an **audit/gate hook on handover** — `--audit <file>` classifies any
  command-bearing events the agent emits through `goline.cli.policy.Policy()`,
  surfaces the ALLOW/DENY verdict in the transcript, and records it to the
  JSONL audit log (pure, append-only). Verified live: `--handover --audit`
  runs clean and writes the log.
- **Done:** the gate policy is **embedded into grounded context packs** as a
  MANDATORY "do not run" section (derived from `policy.py`), so agents *hold*
  the deny rules and self-deny (verified in `--print-context engine|game`).
- **Done:** a **live `--context game` handover** against
  `goline/examples/sample_game/` was validated end-to-end — the free-tier
  model read the actual game files from the context pack and returned the
  correct answers (`player.gd extends CharacterBody2D`; Player node at
  `Vector2(576, 324)`),   streaming `tool_use` + `content.delta` + `done`.
- **Done: `--guard` fail-fast on handover** — if the agent ever emits a command
  the policy denies, the run **aborts with exit code 2** (distinct from the
  provider's exit 1) instead of just auditing/annotating it. Offline-tested.
- **Still ahead:** richer event surface (session/thread persistence).

## Stage 5 — AI-assisted coding — NOT IMPLEMENTED

- Add AI-assisted code/script generation assistance.
- Generate engine/editor code with human review, following the AI rules.

## Stage 6 — AI-assisted debugging — NOT IMPLEMENTED

- Add AI-assisted debugging support.
- Help diagnose build failures, runtime errors, and regressions with AI
  assistance while preserving test integrity.

## Stage 7 — Project/context awareness — NOT IMPLEMENTED

- Give AI agents awareness of the project, files, and surrounding context.
- Provide accurate, scoped context to improve AI assistance quality.

## Stage 8 — Agent tools and permissions — DONE

- **Done (permission/audit gate, ARCHITECTURE §8/§9):** `goline/cli/policy.py` —
  pure command **classification** (default deny-destructive) and an
  **append-only JSONL audit log**. The deny set covers file/filesystem
  destruction, low-level storage, system/power mutation, force process-kill,
  dangerous `git` (incl. force-push in every form), supply-chain pipelines
  (`curl|… | sh`), shell redirection to a file, and **destructive one-liners
  from otherwise allow-listed interpreters** (`python -c` / `node -e` with
  `shutil.rmtree`, `fs.rmSync`, `os.system`, `pip uninstall`, ...), while
  harmless `2>&1` descriptor redirects and normal script runs stay allowed.
  Executables are tokenised robustly (quoted names with spaces, absolute
  paths normalized to basename). Exposed as `--gate "command"` (never
  executes) with optional `--audit <path>`, and wired as a post-dispatch
  audit hook (`--audit`) plus a **fail-fast `--guard`** (exit 2) on
  `--handover`. Offline-tested (107 tests).
- **Done (review/approval UX):** the three-tier verdict model — `ask` joins
  `allow`/`deny` for mutating-but-recoverable commands (non-destructive `git`
  mutations: `add`/`commit`/`push`/`stash`/`restore`, `branch`/`tag` with a
  name; `pip`/`python -m pip`/`npm`/`pnpm`/`yarn`/`brew install`). `--gate`
  returns 2 on ask; `--guard` aborts (exit 2) on deny before any prompt; and
  `--review` (with optional `--approval-file` pre-seed) prompts the human per
  non-allowed verdict, `block` aborting with exit 2. Human verdicts append to
  the same JSONL trail as `"decided_by": "human"` via `ApprovalLog`, and
  `--review <audit.jsonl>` replays a recorded trail offline without
  re-prompting existing human decisions. Together these close the loop: the
  machine classifies, refuses hard denials, and asks a human before any
  agent-run state change is accepted.

## Stage 9 — Testing, performance and polish — NOT IMPLEMENTED

- Harden testing and reliability.
- Measure and optimize Goline workflows; polish usability and readiness.
