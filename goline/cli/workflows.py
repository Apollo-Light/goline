"""Goline file-scoped context and coding workflows (ARCHITECTURE §5/§6).

Delivers the Stage 5+7 milestone: AI-assisted code generation grounded in
accurate, file-level project awareness. Dependency-free (stdlib only):

- `build_file_context` assembles a focused context pack for a single file
  (content + siblings + cross-file references + git metadata + policy).
- `validate_edit_result` catches obviously-bad agent output before the
  human reviews it (diff-size guard, no auto-apply).
- `run_code_workflow` / `run_explain_workflow` are the entry points called
  from `goline_cli.main`; they dispatch through the provider SPI and return
  a numeric exit code.

No upstream Godot code is touched; nothing writes to the filesystem.
"""

from __future__ import annotations

import os
import subprocess
import sys

# ---------------------------------------------------------------------------
# File-scoped context pack
# ---------------------------------------------------------------------------

_MAX_CONTEXT_LINES = 200     # lines of the target file we include in the pack
_MAX_SIBLINGS = 30           # same-directory files surfaced
_MAX_REFS = 20               # cross-file references surfaced
_MAX_REF_FILE_SIZE = 1 << 20 # skip files > 1 MB when grepping for references
_MAX_REF_SCAN_FILES = 4000   # hard cap on files examined during reference scan
_MAX_REF_SCAN_BYTES = 128 << 20  # hard cap on bytes scanned for references
_SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".import", "node_modules",
    "bin", "obj", ".vscode", ".idea", "build", ".eggs",
})


def build_file_context(
    path: str,
    *,
    root: "str | None" = None,
    line_limit: int = _MAX_CONTEXT_LINES,
    ref_limit: int = _MAX_REFS,
) -> str:
    """Produce a plain-text context pack for a single file.

    Includes: the file's first `line_limit` lines, same-directory sibling
    names, files that reference its basename (bounded), one-line git
    last-change metadata, and the Goline permission policy notice.

    Returns a user-facing error string if the file does not exist or is
    unreadable (callers should check for this before dispatching an agent).
    """
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return f"ERROR: file not found or not a regular file: {path}"

    root = root or _detect_root(path)
    directory = os.path.dirname(path)
    filename = os.path.basename(path)

    lines: list[str] = []
    lines.append("# Goline file context")
    lines.append(f"file: {path}")
    if root:
        lines.append(f"repo_root: {root}")

    # --- file content (bounded) ------------------------------------------------
    content = _read_file_bounded(path, line_limit)
    if content is not None:
        actual_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        lines.append(f"lines_shown: {actual_lines}")
        lines.append("")
        lines.append(content)
    else:
        lines.append("content: unreadable")

    # --- siblings in the same directory ----------------------------------------
    siblings = _sibling_files(directory, exclude=filename, limit=_MAX_SIBLINGS)
    if siblings:
        lines.append("")
        lines.append(f"siblings ({len(siblings)} shown): " + ", ".join(siblings))

    # --- cross-file references -------------------------------------------------
    if filename:
        refs_root = _ref_scan_root(path)
        refs = _find_references(filename, refs_root, limit=ref_limit, exclude=path)
        if refs:
            lines.append("")
            lines.append(f"references ({len(refs)} found): " + ", ".join(refs))

    # --- git last change -------------------------------------------------------
    if root:
        meta = _git_last_change(path)
        if meta:
            lines.append("")
            lines.append(f"last_change: {meta}")

    # --- guidance + policy -----------------------------------------------------
    lines.append("")
    lines.append("## Guidance")
    lines.append(
        "This is a file from Goline (a Godot 4 fork). Help with code "
        "grounded in the file content and siblings above. Do not modify "
        "upstream engine internals unless a roadmap stage authorizes it."
    )
    from goline.cli.policy import guidance_notice
    lines.append("")
    lines.append(guidance_notice())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_root(file_path: str) -> "str | None":
    """Walk up from the file to find a Goline/Godot repo root."""
    cur = os.path.dirname(os.path.abspath(file_path))
    markers = ("version.py", "SConstruct", ".git")
    while True:
        for m in markers:
            if os.path.exists(os.path.join(cur, m)):
                return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _ref_scan_root(file_path: str) -> str:
    """Find a bounded root for the cross-file reference scan.

    Walking the whole engine repo for references is both slow and noisy, so
    we bound the scan to the nearest project directory the file lives in:
    the file's own directory, or -- if the file is inside a `goline`
    package -- the parent `goline/` directory. Falls back to the file's
    directory.
    """
    cur = os.path.dirname(os.path.abspath(file_path))
    # If inside a goline package, scope to the goline root.
    head = cur
    while True:
        if os.path.basename(head) == "goline":
            return head
        parent = os.path.dirname(head)
        if parent == head:
            break
        head = parent
    return cur


