# Goline — CLI Agent Integration (Stage 2)

> **Status:** DESIGN + foundation implemented (additive, no upstream Godot
> edits). The agent-agnostic orchestration layer, the discovery mechanism,
> grounded context packs, the permission/audit gate, the Stage 5+7
> file-scoped coding workflows, and the Stage 6 debugging workflow are all
> real and testable. The in-editor UI surface is deferred until an editor
> build exists (see "Editor surface" below).

## Why "external CLIs"

Goline's AI strategy is **agent-agnostic external CLIs**, not AI living inside
the engine. The AI agents (OpenCode, Claude Code, codex, ...) run as ordinary
developer tools on the repo; Goline discovers, invokes, and (later) surfaces
them. This matches `ARCHITECTURE.md` §3/§4 and requires no heavy in-engine C++
AI systems.

Two consequences:

1. **No deep C++ work is required** for the AI story. The engine can be rebuilt
   only when smoke-testing an editor build.
2. **Agent-agnostic by design** — nothing here is tied to one CLI. Adapters
   normalize each CLI's quirks behind a common interface.

## Target architecture

```
   Goline editor UI (future)          Goline CLI tools             Developer shell
   ┌──────────────────────┐     ┌─────────────────────────┐    ┌──────────────────┐
   │ docks / toolbars     │     │  cli/ (discover, run,   │    │  "claude"        │
   │ (deferred)           │ ──► │  context, gate)         │ ──►│  "opencode"      │
   └──────────────────────┘     └─────────────────────────┘    │  "codex" ...     │
                                        │                      └──────────────────┘
                                        ▼
                              Agent-agnostic contract:
                              discovery / launch / capability /
                              scoped-context / permission gate
```

The seam is a small, dependency-free module under `goline/cli/` that:

1. **Discovers** installed CLI agents on `PATH` (`opencode`, `claude`, `codex`,
   `gemini`, `aichat`, ...).
2. **Reports** each CLI's identity, version, and rough capability (generic,
   natural-language, or file-editing agent) via a `--help`/`--version` sniff.
3. **Launches** a chosen CLI against the repo working directory.
4. **Scopes context** (Stage 7) and **gates permissions** (Stage 8) later.

This is deliberately the "AI CLI Adapters" (§4) and "Tool/Command Execution"
(§8) layers of the architecture, delivered now as plain repo tooling.

## Discovery contract

A CLI is a "Goline agent" if it satisfies these checks in order:

1. **On PATH**: `command -v` / `where` finds an executable of that name.
2. **Responds**: running `<cli> --version` (or `--help` for CLIs that lack a
   version flag) exits 0 and prints something.
3. **Versioned**: a plausible version string is parsed for display.

The adapter table maps each known CLI name to:
- `version_flag` — the flag to probe (`--version`, or `--help`).
- `label` — human name.
- `kind` — `file-edit` (can modify files, e.g. Claude Code, OpenCode) vs
  `chat` (conversational only).

Unknown CLIs are reported as "generic" rather than refused, so the system stays
open.

## Adapter table (v1)

| Name      | Version flag | Kind       | Notes |
|-----------|--------------|------------|-------|
| `opencode`| `--version`  | file-edit  | Primary target (roadmap Stage 4) |
| `claude`  | `--version`  | file-edit  | Claude Code |
| `codex`   | `--version`  | file-edit  | OpenAI Codex |
| `gemini`  | `--version`  | file-edit  | Gemini CLI |
| `aichat`  | `--version`  | chat       | Generic chat backend |

## Launch semantics

- Runs the CLI in the **repo root** (`C:\Users\USER\Documents\Goline\godot`)
  as the working directory, so the agent sees the whole tree.
- Passes through `stdin/stdout/stderr` so the CLI is fully interactive.
- Never mutates the repo or the CLI on its own — the agent (and the human)
  own all file changes.
- Extensible via `GOLINE_AGENT` env var to override which CLI is used.

## Repository context (grounded packs)

`GOLINE.md` at the repo root is the agent handoff document. On top of that,
Goline now assembles **grounded context packs** (`goline/cli/context.py`) so
agents work from accurate, scoped state rather than guesses:

