"""Goline AI-assisted debugging workflow (ARCHITECTURE §7, Stage 6).

Provides a pure-CLI debugging entry point that feeds an error / build
failure / backtrace to an external AI CLI together with accurate, scoped
project context, and returns a structured diagnosis for the developer.

- `build_debug_context` assembles a bounded context pack: the raw diagnostics
  text, the most-relevant source file (extracted from the backtrace when
  possible), git/engine identity, and the embedded permission policy.
- `run_debug_workflow` is the entry point called from `goline_cli.main`.

Dependency-free (stdlib only). Nothing here writes to the repo, runs
anything, or auto-applies fixes.
"""

from __future__ import annotations

import os
import re
import sys

# Reuse the workflows module's bounded source-file reading + root helpers.
from goline.cli import workflows

_SOURCE_LINE_CAP = 120        # max source lines to include from the referenced file
_MAX_DIAGNOSTICS_CHARS = 8000 # cap on the raw diagnostics text we embed


def build_debug_context(
    diagnostics: str,
    *,
    source_root: "str | None" = None,
    diagnostics_chars: int = _MAX_DIAGNOSTICS_CHARS,
) -> "tuple[str, str | None]":
    """Assemble a debugging context pack from raw diagnostics text.

    Returns (pack, source_path). `source_path` is the best-guess source file
    referenced by the diagnostics (a path that exists under source_root), or
    None if nothing resolvable is found.

    The pack includes: the (bounded) diagnostics, the referenced source file's
    bounded content, project identity + git metadata, and the mandatory
    permission policy. The source file is never modified.
    """
    diag = (diagnostics or "").strip()
    if not diag:
        diag = "(no diagnostics supplied)"

    lines: list[str] = []
    lines.append("# Goline debug context")
    lines.append("This is a debugging session: diagnose the error below. "
                 "Do the investigation only; propose fixes as text.")
    lines.append("")
    lines.append("## Diagnostics")
    lines.append(diag[:diagnostics_chars])
    if len(diag) > diagnostics_chars:
        lines.append(f"\n[... diagnostics truncated at {diagnostics_chars} chars ...]")

    # Try to resolve the source file the diagnostics references.
    src_root = source_root
    if src_root is None:
        src_root = _detect_repo_root()
    source_path = _extract_source_path(diag, src_root)

    if source_path:
        lines.append("")
        lines.append(f"## Referenced source: {source_path}")
        content = workflows._read_file_bounded(source_path, _SOURCE_LINE_CAP)
        if content is not None:
            lines.append(f"lines_shown: {content.count(chr(10)) + 1}")
            lines.append(content)
        else:
            lines.append("(file exists but could not be read)")

        meta = workflows._git_last_change(source_path)
        if meta:
            lines.append("")
            lines.append(f"source_last_change: {meta}")

    lines.append("")
    lines.append("## Guidance")
    lines.append(
        "Diagnose the root cause, list likely causes with evidence from the "
        "diagnostics and source, and propose concrete next steps / tests. "
        "Do NOT modify files. If you are unsure, say so."
    )
    from goline.cli.policy import guidance_notice
    lines.append("")
    lines.append(guidance_notice())
    return "\n".join(lines), source_path


def _detect_repo_root() -> "str | None":
    """Best-effort repo root: cwd upward for markers, else cwd."""
    cur = os.getcwd()
    markers = ("version.py", "SConstruct", ".git", "project.godot")
    while True:
        for m in markers:
            if os.path.exists(os.path.join(cur, m)):
                return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


# Match common "path:line" references in tracebacks / Godot error output.
# Accept forward/back slashes and a Windows drive colon so both res://-style
# relative paths and C:\...\file.gd absolute paths resolve. Quoted paths
# (File "x") may contain spaces; unquoted markers (at/in) match space-free
# path tokens only (so the leading keyword is never absorbed into the path).
_SOURCE_RE = re.compile(
    r"(?:File\s+[\"'](?P<fileq>[^\"']+?\.(?:gd|cs|py|ts|js))[\"']"
    r"|(?:at|in)\s+(?P<at>[\w./\\:\-]+?\.(?:gd|cs|py|ts|js))[:\s,]"
    r"|(?P<plain>(?:[A-Za-z]:[/\\])?[\w./\\:\-]+?\.(?:gd|cs|py|ts|js))(?:[:\s,]|$))",
    re.IGNORECASE,
)