def _read_file_bounded(path: str, line_limit: int) -> "str | None":
    """Read the first `line_limit` lines of a file. Returns text or None."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines: list[str] = []
            for i, line in enumerate(fh):
                if i >= line_limit:
                    break
                lines.append(line)
            return "".join(lines) if lines else None
    except OSError:
        return None


def _sibling_files(
    directory: str, *, exclude: str = "", limit: int = _MAX_SIBLINGS
) -> list[str]:
    """Sorted filenames in `directory`, excluding `exclude`."""
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    return [n for n in names if n != exclude][:limit]


def _find_references(
    filename: str,
    root: str,
    *,
    limit: int = _MAX_REFS,
    exclude: str = "",
) -> list[str]:
    """Search for mentions of `filename` (basename only) under `root`.

    Bounded to `limit` hits. Skips the `exclude` file, hidden/VCS dirs,
    and files larger than `_MAX_REF_FILE_SIZE`. Pure stdlib (os.walk +
    str.find); no external grep.
    """
    basename = os.path.basename(filename)
    if not basename:
        return []
    hits: list[str] = []
    scanned = 0
    bytes_read = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if scanned >= _MAX_REF_SCAN_FILES:
                return hits
            scanned += 1
            if fn == basename:
                continue
            fp = os.path.join(dirpath, fn)
            if fp == exclude:
                continue
            try:
                size = os.path.getsize(fp)
            except OSError:
                continue
            if size > _MAX_REF_FILE_SIZE:
                bytes_read += _MAX_REF_FILE_SIZE  # counted, but skipped
                continue
            bytes_read += size
            if bytes_read > _MAX_REF_SCAN_BYTES:
                return hits
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            if basename in text:
                hits.append(os.path.relpath(fp, root).replace(os.sep, "/"))
                if len(hits) >= limit:
                    return hits
    return hits


def _git_last_change(path: str) -> "str | None":
    """One-line `git log` for `path`: 'hash date author: subject'."""
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%h %as %an: %s", "--", path],
            capture_output=True, text=True, timeout=5,
            cwd=os.path.dirname(os.path.abspath(path)),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = (proc.stdout or "").strip()
    return out or None


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

_AI_DEVELOPMENT_RULES = """\
1. Inspect before modifying — understand the file, its callers, and tests.
2. Make small, focused changes. Keep the diff reviewable.
3. Never make unrelated changes outside this file.
4. Preserve existing Godot/Goline behavior. Prefer additive changes.
5. If the edit changes architecture, explain why in the diff body.
6. Never delete functionality to pass a build; fix the root cause.
7. Never silently change project configuration.
8. Stop and ask if the requirement is ambiguous."""

_EDIT_PROMPT_TEMPLATE = (
    "Edit {file_path} as follows:\n\n{instruction}\n\n"
    "Return ONLY a unified diff (--- a/... / +++ b/... / @@ hunk headers).\n"
    "If the edit requires changes outside {file_path}, explain why and\n"
    "DO NOT make those changes.\n\n"
    "AI development rules (MANDATORY):\n{rules}"
)

_EXPLAIN_PROMPT_TEMPLATE = (
    "Explain what {file_path} does, its key components, and how it\n"
    "integrates with the project.\n\nContext:\n{context}"
)


def build_edit_prompt(file_path: str, instruction: str) -> str:
    return _EDIT_PROMPT_TEMPLATE.format(
        file_path=file_path,
        instruction=instruction,
        rules=_AI_DEVELOPMENT_RULES,
    )


def build_explain_prompt(file_path: str, context: str) -> str:
    return _EXPLAIN_PROMPT_TEMPLATE.format(
        file_path=file_path,
        context=context,
    )


# ---------------------------------------------------------------------------
# Post-dispatch validation
# ---------------------------------------------------------------------------

_MAX_DIFF_HUNKS_WARN = 50     # > 50 @@ hunks → warning
_MAX_DIFF_HUNKS_HARD = 1000   # > 1000 @@ hunks → reject
_MAX_OUTPUT_LINES = 2000      # raw output cap


def validate_edit_result(agent_text: str) -> tuple[bool, list[str]]:
    """Check the agent's code-edit output for obviously-bad signals.

    Returns (ok, warnings).  ``ok == False`` means the caller should abort
    (exit 2) rather than presenting the diff to the human.  ``warnings``
    are printed but the diff is still presented when ``ok == True``.
    """
    warnings: list[str] = []
    text = (agent_text or "").strip()
    if not text:
        return False, ["agent returned empty output"]

    line_count = text.count("\n") + 1
    if line_count > _MAX_OUTPUT_LINES:
        warnings.append(f"output is {line_count} lines (cap {_MAX_OUTPUT_LINES})")

    # Count diff hunks (rough but reliable: every @@ line is a hunk).
    hunk_count = text.count("\n@@") + (1 if text.startswith("@@") else 0)
    if hunk_count > _MAX_DIFF_HUNKS_HARD:
        return False, [f"diff has {hunk_count} hunks (cap {_MAX_DIFF_HUNKS_HARD})"]
    if hunk_count > _MAX_DIFF_HUNKS_WARN:
        warnings.append(f"diff has {hunk_count} hunks (review carefully)")

    # Basic sanity: agent should have returned at least some diff content.
    has_diff = ("--- " in text or "diff --git" in text or "@@ " in text)
    if not has_diff:
        warnings.append("no unified-diff markers found — output may be explanation, not a patch")

    return True, warnings


# ---------------------------------------------------------------------------
# Workflow dispatchers (called from goline_cli.main)
# ---------------------------------------------------------------------------

def run_code_workflow(
    file_path: str,
    instruction: str,
    *,
    provider: str = "opencode",
    model: "str | None" = None,
    audit_path: "str | None" = None,
    guard: bool = True,
    workdir: "str | None" = None,
) -> int:
    """Assemble file-scoped context, dispatch an edit prompt, validate, and
    return an exit code (0 = output ready for human review, 2 = error/blocked).

    The agent's diff is printed to stdout for the human to review and apply
    manually.  Edits are NEVER auto-applied.
    """
    from goline.cli import goline_cli
    from goline.cli import policy as goline_policy
    from goline.cli import providers as goline_providers

    abs_path = os.path.abspath(file_path)
    if not os.path.isfile(abs_path):
        print(f"ERROR: file not found: {abs_path}", file=sys.stderr)
        return 1

    ctx = build_file_context(abs_path, root=workdir)
    if ctx.startswith("ERROR:"):
        print(ctx, file=sys.stderr)
        return 1

    prompt = build_edit_prompt(abs_path, instruction)
    ctx_path = goline_providers.write_context_file(ctx)
    cwd = workdir or os.path.dirname(abs_path)

    try:
        driver = goline_providers.get_driver(provider)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    audit = goline_policy.AuditLog(audit_path) if audit_path else None
    print(f"[code] provider={driver.driver_kind} model={model or 'default'} "
          f"file={abs_path}", file=sys.stderr)

    result = driver.dispatch(prompt, ctx_path, model=model, workdir=cwd)
    goline_cli._audit_agent_events(result.events, audit)

    if guard:
        denied = goline_cli._find_denied_event(result.events)
        if denied is not None:
            _, decision = denied
            print(f"[GUARD] DENY aborted: {decision.command}", file=sys.stderr)
            return 2

    text = result.text
    ok, warnings = validate_edit_result(text)
    for w in warnings:
        print(f"[WARN] {w}", file=sys.stderr)

    print(text)
    return 0 if ok else 2


def run_explain_workflow(
    file_path: str,
    *,
    provider: str = "opencode",
    model: "str | None" = None,
    workdir: "str | None" = None,
) -> int:
    """Assemble file-scoped context, dispatch an explain prompt, print result."""
    from goline.cli import providers as goline_providers

    abs_path = os.path.abspath(file_path)
    if not os.path.isfile(abs_path):
        print(f"ERROR: file not found: {abs_path}", file=sys.stderr)
        return 1

    ctx = build_file_context(abs_path, root=workdir)
    if ctx.startswith("ERROR:"):
        print(ctx, file=sys.stderr)
        return 1

    prompt = build_explain_prompt(abs_path, ctx)
    ctx_path = goline_providers.write_context_file(ctx)
    cwd = workdir or os.path.dirname(abs_path)

    try:
        driver = goline_providers.get_driver(provider)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"[explain] provider={driver.driver_kind} model={model or 'default'} "
          f"file={abs_path}", file=sys.stderr)

    result = driver.dispatch(prompt, ctx_path, model=model, workdir=cwd)
    text = result.text
    if text:
        print(text)
    else:
        print("[explain] agent returned no text", file=sys.stderr)
    return 0 if result.exit_code == 0 else 1