- **Engine pack** (`--context engine`): repo root, git branch/commit/clean
  state, engine markers, tools detected on PATH, key dirs present, the
  `version.py` identity (name/short_name/major/minor/patch), and Goline handoff
  docs. `git_clean` is reported truthfully: `yes`/`no` only when git answers,
  `unknown` when git is unavailable (it never claims a clean tree it could not
  check).
- **Game pack** (`--context game --project <dir>`): requires `project.godot`;
  surfaces project name, `config_version`, main scene, and a bounded (≤60
  files, ≤4 deep) listing of `.gd`/`.cs`/`.tscn`/`.tres` files.

`--context <kind>` writes the pack to a temp file and launches the chosen agent
with a prompt pointing at it (plus any `--` prompt you supply).
`--print-context <kind>` prints the pack without launching an agent or creating
a temp file — useful for review and tests.

## File-scoped context & coding workflows (Stage 5+7)

`goline/cli/workflows.py` extends the grounded-packs idea to a **single file**
and adds two AI-assisted coding entry points:

- **`--code FILE --instruction "..."`** — assembles a file-scoped context
  pack, builds a strict edit prompt (unified-diff only; forbids touching
  anything outside the target file; embeds the `AI_DEVELOPMENT.md` rules),
  dispatches it through the provider SPI, runs the permission gate/guard on
  the agent's emitted events, then **validates** the returned diff
  (empty / huge / missing-marker guards in `validate_edit_result`). The diff
  is printed for **human review and manual apply** — it is never auto-applied.
  Exit: 0 = diff printed, 2 = blocked by guard or invalid output.
- **`--explain FILE`** — assembles the same context pack and dispatches an
  explain prompt, printing the model's explanation of the file's role and
  integration.
- **`--context file --project <path>`** / **`--print-context file`** expose
  the file-scoped pack directly.

**File-scoped context pack** (`workflows.build_file_context`) includes: bounded
file content (first `line_limit` lines), same-directory sibling names,
cross-file references (a **bounded** scan rooted at the enclosing `goline/`
package or the file's own dir — never the whole engine tree — with
file-size/scan-file/scan-byte budgets so it completes fast), one-line git
last-change metadata, and the embedded MANDATORY permission policy. All
filesystem access is read-only; nothing writes.

## AI-assisted debugging (Stage 6)

`goline/cli/debugging.py` adds a diagnose-only workflow (the safety mirror of
`--code`: it never edits files):

- **`--debug "<error / build failure / backtrace>"`** — assembles a **debug
  context pack** (`build_debug_context`): the bounded raw diagnostics, the
  most-relevant source file extracted from the backtrace
  (`_extract_source_path` handles `File "..."`, `at ...`, `in ...` markers,
  Windows `C:\...` drive-colon paths and `res://`-style paths; only existing
  files resolve), its bounded content + git last-change, and the embedded
  permission policy. Dispatches a root-cause investigation prompt through the
  provider SPI (audit/`--guard` applied like handover) and prints the
  diagnosis. The model must provide root cause, ranked likely causes,
  concrete next steps/tests, and may only sketch fixes as text — never
  modify files.
- **`--debug` with no argument reads piped stdin** (non-TTY), so piping a log
  tail works: `Get-Content build.log -Tail 50 | python goline/cli/goline_cli.py --debug`.
- Diagnostics are capped (8 KB) and there is no auto-edit of any kind.

## Editor surface (deferred)

The `docs/goline/ARCHITECTURE.md` "Goline Editor Layer" (§2) implies docks and
toolbars inside the editor. That requires compiling an editor build with a
Godot module / EditorPlugin. That is **not** delivered here — it needs the C++
toolchain. Until then, Goline's CLI integration is exercised from the
**developer shell** (the primary model anyway) and will later get an optional
in-editor launcher.

## Security posture

- Discovery and launch are read-only against `PATH` and subprocesses.
- `--version` sniffing runs with no working-dir writes.
- The helper refuses to shell out to anything not discovered via the adapter
  table unless explicitly allowed.
