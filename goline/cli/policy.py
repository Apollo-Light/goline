"""Goline command permission / audit gate (ARCHITECTURE §8/§9, Stage 8).

Pure, dependency-free policy that decides whether a command string may be
run by an agent, plus an append-only audit log. Nothing here executes
commands; it only *classifies* them so callers (the orchestrator or a human)
can enforce the decision.

Core ideas:
  - Every command is reduced to its executable name + a normalized lowercased
    form, then matched against deny patterns (destructive) and allow patterns.
  - Default posture: allow read-only/inspect commands; DENY destructive ones
    unless a policy explicitly allows them.
  - Audit entries are timestamped {decision, reason, command} and appended as
    JSON lines; the log is never mutated in place (append-only).

This module is deliberately pure (no subprocess) and fully offline-testable.
"""

from __future__ import annotations

import datetime
import json
import os
import re

# ---------------------------------------------------------------------------
# Destructive command patterns (default deny). Each is an executable name or
# a regex matched against the whole normalized command.
# ---------------------------------------------------------------------------

# Executable names that are destructive by nature, whatever their args.
_DENY_EXECUTABLES = frozenset(
    {
        # file / filesystem destruction
        "rm",
        "rmdir",
        "shred",
        "dd",
        "deltree",
        "format",
        "wipefs",
        "del",
        "rd",
        "remove-item",
        "ri",
        "clear-item",
        "clear-content",
        "remove-itemproperty",
        # partitioning / low level storage
        "mkfs",
        "fdisk",
        "parted",
        "sfdisk",
        "gdisk",
        "cryptsetup",
        "pvcreate",
        "vgremove",
        "lvremove",
        # system / power
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "init",
        # process killing (force)
        "kill",
        "killall",
        "pkill",
        "taskkill",
        "stop-process",
    }
)

# Whole-command regexes for dangerous flags/combinations.
_DENY_PATTERNS = (
    # --- git history / working-tree destruction ---
    re.compile(r"\bgit\s+(reset|clean|rebase|merge|prune|gc)\b"),
    re.compile(r"\bgit\s+checkout\s+--\s+"),
    re.compile(r"\bgit\s+(branch|tag)\s+-(d|D)\b"),
    re.compile(r"\bgit\s+stash\s+(drop|clear)\b"),
    re.compile(r"\bgit\s+rm\b"),
    # --- git force push (any form, incl. +refspec) ---
    re.compile(r"\bgit\s+push\b.*(--force|\\-\\-force|\s-f\b)"),
    re.compile(r"\bgit\s+push\b.*\+\s*[A-Za-z0-9_:./-]+"),
    # --- recursive / forced deletion across shells ---
    re.compile(r"\b(rm|rmdir)\b.*-\w*r\b"),
    re.compile(r"\b(rm|rmdir|del|rd|deltree|format)\b.*[-/][Ss]"),
    re.compile(r"\b(Remove-Item|Clear-Item|Clear-Content)\b.*\s(-Recurse|-Force)"),
    re.compile(r"\bremove-item\b.*\s-(recurse|force|r|f)\b"),
    # --- privilege escalation / shell takeover ---
    re.compile(r"\bsudo\b"),
    re.compile(r"\b>:\(\)|:\s*\(\s*\)\s*\{"),
    re.compile(r"\b(eval|exec)\b.*\$\("),
    # --- remote code install pipelines (supply chain) ---
    re.compile(r"\b(curl|wget|iwr|Invoke-WebRequest)\b.*\|\s*(sh|bash|zsh|python|python3|node|iex)\b"),
    re.compile(r"\b(iwr|Invoke-WebRequest)\b.*\|\s*iex\b"),
    # --- system mutation ---
    re.compile(r"\b(reg|reg\.exe)\s+delete\b"),
    re.compile(r"\b(taskkill|kill|Stop-Process|pkill|killall)\b.*\b(-9|/f|-Force|force)\b"),
    re.compile(r"\b(shutdown|reboot|halt|poweroff|Stop-Computer|Restart-Computer)\b"),
    re.compile(r"\binit\s+(0|6)\b"),
    re.compile(r"\bchmod\s+[0-7]{3}\b"),
    # Shell redirection to a file (echo hi > x, python y > out.txt) is denied.
    # The previous `>+` form required a preceding word boundary, which let a
    # redirect after a space slip through AND wrongly caught harmless `2>&1`
    # file-descriptor redirects (word boundary before `>`). This form catches
    # ` > file` / `&& > file` while `(?!&1)` keeps `2>&1` allowed.
    re.compile(r"(?:\s|^|;|&&)\>+\s*(?!&1)\S+"),
    re.compile(r"\b(powershell|cmd)\s+/c\s+(rm|del|format|rd|deltree|reset|clean|iwr|curl)"),
    # Destructive one-liners from interpreters that are otherwise allow-listed
    # (python/node are safe for script runs but `-c`/`-e` can perform arbitrary
    # deletion). Matches the interpreter followed by a code-eval flag whose
    # payload contains a destructive operation.
    re.compile(
        r"\b(python|python3|py|node)\b.*\s(-c|-e|-p|--eval|--print|-m)\s.*"
        r"\b(remove|rmtree|rm\s+-rf|rmdir|unlink\b|delete|uninstall|shutil|"
        r"os\.system|subprocess|rmsync|rmdirsync|unlinksync)\.?\b"
    ),
)