def _extract_source_path(diagnostics: str, root: "str | None") -> "str | None":
    """Return the first diagnostics-referenced source path that exists under
    `root` (or as-is, if absolute and existing). None if nothing resolves."""
    if not root:
        return None
    root = os.path.abspath(root)
    for cand in _source_candidates(diagnostics):
        cand = cand.strip()
        if not cand.endswith((".gd", ".cs", ".py", ".ts", ".js")):
            continue
        abs_cand = os.path.abspath(cand)
        if os.path.isfile(abs_cand):
            return abs_cand
        joined = os.path.normpath(os.path.join(root, cand))
        if os.path.isfile(joined):
            return joined
    return None


def _source_candidates(diagnostics: str) -> "list[str]":
    """Collect candidate source paths from diagnostics text (deduplicated,
    in order of appearance)."""
    candidates: "list[str]" = []
    seen: "set[str]" = set()
    for m in _SOURCE_RE.finditer(diagnostics):
        group = m.group("fileq") or m.group("at") or m.group("plain")
        if group:
            g = group.strip()
            if g and g not in seen:
                seen.add(g)
                candidates.append(g)
    return candidates


_DEBUG_PROMPT_TEMPLATE = (
    "A build / runtime / regression problem occurred in {repo}.\n\n"
    "Here is the diagnostics output:\n{diagnostics}\n\n"
    "Investigate the root cause using the context. Provide:\n"
    "1. Root cause (with evidence from the diagnostics and any referenced "
    "source).\n"
    "2. Likely causes if not certain, ranked.\n"
    "3. Concrete next steps: which files to inspect, what to run to confirm, "
    "and which tests to run.\n"
    "4. If you propose a code fix, describe it as a diff sketch only — "
    "DO NOT modify files.\n\n"
    "Be concise and specific."
)


def build_debug_prompt(diagnostics: str, repo_root: "str | None") -> str:
    repo = repo_root or os.getcwd()
    return _DEBUG_PROMPT_TEMPLATE.format(
        repo=repo,
        diagnostics=(diagnostics or "").strip() or "(none)",
    )


def run_debug_workflow(
    diagnostics: str,
    *,
    provider: str = "opencode",
    model: "str | None" = None,
    audit_path: "str | None" = None,
    guard: bool = True,
    workdir: "str | None" = None,
) -> int:
    """Dispatch a debugging prompt with grounded context and print the
    diagnosis. Returns an exit code (0 = ok, 1 = provider/input error,
    2 = guarded)."""
    from goline.cli import goline_cli
    from goline.cli import policy as goline_policy
    from goline.cli import providers as goline_providers

    if not (diagnostics or "").strip():
        print("ERROR: --debug requires diagnostics (argument or piped stdin)",
              file=sys.stderr)
        return 1

    src_root = workdir or _detect_repo_root()
    pack, _src = build_debug_context(diagnostics, source_root=src_root)

    prompt = build_debug_prompt(diagnostics, src_root)
    ctx_path = goline_providers.write_context_file(pack)
    cwd = src_root or os.getcwd()

    try:
        driver = goline_providers.get_driver(provider)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    audit = goline_policy.AuditLog(audit_path) if audit_path else None
    print(f"[debug] provider={driver.driver_kind} model={model or 'default'} "
          f"cwd={cwd}", file=sys.stderr)

    result = driver.dispatch(prompt, ctx_path, model=model, workdir=cwd)
    goline_cli._audit_agent_events(result.events, audit)

    if guard:
        denied = goline_cli._find_denied_event(result.events)
        if denied is not None:
            _, decision = denied
            print(f"[GUARD] DENY aborted: {decision.command}", file=sys.stderr)
            return 2

    text = result.text
    if text:
        print(text)
    else:
        print("[debug] agent returned no text", file=sys.stderr)
    return 0 if result.exit_code == 0 else 1