- Destructive operations are never automated here; real agent actions follow
  the `docs/goline/AI_DEVELOPMENT.md` rules and the Stage 8 permission layer.

## Permission & audit gate (ARCHITECTURE §8/§9)

`goline/cli/policy.py` is a pure (never-executes) decision layer:

- **Classification** — `Policy.classify(command)` returns `allow`/`deny`/
  `ask`/`error` with a reason. Default posture is **deny-destructive**:
  - **Denied:** file/filesystem destruction (`rm`/`del`/`rd`/`rmdir`/`shred`/
    `dd`/`Remove-Item`/`clear-*`/`format`/`wipefs`), `sudo`, dangerous `git`
    (`reset`/`clean`/`rebase`/`merge`/`prune`/`gc`, `checkout --`, `rm`,
    `stash drop`/`clear`, `branch`/`tag -d`, and *any* force push including
    `--force`/`-f`/`+refspec`), shell redirection to a file (`echo hi > x`,
    `python y > out.txt`), remote-code install pipelines (`curl|wget|iwr … |
    sh|bash|python|node|iex`), system/power mutation (`shutdown`/`reboot`/
    `init 0`/`Stop-Computer`), process killing (`kill -9`/`taskkill /f`/
    `Stop-Process -Force`), low-level storage tools (`fdisk`/`parted`/`mkfs`/
    `cryptsetup`), registry deletes, **destructive one-liners from otherwise
    allow-listed interpreters** (`python -c`/`python -m`/`node -e` with
    `shutil.rmtree`, `fs.rmSync`/`rmdirSync`/`unlinkSync`, `os.system`,
    `subprocess`, `pip uninstall`, ...), and any **unrecognized executable**
    (default-deny). Deny always wins over the review bucket, so the
    destructive variants (force push, `branch -d`, `stash drop`, …) never
    degrade to an ask.
  - **Allowed:** read-only `git`, the known toolchain (`python`, `node`,
    `scons`, `cl`/`g++`, ...), known agent CLIs, and harmless file-descriptor
    redirects (`2>&1`).
  - **Ask (review bucket):** mutating-but-recoverable commands are *neither*
    auto-allowed *nor* auto-denied — `Policy` returns `ask` and the human
    decides. Bucket contents: non-destructive `git` mutations
    (`git add`/`commit`/`push`/`stash`/`restore`, `git branch`/`git tag`
    with a name; bare `git branch`/`git tag` list refs and stay allowed) and
    package-manager installs (`pip`/`python -m pip`/`npm`/`pnpm`/`yarn`/
    `brew install`). The same rules are crisped into the grounded-context
    guidance notice so agents know to ask before running them.
  - Executable tokenization is robust: quoted executables with spaces
    (`"my tool" x`) are parsed whole, and absolute paths are normalized to
    their basename so `/usr/bin/rm` still matches the `rm` deny.
  - Callers can add `custom_allow` / `custom_deny` / `custom_ask` regexes, or
    set `deny_all`.
- **Audit log** — `AuditLog` is **append-only**: each decision is a JSONL line
  with a UTC timestamp, decision, reason, and command; policy verdicts carry
  `"decided_by": "policy"`. A write failure never crashes the caller.
- **Approval log** — `ApprovalLog` (same append-only JSONL format) records
  **human** decisions with `"decided_by": "human"` plus `"human_decision":
  "approve"|"block"`, so machine verdicts and human overrides share one trail
  and are never confused.

CLI: `--gate "command"` classifies a command (does **not** run it) and returns
0 on allow / 1 on deny / 2 on ask; `--audit <path>` appends the decision to a
JSONL file.

**Review / approval UX** (`--review`, `--approval-file`): because headless
dispatch records agent commands *after* the subprocess exits, review is
post-dispatch — it never mid-run halts an in-flight agent — but it still gives
a human veto before a run is treated as done:

- `--handover ... --review` prompts on every `ask`/`deny` the agent emitted
  (`approve [a]` / `block [b]` / `skip [s]`). A `block` aborts like `--guard`
  (exit 2); approve/skip continue. Human verdicts append to the `--audit`
  trail when set; otherwise they stay in memory.
