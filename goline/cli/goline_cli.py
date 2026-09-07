"""Goline CLI agent orchestrator (Stage 2).

Agent-agnostic discovery and launch of external AI CLI tools against the
Goline repository. Dependency-free (stdlib only), no upstream Godot code
touched, safe to run without building the engine.

Usage:
    python goline/cli/goline_cli.py --list
    python goline/cli/goline_cli.py --agent claude -- "<cli args...>"
    python goline/cli/goline_cli.py --self-test
    python goline/cli/goline_cli.py --context engine
    python goline/cli/goline_cli.py --context game --project <game dir> -- "<prompt>"
    python goline/cli/goline_cli.py --gate "git push --force origin master" [--audit log.jsonl]
    python goline/cli/goline_cli.py --handover --provider opencode \
        --context engine --model opencode/gpt-4o -- "your prompt"
    python goline/cli/goline_cli.py --handover --provider opencode \
        --model opencode/gpt-4o --audit handover.jsonl -- "your prompt"
    python goline/cli/goline_cli.py --handover --provider opencode \
        --model opencode/gpt-4o --guard -- "your prompt"  # abort (exit 2) on denied cmd
    python goline/cli/goline_cli.py --handover --provider opencode \
        --model opencode/gpt-4o --review [--approval-file a.json] -- "your prompt"
    python goline/cli/goline_cli.py --review prior-audit.jsonl --approval-file a.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

# Make the `goline` package importable when this script is run directly
# (e.g. `python goline/cli/goline_cli.py`) from the repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from goline.cli import context as goline_context
from goline.cli import policy as goline_policy
from goline.cli import providers as goline_providers

# ---------------------------------------------------------------------------
# Adapter table (v1)
# ---------------------------------------------------------------------------
# Each entry is (version_flag, label, kind). kind is one of:
#   "file-edit" - can read/write files (Claude Code, OpenCode, codex, gemini)
#   "chat"      - conversational only (aichat)
# Alias keys map a binary to an adapter entry.
_ADAPTERS = {
    "opencode": ("--version", "OpenCode", "file-edit"),
    "claude": ("--version", "Claude Code", "file-edit"),
    "codex": ("--version", "OpenAI Codex", "file-edit"),
    "gemini": ("--version", "Gemini CLI", "file-edit"),
    "aichat": ("--version", "aichat", "chat"),
}

# Binary names we will probe. Unknown CLIs on PATH are ignored for discovery
# unless launched explicitly, to keep the surface predictable.
_KNOWN_NAMES = frozenset(_ADAPTERS)

# Kind ordering for stable, readable output.
_KIND_RANK = {"file-edit": 0, "chat": 1}


def _which(command: str) -> str | None:
    """Return the absolute path to `command` on PATH, or None."""
    return shutil.which(command)


def _command_argv(command: str) -> list[str]:
    """Build an argv that actually executes `command` across platforms.

    Some CLIs on Windows resolve to PowerShell/Cmd script wrappers
    (`claude.ps1`, `opencode.cmd`, ...) which CreateProcess cannot run
    directly. Return an argv routed through the appropriate interpreter
    when that is the case; otherwise the command as-is.
    """
    path = _which(command)
    if path is None:
        return [command]
    lower = path.lower()
    if lower.endswith(".ps1"):
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
    if lower.endswith((".cmd", ".bat")):
        return ["cmd", "/d", "/c", path]
    return [command]


def _probe_version(command: str, version_flag: str) -> str | None:
    """Run `<command> <version_flag>` and return first non-empty line, or None.

    Read-only against the filesystem. Never writes to the working directory.
    """
    argv = _command_argv(command) + [version_flag]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=10,
            cwd=os.getcwd(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (proc.stdout or "").splitlines() + (proc.stderr or "").splitlines():
        line = line.strip()
        if line:
            return line[:120]
    return None


def discover_agents() -> dict[str, dict]:
    """Return {binary: metadata} for every known CLI found on PATH."""
    found: dict[str, dict] = {}
    for name in _KNOWN_NAMES:
        path = _which(name)
        if path is None:
            continue
        version_flag, label, kind = _ADAPTERS[name]
        version = _probe_version(name, version_flag)
        found[name] = {
            "binary": name,
            "path": path,
            "label": label,
            "kind": kind,
            "version": version,
        }
    # Stable ordering: by kind (file-edit first), then name.
    ordered = sorted(
        found.values(),
        key=lambda m: (_KIND_RANK.get(m["kind"], 99), m["binary"]),
    )
    return {m["binary"]: m for m in ordered}


def resolve_agent(requested: str | None, agents: dict[str, dict]) -> str:
    """Pick which binary to launch.

    `--agent` wins. Otherwise GOLINE_AGENT env var. Otherwise the first
    discoverable file-edit agent.
    """
    if requested:
        if requested not in agents:
            raise RuntimeError(f"agent '{requested}' not found on PATH")
        return requested
    env = os.environ.get("GOLINE_AGENT")
    if env and env in agents:
        return env
    for name in agents:
        if agents[name]["kind"] == "file-edit":
            return name
    raise RuntimeError("no Goline CLI agent found on PATH (try: --list)")


def launch_agent(agent_binary: str, args: list[str]) -> int:
    """Run the agent in the repo root, interactive, pass-through stdio."""
    cwd = os.getcwd()
    argv = _command_argv(agent_binary) + list(args)
    proc = subprocess.run(argv, cwd=cwd)
    return proc.returncode


def _format_agents(agents: dict[str, dict]) -> str:
    lines = []
    for name, m in agents.items():
        version = m["version"] or "unknown"
        lines.append(f"{name:10} {m['kind']:9} {m['label']}  [{version}]")
    return "\n".join(lines) if lines else "(no Goline CLI agents found on PATH)"


def _classify_event_command(ev) -> "goline_policy.Decision | None":
    """Extract a command string from an agent permission/tool event and
    classify it against the policy; None if the event carries no command."""
    cmd = ev.data.get("command")
    if not cmd:
        # Some providers nest the command under a different key.
        cmd = ev.data.get("cmd") or ev.data.get("tool_input")
    if not cmd or not isinstance(cmd, str):
        return None
    return goline_policy.Policy().classify(cmd)


def _audit_agent_events(events, audit) -> None:
    """Record audit entries for every command-bearing agent event."""
    if audit is None:
        return
    for ev in events:
        if ev.kind not in ("permission", "tool", "command"):
            continue
        decision = _classify_event_command(ev)
        if decision is not None:
            audit.record(decision)


def _find_denied_event(events) -> "tuple[object, goline_policy.Decision] | None":
    """Return (event, decision) for the first command the agent emits that the
    policy would DENY; None if nothing is denied. Used by --guard fail-fast.
    ASK verdicts are NOT an automatic denial -- they go to human review."""
    for ev in events:
        if ev.kind not in ("permission", "tool", "command"):
            continue
        decision = _classify_event_command(ev)
        if decision is not None and decision.decision == goline_policy.DENY:
            return ev, decision
    return None


def _load_preseeded_approvals(path: "str | None") -> "dict[str, str]":
    """Load a pre-seeded approvals file: a JSON object mapping a command
    string to an "approve" | "block" human decision. Malformed/unreadable
    files degrade to an empty map (the review then just prompts)."""
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        out = {}
        for k, v in data.items():
            if isinstance(k, str) and isinstance(v, str) and v in ("approve", "block"):
                out[k] = v
        return out
    except (OSError, ValueError):
        return {}


def _prompt_approval(decision: "goline_policy.Decision", prompt_fn=input) -> str:
    """Ask the human for a review decision on a non-allowed command.
    Returns "approve" | "block" | "skip"."""
    while True:
        raw = (
            prompt_fn(
                f"[REVIEW] {decision.decision.upper()} command: {decision.command}\n"
                f"  reason: {decision.reason}\n"
                "  approve [a] / block [b] / skip [s]: "
            )
            or ""
        ).strip().lower()
        if raw in ("a", "approve", "y", "yes"):
            return "approve"
        if raw in ("b", "block", "n", "no"):
            return "block"
        if raw in ("s", "skip", ""):
            return "skip"


def _approve_decisions(
    decisions,
    approvals: "goline_policy.ApprovalLog | None",
    approval_file: "str | None" = None,
    prompt_fn=input,
) -> bool:
    """Review each non-allowed decision: honour a pre-seeded approval for the
    exact command, otherwise prompt the human. Every decision is recorded on
    the approvals trail (decided_by=human). Returns True if a human BLOCKED a
    command (caller should abort), False otherwise."""
    preseeded = _load_preseeded_approvals(approval_file)
    blocked = False
    for decision in decisions:
        if decision is None or decision.allowed:
            continue
        human = preseeded.get(decision.command)
        if human is None:
            human = _prompt_approval(decision, prompt_fn)
        if approvals is not None:
            approvals.record(decision, human)
        if human == "approve":
            print(f"[REVIEW] approved: {decision.command}")
        elif human == "block":
            print(f"[REVIEW] blocked: {decision.command}", file=sys.stderr)
            blocked = True
            break
        # "skip" leaves the automated verdict as-is; nothing further.
    return blocked


def _review_events(
    events,
    approvals: "goline_policy.ApprovalLog | None",
    approval_file: "str | None" = None,
    prompt_fn=input,
) -> bool:
    """Review every command-bearing agent event, honouring the pre-seeded
    approvals and recording human decisions. Returns True if a human blocked
    a command (caller should abort with exit code 2)."""
    decisions = [_classify_event_command(ev) for ev in events]
    return _approve_decisions(
        decisions, approvals, approval_file=approval_file, prompt_fn=prompt_fn
    )


def _decisions_from_audit(path: str) -> "list[goline_policy.Decision]":
    """Rehydrate Decisions from an append-only audit/approval JSONL trail,
    skipping human-verdict rows (they were already decided, not re-received
    for classification)."""
    out: "list[goline_policy.Decision]" = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if rec.get("decided_by") == "human":
                    continue
                if not rec.get("command"):
                    continue
                out.append(
                    goline_policy.Decision(
                        rec.get("decision") or goline_policy.ALLOW,
                        rec.get("reason") or "replayed from audit",
                        rec["command"],
                    )
                )
    except OSError:
        return []
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Goline CLI agent orchestrator")
    parser.add_argument("--list", action="store_true", help="list discovered agents")
    parser.add_argument("--agent", help="which agent binary to launch")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="verify discovery works on this machine",
    )
    parser.add_argument(
        "--context",
        choices=["engine", "game", "file"],
        help="assemble a grounded context pack and launch the agent with it. "
             "With 'file': --project is the file path",
    )
    parser.add_argument(
        "--print-context",
        choices=["engine", "game", "file"],
        help="assemble and print a context pack without launching an agent. "
             "With 'file': --project is the file path",
    )
    parser.add_argument(
        "--project",
        help="game project dir or file path (required with --context/--print-context game or file)",
    )
    parser.add_argument(
        "--gate",
        help="classify a command against the Goline policy (does NOT run it)",
    )
    parser.add_argument(
        "--audit",
        help="append audit entries (JSONL) for gate decisions to this file",
    )
    parser.add_argument(
        "--guard",
        action="store_true",
        help="on --handover: abort (exit 2) if the agent emits a denied command "
             "instead of just auditing it",
    )
    parser.add_argument(
        "--review",
        nargs="?",
        const="__handover_review__",
        metavar="AUDIT_JSONL",
        help="review non-allowed decisions before or after a run. With "
             "--handover: prompt on every ASK/DENY the agent emitted and abort "
             "(exit 2) if you block one. With a path: replay that audit trail "
             "offline. Human decisions append to the --audit trail.",
    )
    parser.add_argument(
        "--approval-file",
        metavar="JSON",
        help="pre-seeded approvals: {command: 'approve'|'block'} consulted "
             "during review before prompting (commands not listed still prompt)",
    )
    parser.add_argument(
        "--handover",
        action="store_true",
        help="dispatch a prompt to a provider with grounded context (t3-style handover)",
    )
    parser.add_argument(
        "--provider",
        help="provider driver to use for handover (opencode | claude)",
    )
    parser.add_argument(
        "--model",
        help="model to use for handover (provider/model for opencode)",
    )
    parser.add_argument(
        "--code",
        metavar="FILE",
        help="file-scoped coding workflow: assemble context for FILE, dispatch "
             "an edit instruction, validate the returned diff, and print it "
             "for human review",
    )
    parser.add_argument(
        "--instruction",
        help="instruction for --code (what the AI should do to the file)",
    )
    parser.add_argument(
        "--explain",
        metavar="FILE",
        help="file-scoped explain workflow: assemble context for FILE, dispatch "
             "an explain prompt, and print the result",
    )
    parser.add_argument(
        "--debug",
        nargs="?",
        const="__debug_stdin__",
        metavar="DIAGNOSTICS",
        help="AI-assisted debugging workflow: feed an error / build failure / "
             "backtrace (argument or piped stdin) plus scoped context to the "
             "provider and print a diagnosis. Never modifies files.",
    )
    parser.add_argument("cli_args", nargs="*", help="args passed to the agent")
    args = parser.parse_args(argv)

    if args.self_test:
        agents = discover_agents()
        print("SELF-TEST: discovered %d agent(s)" % len(agents))
        if agents:
            print(_format_agents(agents))
        return 0

    # Permission gate: classify a command without executing it.
    if args.gate is not None:
        decision = goline_policy.Policy().classify(args.gate)
        print(f"[{decision.decision.upper()}] {args.gate}")
        print(f"  reason: {decision.reason}")
        if args.audit:
            log = goline_policy.AuditLog(args.audit)
            log.record(decision)
            print(f"  audited -> {args.audit}")
        if decision.decision == goline_policy.ALLOW:
            return 0
        if decision.decision == goline_policy.ASK:
            return 2  # review bucket: neither allow nor deny; needs a human
        return 1

    # Print a context pack and stop (no agent launched, no temp file).
    if args.print_context:
        if args.print_context == "file":
            if not args.project:
                print("ERROR: --print-context file requires --project <path>",
                      file=sys.stderr)
                return 1
            from goline.cli.workflows import build_file_context
            pack = build_file_context(args.project)
        else:
            try:
                pack = goline_context.build_context(args.print_context, args.project)
            except ValueError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
        print(pack)
        return 0

    # Offline replay: review a previously-recorded audit/approval trail
    # without re-dispatching an agent. Blocking aborts with exit code 2.
    if args.review is not None and args.review != "__handover_review__":
        decisions = _decisions_from_audit(args.review)
        trail = goline_policy.ApprovalLog(args.review)
        blocked = _approve_decisions(
            decisions,
            trail,
            approval_file=args.approval_file,
        )
        print(f"[review] {len(decisions)} non-policy decision point(s) replayed")
        if blocked:
            print("[review] a command was blocked (exit 2)", file=sys.stderr)
            return 2
        return 0

    # AI-assisted debugging workflow (--debug): feed diagnostics + scoped
    # context to the provider and print a diagnosis. Reads from the argument
    # or, if piped (no argument), from stdin.
    if args.debug is not None:
        from goline.cli.debugging import run_debug_workflow
        diagnostics = ""
        if args.debug == "__debug_stdin__":
            if not sys.stdin.isatty():
                diagnostics = sys.stdin.read()
        else:
            diagnostics = args.debug
        return run_debug_workflow(
            diagnostics,
            provider=args.provider or "opencode",
            model=args.model,
            audit_path=args.audit,
            guard=args.guard,
            workdir=args.project,
        )

    # File-scoped code workflow (--code / --explain): dispatch through the
    # provider SPI with file-level grounded context, validate, and print.
    if args.code:
        from goline.cli.workflows import run_code_workflow
        if not args.instruction:
            print("ERROR: --code requires --instruction", file=sys.stderr)
            return 1
        return run_code_workflow(
            args.code,
            args.instruction,
            provider=args.provider or "opencode",
            model=args.model,
            audit_path=args.audit,
            guard=args.guard,
            workdir=args.project,
        )

    if args.explain:
        from goline.cli.workflows import run_explain_workflow
        return run_explain_workflow(
            args.explain,
            provider=args.provider or "opencode",
            model=args.model,
            workdir=args.project,
        )

    # Handover: dispatch a prompt to a provider with grounded context.
    if args.handover:
        prompt = " ".join(args.cli_args).strip()
        if not prompt:
            print("ERROR: --handover requires a prompt (after '--')", file=sys.stderr)
            return 1
        context_kind = args.context or "engine"
        if context_kind == "file":
            if not args.project:
                print("ERROR: --context file requires --project <file path>",
                      file=sys.stderr)
                return 1
            from goline.cli.workflows import build_file_context
            pack = build_file_context(args.project)
        else:
            try:
                pack = goline_context.build_context(context_kind, args.project)
            except ValueError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            if pack.lower().startswith("no game project"):
                print(f"ERROR: {pack}", file=sys.stderr)
                return 1
        provider = args.provider or "opencode"
        try:
            driver = goline_providers.get_driver(provider)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        ctx_path = goline_providers.write_context_file(pack)
        workdir = args.project or os.getcwd()
        audit = goline_policy.AuditLog(args.audit) if args.audit else None
        print(f"[handover] provider={driver.driver_kind} model={args.model or 'default'} "
              f"context={context_kind} cwd={workdir}"
              + (" audit=" + args.audit if audit else ""))
        result = driver.dispatch(prompt, ctx_path, model=args.model, workdir=workdir)

        # Post-dispatch audit filter: classify and log command-bearing events
        # the agent emitted (headless dispatch has no live permission prompt,
        # so this surfaces the verdict in the transcript and records it).
        _audit_agent_events(result.events, audit)

        # --guard fail-fast: if the agent emitted a command our policy denies,
        # abort with a distinct exit code (2) rather than proceeding as if OK.
        if args.guard:
            denied = _find_denied_event(result.events)
            if denied is not None:
                _ev, decision = denied
                print(f"[GUARD] DENY aborted: {decision.command}", file=sys.stderr)
                print(f"  reason: {decision.reason}", file=sys.stderr)
                return 2

        # --review: human review of every ASK/DENY the agent emitted. A block
        # behaves like --guard (exit 2); approve/skip continue. Human verdicts
        # append to the same trail as the policy verdicts when --audit is set.
        if args.review == "__handover_review__":
            approvals = goline_policy.ApprovalLog(args.audit) if args.audit else None
            blocked = _review_events(
                result.events,
                approvals,
                approval_file=args.approval_file,
            )
            if blocked:
                print("[REVIEW] a command was blocked (exit 2)", file=sys.stderr)
                return 2

        for ev in result.events:
            if ev.kind in ("content.delta", "reasoning", "error"):
                print(f"[{ev.kind}] {ev.data.get('text') or ev.data.get('message') or ''}")
            elif ev.kind in ("permission", "tool"):
                verdict = _classify_event_command(ev)
                tag = verdict.decision.upper() if verdict else "?"
                print(f"[{ev.kind}] [{tag}] {ev.data.get('command') or json.dumps(ev.data)}")
            else:
                print(f"[{ev.kind}] {json.dumps(ev.data)}")
        if result.error:
            print(f"[error] {result.error}", file=sys.stderr)
        print(f"[done] exit={result.exit_code} provider={provider}")
        return 0 if result.exit_code == 0 else 1

    agents = discover_agents()
    if args.list:
        print(_format_agents(agents))
        return 0

    try:
        binary = resolve_agent(args.agent, agents)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Grounded-context mode: write the pack to a temp file and hand the agent
    # a prompt that points at it (plus any caller-provided prompt).
    if args.context:
        if args.context == "file":
            if not args.project:
                print("ERROR: --context file requires --project <file path>",
                      file=sys.stderr)
                return 1
            from goline.cli.workflows import build_file_context
            pack = build_file_context(args.project)
        else:
            try:
                pack = goline_context.build_context(args.context, args.project)
            except ValueError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            if pack.lower().startswith("no game project"):
                print(f"ERROR: {pack}", file=sys.stderr)
                return 1
        fd, path = tempfile.mkstemp(prefix="goline-ctx-", suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(pack)
        except OSError:
            path = None
        prompt_args = list(args.cli_args)
        if not prompt_args:
            prompt_args = [
                f"Read the grounded context at {path} and act on it "
                "per the guidance included."
            ]
        print(f"Context pack ({args.context}) written; launching '{binary}'")
        return launch_agent(binary, prompt_args)

    print(f"Launching agent '{binary}' in {os.getcwd()}")
    print("(interactive; Ctrl-C to stop. Passing stdin/stdout/stderr through.)")
    return launch_agent(binary, args.cli_args)


if __name__ == "__main__":
    sys.exit(main())
