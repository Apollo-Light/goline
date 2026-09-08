# Goline — Roadmap

The staged plan for turning the Godot Engine fork into **Goline**, an
AI-assisted game development engine.

> **Status: SHIPPED as v0.1** — all Stages 0–9 DONE, merged via PR #2
> (`goline/cli-polish` → `master`, tag `goline-v0.1`). No upstream Godot
> code was modified; all Goline code lives under `goline/`, `docs/goline/`,
> and the sample project.

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

## Stage 2 — Goline editor integration — DONE

- **Done (external-CLI model):** the agent-agnostic **CLI orchestration seam** —
  `goline/cli/goline_cli.py` (discovery of installed AI CLIs on `PATH`,
  interactive launch against the repo root, Windows `.ps1`/`.cmd` wrapper
  handling) plus `docs/goline/CLI_INTEGRATION.md` and a pure test suite
  (`goline/cli/tests/`). No upstream Godot code is touched; nothing needs an
  engine build.
- **Done (in-editor dock on a stock build):**
  `goline/examples/sample_game/addons/goline_ai/` is a GDScript
  `EditorPlugin` that adds a **Goline AI** dock to a *stock* Godot editor
  binary — no C++ toolchain, no engine build. It drives `goline_cli` from a
  background `OS.execute` thread (Explain file / Edit file / Debug error, CLI
  auto-detect walking up from `res://`, provider selector, output copy).
  Loaded in the sample project via `[editor_plugins]
  enabled=PackedStringArray("goline_ai")` and verified under stock Godot 4.7.2
  (headless load/unload clean; live `--explain` through the dock's exact argv
  returns the AI summary). The engine-embedded C++ EditorPlugin variant
  (ARCHITECTURE §2) remains an optional follow-up once a C++ toolchain exists —
  the addon route needs none.

## Stage 3 — AI integration architecture — DONE

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
- **Done (in-editor surface, stock binary):** the sample project's
  `addons/goline_ai` dock is the editor-facing end of this architecture — an
  ARCHITECTURE §2 "Editor Layer" consumer that drives the same `goline_cli`
  seam the shell uses. The architecture is exercised end-to-end
  (editor → CLI → ProviderDriver SPI → live model) with no engine build.

## Stage 4 — OpenCode integration — DONE

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
- **Done (session/thread persistence):** drivers accept an optional `session`
  id (opencode `--session <id>`, claude `--resume <id>`) and
  `providers.save_session`/`load_session` persist the provider-reported
  session id (`session.updated`/text-event `session_id`) to a per-project
  `.goline/session.json`. `--handover --continue` (plus `--session-file` to
  override the path) resumes the previous thread instead of starting fresh;
  provider mismatches, missing, or corrupt session files fail closed to a new
  thread. Offline-tested (21 new cases in `test_sessions.py`; suite at **184
  tests**).

## Stage 5 — AI-assisted coding — DONE

- **Done (file-scoped coding workflow):** `goline/cli/workflows.py` plus the
  `--code FILE --instruction "..."` command. Assembles a **file-scoped
  context pack** (file content, same-directory siblings, cross-file
  references, git last-change, permission policy), assembles a strict edit
  prompt that demands a **unified diff only** (and forbids changes outside
  the target file, citing `AI_DEVELOPMENT.md` rules), dispatches it through
  the provider SPI, runs the permission gate/guard on the emitted events,
  then **validates** the returned diff (empty/huge/no-marker guards). The
  diff is **printed for human review and applied manually** — never
  auto-applied. Offline-tested with a mocked provider (exit 0/l/2 paths).
- **Done (explain workflow):** `--explain FILE` assembles the same context
  pack and dispatches an explain prompt; the result is printed for the
  developer. Part of closing the AI-assisted coding loop. New
  `test_workflows.py` (32 tests) brings the offline suite to **147 tests**.

## Stage 6 — AI-assisted debugging — DONE

- **Done (debugging workflow):** `goline/cli/debugging.py` plus the
  `--debug` command. Feed an error / build failure / backtrace (as an
  argument or piped stdin) and Goline assembles a bounded **debug context
  pack** — the raw diagnostics, the most-relevant referenced source file
  (extracted from the backtrace via `File "..."` / `at ...` / `in ...`
  markers, Windows (drive-colon) and res:// path forms supported), its git
  last-change, and the embedded permission policy — then dispatches a
  root-cause investigation prompt through the provider SPI. The model must
  diagnose (root cause + ranked likely causes + concrete next steps/tests)
  and may only sketch fixes as text; it never modifies files. The run goes
  through the same audit gate/`--guard` as handover. **Inverts the safety
  posture of `--code`: diagnose-only, never auto-edit.** New
  `test_debugging.py` (16 tests) brings the offline suite to **163 tests**.

## Stage 7 — Project/context awareness — DONE

- **Done (file-scoped context):** `workflows.build_file_context` gives agents
  accurate, scoped awareness of a single file: bounded file content
  (`line_limit`), same-directory siblings, cross-file references discovered
  by a **bounded** reference scan (limited to the enclosing `goline/` package
  or file dir — never the whole engine tree — with file-size/scan-file/byte
  budgets so it runs in <1 s), git last-change metadata, and the embedded
  MANDATORY permission policy. Exposed via `--context file`, `--print-context
  file`, and the `--code`/`--explain` workflows.

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

## Stage 9 — Testing, performance and polish — DONE

- **Done (optimization + benchmark harness):** measured the real workflows and
  shaved the hot paths:
  - **Policy classification**: precomputed deny/ask pattern tuples in
    `Policy.__init__` (previously rebuilt a combined list on *every*
    `classify` call). `classify` now runs ~18-24 µs/op (~41k-54k ops/s on a
    mixed allow/ask/deny corpus), ~0.00 MB peak allocation, no allocation in
    steady state.
  - **Engine context pack**: `git branch` + `git commit` were two subprocess
    spawns (~78 ms combined since git process startup dominates); a new
    `context._git_rev()` parses `git log -1 --oneline --decorate` into
    (branch, commit) in **one** spawn, and handles `HEAD -> branch` /
    detached-`HEAD` / clean-output cases with the same truthful
    `git_clean: unknown` semantics. Version output is byte-identical; real
    engine-pack builds drop from ~173 ms to ~150 ms p50 (still dominated by
    the remaining 2 git spawns).
  - **`goline/cli/benchmarks/benchmark.py`**: dependency-free harness
    (`python -m goline.cli.benchmarks.benchmark [iterations]`) timing import
    (warm + cold-net), classify throughput + tracemalloc peak, the handover
    scan pipeline, engine/game context packs, and opencode JSON parsing —
    no network. Hermetic smoke tests in `test_benchmark.py`.
- **Done (reliability guard):** a determinism test (`test_classify_is_
  deterministic`) locks `classify` to identical verdict+reason across calls
  and Policy instances, so the precomputed-pattern optimization cannot change
  behavior. 115 offline tests.