- `--review <audit.jsonl>` replays a recorded trail offline (no provider),
  prompting per non-allowed machine record and appending human verdicts back
  to the same file. Human rows are never re-prompted.
- `--approval-file <approvals.json>` pre-seeds `{command: "approve"|"block"}`
  so known-good/bad commands skip the prompt (anything not listed still asks).
- `--guard` remains the fail-fast: a policy `deny` aborts (exit 2) before any
  review prompt appears. `--guard` and `--review` compose.

## T3 Code patterns (adopted via Option A port)

We studied **T3 Code** (`pingdotgg/t3code` — an agent "harness control surface"
that wraps Codex/Claude/Cursor/Grok/OpenCode behind a WebSocket server + web/
desktop/mobile clients). It is a large Node 24 + Effect + pnpm + vite-plus
monorepo; we deliberately did **not** vendor it. Instead we ported its clean
ideas into `goline/cli/providers.py`:

- **ProviderDriver SPI** — a common `ProviderDriver` interface
  (`driver_kind` / `display_name` / `dispatch`) mirroring T3's
  `ProviderDriver`/`ProviderInstance` model, reduced to a one-shot dispatch.
- **Normalized event vocabulary** — a small discriminated-union of
  `ProviderEvent`s (`content.delta`, `reasoning`, `prompt.recorded`,
  `session.updated`, `permission`, `error`, `done`), inspired by T3's
  `ProviderRuntimeEventV2`.