# Read-only / inspect commands we permit by default. Anything not matching a
# deny pattern and whose executable is known-safe is allowed; a small
# allow-list makes the intent explicit and tightens the default.
_ALLOW_EXECUTABLES = frozenset(
    {
        "git",
        "python",
        "node",
        "scons",
        "cl",
        "g++",
        "clang",
        "where",
        "Get-ChildItem",
        "dir",
        "ls",
        "cat",
        "type",
        "echo",
        "opencode",
        "claude",
    }
)

# Commands that are neither auto-deny (destructive) nor auto-allow (safe reads
# / known toolchain): they MUTATE the repo or the machine in a recoverable,
# intentional way, so the safe default is to ASK a human for a review decision.
# Destructive forms of these (force push, branch/tag -d, stash drop/clear,
# ...) are caught by _DENY_PATTERNS first, so only the non-destructive
# mutations land here.
_ASK_PATTERNS = (
    # Non-destructive git mutations.
    re.compile(r"\bgit\s+(add|commit|push|stash|restore)\b"),
    # `git branch` / `git tag` alone only list refs (read-only); with an
    # operand they mutate. The `-d`/`-D` forms are denied further above.
    re.compile(r"\bgit\s+(branch|tag)\s+"),
    # Package-manager installs (network + machine/system mutation, but are
    # ordinary intended work).
    re.compile(r"\b(pip|pip3)\s+install\b"),
    re.compile(r"\b(python|python3|py)\s+-m\s+pip\s+install\b"),
    re.compile(r"\b(npm|pnpm|yarn)\s+(install|add)\b"),
    re.compile(r"\bbrew\s+install\b"),
)

# ---------------------------------------------------------------------------
# Decision types
# ---------------------------------------------------------------------------

DENY = "deny"
ALLOW = "allow"
ERROR = "error"
ASK = "ask"  # neither auto-deny nor auto-allow: needs a human review decision.


class Decision:
    """The verdict for a command plus a human-readable reason."""

    __slots__ = ("decision", "reason", "command")

    def __init__(self, decision: str, reason: str, command: str) -> None:
        self.decision = decision
        self.reason = reason
        self.command = command

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW

    @property
    def needs_review(self) -> bool:
        """True when the machine verdict needs a human review decision
        (ASK, or DENY a human might want to override)."""
        return self.decision in (ASK, DENY)

    def to_dict(self) -> dict:
        return {"decision": self.decision, "reason": self.reason, "command": self.command}


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

class Policy:
    """Default-deny-destructive policy. Pure and deterministic."""

    def __init__(
        self,
        custom_deny: "list[str] | None" = None,
        custom_allow: "list[str] | None" = None,
        custom_ask: "list[str] | None" = None,
        deny_all: bool = False,
    ) -> None:
        # Extra rule regexes (strings) compiled once at construction. The
        # combined deny/ask tuples are precomputed too, so `classify` never
        # reallocates a fresh list per call (hot path, called per agent event
        # and per `--gate`).
        self._extra_deny = tuple(re.compile(p) for p in (custom_deny or []))
        self._extra_allow = tuple(re.compile(p) for p in (custom_allow or []))
        self._extra_ask = tuple(re.compile(p) for p in (custom_ask or []))
        self._deny_pats = self._extra_deny + _DENY_PATTERNS
        self._ask_pats = self._extra_ask + _ASK_PATTERNS
        self._deny_all = deny_all

    @staticmethod
    def _executable(command: str) -> str:
        """Return the leading executable token, lowercased.

        Handles quoted executables that contain spaces (`"my tool" x`) which a
        naive whitespace split would truncate to `my`. Paths are normalized to
        their basename so `/usr/bin/rm x` still matches the `rm` deny entry.
        """
        cmd = command.strip()
        if not cmd:
            return ""
        if cmd[0] in ("'", '"'):
            # Quoted first token: take up to the matching close quote.
            quote = cmd[0]
            end = cmd.find(quote, 1)
            tok = cmd[1 : end if end != -1 else len(cmd)]
        else:
            tok = cmd.split(None, 1)[0]
        head = tok.strip().strip('"').strip("'")
        # Normalize a path to its basename so deny/allow sets still match
        # (`/usr/bin/rm` -> `rm`).
        base = os.path.basename(head.replace("\\", "/"))
        return (base or head).lower()

    def classify(self, command: str) -> Decision:
        """Return an allow/deny decision for `command` (never executes it)."""
        cmd = (command or "").strip()
        if self._deny_all:
            return Decision(DENY, "deny-all policy active", cmd)
        if not cmd:
            return Decision(ERROR, "empty command", cmd)

        norm = cmd.lower()
        exe = self._executable(cmd)

        # Extra allow rules first (explicitly permitted by the operator).
        if any(p.search(norm) for p in self._extra_allow):
            return Decision(ALLOW, "explicit allow rule matched", cmd)

        # Check caller-provided deny rules, then built-in deny pattern set.
        for pat in self._deny_pats:
            if pat.search(norm):
                return Decision(DENY, f"deny pattern: {pat.pattern}", cmd)

        if exe in _DENY_EXECUTABLES:
            return Decision(DENY, f"destructive executable: {exe}", cmd)

        # Review bucket: mutating-but-recoverable commands get ASKED (custom
        # operator patterns first, then the built-in ask set). Deny (above)
        # already won for the destructive forms of these same commands.
        for pat in self._ask_pats:
            if pat.search(norm):
                return Decision(ASK, f"review requested: {pat.pattern}", cmd)

        if exe == "git":
            # Already handled destructive git ops above; remaining git is
            # read-only-ish (status/diff/log/rev-parse) and allowed.
            return Decision(ALLOW, "git command not matching destructive patterns", cmd)

        if exe in _ALLOW_EXECUTABLES:
            return Decision(ALLOW, f"known-safe executable: {exe}", cmd)

        # Unknown executable: default-deny (safer) unless allow-listed above.
        return Decision(DENY, f"unrecognized executable: {exe}, not allow-listed", cmd)


