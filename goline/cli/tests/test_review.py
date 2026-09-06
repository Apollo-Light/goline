"""Tests for the Stage 8 review/approval UX (goline_cli --review).

Offline: no provider is invoked -- DispatchResult shapes are built directly
and `input`/approvals are mocked, so the human-review path is deterministic.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from goline.cli import goline_cli as cli
from goline.cli import policy
from goline.cli import providers


def _decision(cmd: str) -> policy.Decision:
    return policy.Policy().classify(cmd)


class PromptApprovalTest(unittest.TestCase):
    def test_approve_alias(self):
        self.assertEqual(cli._prompt_approval(_decision("git push origin master"), prompt_fn=lambda _: "a"), "approve")
        self.assertEqual(cli._prompt_approval(_decision("git push origin master"), prompt_fn=lambda _: "yes"), "approve")

    def test_block_alias(self):
        self.assertEqual(cli._prompt_approval(_decision("git push origin master"), prompt_fn=lambda _: "b"), "block")
        self.assertEqual(cli._prompt_approval(_decision("git push origin master"), prompt_fn=lambda _: "NO"), "block")

    def test_skip_on_empty(self):
        self.assertEqual(cli._prompt_approval(_decision("git push origin master"), prompt_fn=lambda _: ""), "skip")
        self.assertEqual(cli._prompt_approval(_decision("git push origin master"), prompt_fn=lambda _: "s"), "skip")

    def test_retries_until_valid_input(self):
        answers = iter(["wat", "x", "approve"])
        got = cli._prompt_approval(_decision("git push origin master"), prompt_fn=lambda _: next(answers))
        self.assertEqual(got, "approve")


class PreseededApprovalsTest(unittest.TestCase):
    def test_loads_valid_map(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"git push origin master": "block", "npm install": "approve"}, fh)
            loaded = cli._load_preseeded_approvals(path)
        self.assertEqual(loaded["git push origin master"], "block")
        self.assertEqual(loaded["npm install"], "approve")

    def test_ignores_malformed_entries(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"x": "maybe", "y": "approve"}, fh)
            loaded = cli._load_preseeded_approvals(path)
        self.assertEqual(loaded, {"y": "approve"})

    def test_non_object_root_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('["approve"]')
            loaded = cli._load_preseeded_approvals(path)
        self.assertEqual(loaded, {})

    def test_missing_file_is_empty(self):
        self.assertEqual(cli._load_preseeded_approvals(os.path.join("Z:", "nope", "a.json")), {})


class ApproveDecisionsTest(unittest.TestCase):
    def test_preseeded_block_aborts_without_prompt(self):
        decisions = [_decision("git push origin master")]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"git push origin master": "block"}, fh)
            log = policy.ApprovalLog()
            blocked = cli._approve_decisions(
                decisions, log, approval_file=path, prompt_fn=lambda _: (_ for _ in ()).throw(AssertionError("should not prompt"))
            )
        self.assertTrue(blocked)
        self.assertEqual(log.entries[0]["human_decision"], "block")

    def test_prompt_approve_continues_and_records(self):
        decisions = [_decision("git push origin master")]
        log = policy.ApprovalLog()
        blocked = cli._approve_decisions(decisions, log, prompt_fn=lambda _: "approve")
        self.assertFalse(blocked)
        self.assertEqual(log.entries[0]["decided_by"], "human")
        self.assertEqual(log.entries[0]["human_decision"], "approve")
        self.assertEqual(log.entries[0]["decision"], policy.ASK)

    def test_prompt_block_aborts(self):
        decisions = [_decision("git push origin master"), _decision("rm -rf x")]
        log = policy.ApprovalLog()
        blocked = cli._approve_decisions(decisions, log, prompt_fn=lambda _: "block")
        self.assertTrue(blocked)
        # Stops at the first block: the later decision is never reviewed.
        self.assertEqual(len(log.entries), 1)

    def test_allowed_decisions_never_prompted(self):
        decisions = [_decision("git status")]
        blocked = cli._approve_decisions(
            decisions, None, prompt_fn=lambda _: (_ for _ in ()).throw(AssertionError("should not prompt"))
        )
        self.assertFalse(blocked)


class ReviewEventsTest(unittest.TestCase):
    def test_asks_on_agent_command(self):
        events = [
            providers.ProviderEvent("tool", {"command": "pip install requests"}),
            providers.ProviderEvent("content.delta", {"text": "ok"}),
        ]
        log = policy.ApprovalLog()
        blocked = cli._review_events(events, log, prompt_fn=lambda _: "approve")
        self.assertFalse(blocked)
        self.assertEqual(len(log.entries), 1)
        self.assertEqual(log.entries[0]["command"], "pip install requests")
        self.assertEqual(log.entries[0]["decision"], policy.ASK)

    def test_deny_is_reviewable_and_blockable(self):
        # Review lets a human override a policy DENY too.
        events = [providers.ProviderEvent("tool", {"command": "rm -rf x"})]
        log = policy.ApprovalLog()
        blocked = cli._review_events(events, log, prompt_fn=lambda _: "approve")
        self.assertFalse(blocked)

        log2 = policy.ApprovalLog()
        blocked2 = cli._review_events(events, log2, prompt_fn=lambda _: "block")
        self.assertTrue(blocked2)


class DecisionsFromAuditTest(unittest.TestCase):
    def test_skips_human_rows_and_malformed(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": "t", "decided_by": "policy", "decision": "deny", "reason": "r", "command": "rm x"}) + "\n")
                fh.write(json.dumps({"ts": "t", "decided_by": "human", "human_decision": "approve", "decision": "ask", "reason": "r", "command": "git push origin master"}) + "\n")
                fh.write("not json\n")
                fh.write(json.dumps({"decided_by": "policy", "decision": "error", "reason": "r", "command": ""}) + "\n")
            decisions = cli._decisions_from_audit(path)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].command, "rm x")
        self.assertEqual(decisions[0].decision, policy.DENY)


class ReplayCLITest(unittest.TestCase):
    def _audit(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": "t", "decided_by": "policy", "decision": "ask", "reason": "r", "command": "git push origin master"}) + "\n")
        return path

    def test_replay_prompt_approve_exit_zero(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._audit(os.path.join(d, "t.jsonl"))
            with mock.patch.object(cli, "_prompt_approval", return_value="approve"):
                code = cli.main(["--review", path])
        self.assertEqual(code, 0)

    def test_replay_prompt_block_exit_two(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._audit(os.path.join(d, "t.jsonl"))
            with mock.patch.object(cli, "_prompt_approval", return_value="block"):
                code = cli.main(["--review", path])
        self.assertEqual(code, 2)

    def test_replay_preseeded_block_no_prompt_exit_two(self):
        with tempfile.TemporaryDirectory() as d:
            audit = self._audit(os.path.join(d, "t.jsonl"))
            ap = os.path.join(d, "a.json")
            with open(ap, "w", encoding="utf-8") as fh:
                json.dump({"git push origin master": "block"}, fh)
            with mock.patch.object(
                cli, "_prompt_approval", side_effect=AssertionError("must not prompt")
            ):
                code = cli.main(["--review", audit, "--approval-file", ap])
        self.assertEqual(code, 2)

    def test_replay_appends_human_verdict_to_trail(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._audit(os.path.join(d, "t.jsonl"))
            with mock.patch.object(cli, "_prompt_approval", return_value="approve"):
                code = cli.main(["--review", path])
            with open(path, encoding="utf-8") as fh:
                lines = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1]["decided_by"], "human")
        self.assertEqual(lines[1]["human_decision"], "approve")

    def test_replay_will_not_prompt_human_only_trail(self):
        # A trail whose only records are existing HUMAN verdicts (machine
        # rows already reviewed) has nothing left to re-prompt.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": "t", "decided_by": "human", "human_decision": "approve", "decision": "ask", "reason": "r", "command": "git push origin master"}) + "\n")
            with mock.patch.object(
                cli, "_prompt_approval", side_effect=AssertionError("must not prompt")
            ):
                code = cli.main(["--review", path])
        self.assertEqual(code, 0)


class HandoverReviewCLITest(unittest.TestCase):
    def _driver(self, *commands):
        events = [
            providers.ProviderEvent("tool", {"command": cmd})
            for cmd in commands
        ] + [providers.ProviderEvent("content.delta", {"text": "ok"})]
        fake = mock.Mock(driver_kind="opencode")
        fake.dispatch.return_value = providers.DispatchResult(
            provider="opencode", model="m", events=events, exit_code=0
        )
        return fake

    def _run(self, fake, extra=()):
        with mock.patch.object(providers, "get_driver", return_value=fake), \
             mock.patch.object(providers, "write_context_file", return_value="/tmp/ctx.txt"), \
             mock.patch.object(cli.goline_context, "build_context", return_value="PACK"):
            return cli.main([
                "--handover", "--provider", "opencode", "--context", "engine",
                "--review", *extra, "--", "hello"
            ])

    def test_ask_approved_run_continues(self):
        fake = self._driver("git push origin master", "git status")
        with mock.patch.object(cli, "_prompt_approval", return_value="approve"):
            code = self._run(fake)
        self.assertEqual(code, 0)
        fake.dispatch.assert_called_once()

    def test_ask_blocked_aborts_with_exit_two(self):
        fake = self._driver("npm install")
        with mock.patch.object(cli, "_prompt_approval", return_value="block"):
            code = self._run(fake)
        self.assertEqual(code, 2)

    def test_guard_still_aborts_deny_before_review_prompts(self):
        fake = self._driver("git reset --hard HEAD", "git status")
        with mock.patch.object(
            cli, "_prompt_approval", side_effect=AssertionError("must not prompt")
        ):
            with mock.patch.object(providers, "get_driver", return_value=fake), \
                 mock.patch.object(providers, "write_context_file", return_value="/tmp/ctx.txt"), \
                 mock.patch.object(cli.goline_context, "build_context", return_value="PACK"):
                code = cli.main([
                    "--handover", "--provider", "opencode", "--context", "engine",
                    "--guard", "--review", "--", "hello"
                ])
        self.assertEqual(code, 2)

    def test_review_records_approvals_to_audit_trail(self):
        with tempfile.TemporaryDirectory() as d:
            audit = os.path.join(d, "handover.jsonl")
            fake = self._driver("git push origin master")
            with mock.patch.object(cli, "_prompt_approval", return_value="approve"), \
                 mock.patch.object(providers, "get_driver", return_value=fake), \
                 mock.patch.object(providers, "write_context_file", return_value="/tmp/ctx.txt"), \
                 mock.patch.object(cli.goline_context, "build_context", return_value="PACK"):
                code = cli.main([
                    "--handover", "--provider", "opencode", "--context", "engine",
                    "--review", "--audit", audit, "--", "hello"
                ])
            with open(audit, encoding="utf-8") as fh:
                lines = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual(code, 0)
        kinds = [l["decided_by"] for l in lines]
        self.assertEqual(kinds, ["policy", "human"])
        self.assertEqual(lines[1]["human_decision"], "approve")


if __name__ == "__main__":
    unittest.main()