- **Drivers** (native headless CLI modes, no hand-rolled HTTP SDK):
  - `OpenCodeDriver` → `opencode run --format json [--file <ctx>] [--model <m>] <prompt>`
    (matches T3's `opencode` provider; `--file` attaches the grounded context).
  - `ClaudeDriver` → `claude -p` (headless print mode) for parity.
- **`handover` command** — our lightweight port of `t3code handover`: resolve
  the workspace/git root, assemble a grounded context pack, write it to a temp
  file, and dispatch the first prompt to a provider, streaming normalized
  events. Usage:

  ```
  python goline/cli/goline_cli.py --handover --provider opencode \
      --context engine --model opencode/<model> -- "your prompt"
  python goline/cli/goline_cli.py --handover --provider claude \
      --context game --project <game> -- "help with player.gd"
  ```

  > **Auth note (real-world):** the standalone `opencode` CLI reports **0
  > credentials** via `opencode providers list`, **and yet its free-tier models
  > (verified live: `opencode/nemotron-3.5-lightning-free`, etc. from
  > `opencode models`) work with no login** — a real handover was run against
  > one successfully. Paid/model-specific providers will additionally need
  > `opencode providers login`. Verified quirks: the executable resolves to a
  > PowerShell `.ps1` wrapper (handled by `providers._executable_argv`), and
  > `opencode run` requires the prompt **before** `--file` (a trailing
  > positional after `--file` is misread as a file path, not inline text).

## Verified on the dev machine

- **OpenCode CLI `1.18.27`** installed globally (`npm i -g --allow-scripts=opencode-ai opencode-ai`; the npm package's postinstall script must be allowed so the correct Windows `bin/opencode.exe` is fetched — the default blocked-script warning leaves a non-Windows stub otherwise).
- **Claude Code `2.1.220`** installed globally (npm).
- `python goline/cli/goline_cli.py --self-test` discovers both as `file-edit` agents.
- **Live-validated end-to-end**: an engine handover
  (`--handover --context engine`) correctly answered "Goline" from `version.py`,
  and a game handover (`--handover --context game --project
  goline/examples/sample_game`) read the real game files from the context pack
  and returned the correct answers (`player.gd extends CharacterBody2D`; Player
  node at `Vector2(576, 324)`). Both streamed normalized events and exited 0.

> The user's ARE you using OpenCode now is the **desktop app** (Electron,
> `%LOCALAPPDATA%\Programs\@opencode-aidesktop\OpenCode.exe`), which is a GUI
> and NOT a headless CLI — it cannot be shelled out to by Goline. Goline
> automation requires the standalone CLI (installed above), which is a distinct
> binary from the desktop app.

## Usage

```
python goline/cli/goline_cli.py --list          # list discovered agents
python goline/cli/goline_cli.py --agent claude -- "<args>"         # launch one
python goline/cli/goline_cli.py --self-test     # verify discovery on this machine
python goline/cli/goline_cli.py --print-context engine             # show engine pack
python goline/cli/goline_cli.py --context engine -- "<prompt>"     # engine agent
python goline/cli/goline_cli.py --context game --project <game> -- "<prompt>"
python goline/cli/goline_cli.py --gate "git status"                # allow (exit 0)
python goline/cli/goline_cli.py --gate "rm -rf /tmp" --audit a.jsonl   # deny (exit 1)
python goline/cli/goline_cli.py --gate "pip install x"            # ask (review, exit 2)
python goline/cli/goline_cli.py --handover --provider opencode \
    --context engine --model opencode/<model> -- "prompt"          # t3-style handover
python goline/cli/goline_cli.py --handover --provider opencode \
    --model opencode/<model> --audit handover.jsonl -- "prompt"    # + audit gate
python goline/cli/goline_cli.py --handover --provider opencode \
    --model opencode/<model> --guard -- "prompt"                 # abort (exit 2) on denied cmd
python goline/cli/goline_cli.py --handover --provider opencode \
    --model opencode/<model> --review -- "prompt"   # ask/deny -> human veto; block = exit 2
python goline/cli/goline_cli.py --review handover.jsonl --approval-file known.json  # offline replay
python goline/cli/goline_cli.py --print-context file --project <path>   # file-scoped pack
python goline/cli/goline_cli.py --code <file> --instruction "add _ready" \
    --provider opencode [--guard] [--audit a.jsonl]   # code workflow -> diff for review
python goline/cli/goline_cli.py --explain <file> --provider opencode   # explain workflow
python goline/cli/goline_cli.py --debug "ERROR: Null access on instance (at player.gd:12)" \
    --provider opencode          # debugging workflow -> diagnosis (never edits)
Get-Content build.log -Tail 50 | python goline/cli/goline_cli.py --debug   # pipe diagnostics
```

## Tests

```
python -m unittest discover -s goline/cli/tests -v   # pure, no network
```

## Performance (Stage 9)

`goline/cli/benchmarks/benchmark.py` times the real workflows with no
dependencies and no network:

```
python -m goline.cli.benchmarks.benchmark [iterations]
```

Measured (author's machine, Python 3.12.10):

- **Import** — warm in-process reload ~2 ms; cold (`import goline.cli.goline_cli`
  in a fresh interpreter, minus Python startup) ~89 ms.
- **`Policy.classify`** — ~18-24 µs/op (~41k-54k ops/s) on a mixed
  allow/ask/deny corpus; ~0 MB peak allocation (steady state allocates
  nothing). Achieved by precomputing the deny/ask pattern tuples in
  `Policy.__init__` instead of rebuilding a combined list on every call.
- **Engine context pack** — ~150-240 ms p50 (dominated by 2 real `git`
  spawns; a third was eliminated by folding branch+commit into one
  `git log --oneline --decorate` call — see `context._git_rev`).
- **Game context pack** — sub-millisecond.
- **File-scoped context pack** (`build_file_context`) — ~0.5 s on
  `goline/cli/*.py`, dominated by the bounded cross-file reference scan
  (rooted at the `goline/` package, not the whole engine tree; capped by
  file-size / scan-file / scan-byte budgets) plus one `git log` call.
- **Debug context pack** (`build_debug_context`) — ~0.2 s on a backtrace
  referencing `goline/cli/*.py` (one `git log` call; no tree scan).
- **Handover scan pipeline** (extract + classify a 20-event agent stream +
  record to audit) — ~16 ms per sample batch.
- **Opencode JSON parsing** — 50 events in ~0.3 ms (`_parse_opencode_events`).

A determinism test (`test_policy.py::test_classify_is_deterministic`) locks
`classify` verdicts so the precomputed-pattern optimization cannot change
behavior.