# ---------------------------------------------------------------------------
# Audit log (append-only)
# ---------------------------------------------------------------------------

class AuditLog:
    """Append-only JSONL audit log. Never rewrites existing entries."""

    def __init__(self, path: "str | None" = None) -> None:
        self.path = path
        self._memory: list[dict] = []

    def record(self, decision: Decision) -> None:
        entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "decided_by": "policy",
            **decision.to_dict(),
        }
        self._memory.append(entry)
        if self.path:
            self._append_to_disk(entry)

    def _append_to_disk(self, entry: dict) -> None:
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            # Logging failure must never crash the caller.
            pass

    @property
    def entries(self) -> "list[dict]":
        return list(self._memory)


class ApprovalLog(AuditLog):
    """Append-only JSONL trail of HUMAN review decisions (decided_by=human).

    Same append-only semantics and no-crash-on-write-failure behavior as
    `AuditLog`, but each entry pairs the machine verdict with the human's
    `approve` / `block` decision, so automated policy verdicts and human
    overrides are never confused.
    """

    def record(self, decision: Decision, human_decision: str) -> None:
        entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "decided_by": "human",
            "human_decision": human_decision,
            **decision.to_dict(),
        }
        self._memory.append(entry)
        if self.path:
            self._append_to_disk(entry)


DEFAULT_DENY_NOTICE = (
    "Destructive commands (rm/del, git reset/clean/force-push, sudo, ...) are "
    "denied by the default Goline policy. Override with an explicit allow rule "
    "only when you intend it."
)


def default_kickoff_notice() -> str:
    return DEFAULT_DENY_NOTICE


# Human-readable summary of the DENY *patterns* (regex-based rules that are
# not expressed as a single denied executable). Derived from _DENY_PATTERNS so
# an agent can "hold" the policy as an instruction.
_DENY_PATTERN_NOTICE = (
    "git history/destruction: reset, clean, rebase, merge, prune, gc, "
    "checkout --, rm, stash drop/clear, branch/tag -d, and ANY force push "
    "(--force, -f, or +refspec)",
    "privilege escalation: sudo; shell redirection to a file",
    "remote code install: curl | wget | iwr piped to sh | bash | python | node | iex",
    "registry deletion and Windows/system mutation (reg delete, init 0, ...)",
    "any executable NOT in the allow list (unknown tools are denied by default)",
)

_ASK_NOTICE = (
    "Commands that mutate state in a recoverable way (git add/commit/push/"
    "stash/restore, branch/tag with a name, pip/npm/yarn/brew install) are "
    "neither auto-allowed nor auto-denied -- ASK the human for a review "
    "decision before running them."
)


def guidance_notice() -> str:
    """Render the gate policy as a MANDATORY instruction block for an agent."""
    exes = ", ".join(sorted(_DENY_EXECUTABLES))
    patterns = "".join(f"- {line}\n" for line in _DENY_PATTERN_NOTICE)
    return (
        "## Agent permission policy (MANDATORY)\n"
        "You MUST NOT run any command matching these rules without first "
        "asking the human for explicit approval:\n"
        + f"- Denied executables: {exes}\n"
        + patterns
        + f"- Review bucket: {_ASK_NOTICE}\n"
        + "If your next action would run such a command, STOP and ask first."
    )
