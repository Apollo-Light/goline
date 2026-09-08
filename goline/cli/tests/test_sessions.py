"""Tests for goline.cli session/thread persistence (Stage 4). Offline.

Session persistence lets a later --handover --continue resume the same
provider thread. Every test here injects a fake executor / driver; nothing
spawns a process or touches the network.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from goline.cli import goline_cli
from goline.cli import providers

SESSION_LINE = json.dumps(
    {"type": "step_start", "sessionID": "sess-2", "part": {"type": "step-start"}}
) + "\n"


def _make_proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _logical(argv):
    """Strip the wrapper prefix (e.g. powershell/cmd + script path) from an
    argv and return the CLI's own arguments."""
    if not argv:
        return []
    for probe in ("powershell", "cmd"):
        if os.path.basename(argv[0]).lower() == probe:
            for i, arg in enumerate(argv):
                if i > 0 and arg.lower().endswith((".ps1", ".cmd", ".bat")):
                    return argv[i + 1:]
            return argv[2:]
    return argv


class OpenCodeSessionArgvTest(unittest.TestCase):
    def _dispatch(self, **kwargs):
        calls = {}

        def fake_executor(argv, cwd):
            calls["argv"] = list(argv)
            return _make_proc(0, "")

        providers.OpenCodeDriver().dispatch(
            "prompt", None, workdir=tempfile.gettempdir(),
            executor=fake_executor, **kwargs
        )
        return calls["argv"]

    def test_no_session_argv_unchanged(self):
        argv = self._dispatch()
        logical = _logical(argv)
        self.assertNotIn("--session", logical)

    def test_session_flag_inserted_before_prompt(self):
        argv = self._dispatch(session="abc123")
        logical = _logical(argv)
        self.assertIn("--session", logical)
        self.assertEqual(logical[logical.index("--session") + 1], "abc123")
        self.assertLess(logical.index("--session"), logical.index("prompt"))


class ClaudeSessionArgvTest(unittest.TestCase):
    def _dispatch(self, **kwargs):
        calls = {}

        def fake_executor(argv, cwd):
            calls["argv"] = list(argv)
            return _make_proc(0, "")

        providers.ClaudeDriver().dispatch(
            "prompt", None, workdir=tempfile.gettempdir(),
            executor=fake_executor, **kwargs
        )
        return calls["argv"]

    def test_resume_flag_added_when_session_given(self):
        logical = _logical(self._dispatch(session="t1"))
        self.assertIn("--resume", logical)
        self.assertEqual(logical[logical.index("--resume") + 1], "t1")

    def test_no_resume_flag_when_no_session(self):
        self.assertNotIn("--resume", _logical(self._dispatch()))


class SessionPersistenceTest(unittest.TestCase):
    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            state = providers.SessionState(
                provider="opencode", model="m", session_id="s1",
                updated_at="2026-01-01T00:00:00+00:00",
            )
            self.assertTrue(providers.save_session(path, state))
            loaded = providers.load_session(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.provider, "opencode")
            self.assertEqual(loaded.model, "m")
            self.assertEqual(loaded.session_id, "s1")
            self.assertEqual(loaded.updated_at, state.updated_at)

    def test_load_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(providers.load_session(os.path.join(d, "nope.json")))

    def test_load_garbage_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("not json {{{")
            self.assertIsNone(providers.load_session(path))

    def test_load_wrong_shape_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"provider": "opencode"}, fh)
            self.assertIsNone(providers.load_session(path))

    def test_load_missing_fields_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"provider": "opencode", "model": "m", "session_id": ""}, fh)
            self.assertIsNone(providers.load_session(path))

    def test_session_id_from_events_returns_first(self):
        events = [
            providers.ProviderEvent("message.updated", {"role": "user"}),
            providers.ProviderEvent("step.start", {"session_id": "first"}),
            providers.ProviderEvent("content.delta", {"text": "x", "session_id": "second"}),
        ]
        self.assertEqual(providers.session_id_from_events(events), "first")

    def test_session_id_from_events_none(self):
        events = [
            providers.ProviderEvent("message.updated", {"role": "user"}),
            providers.ProviderEvent("content.delta", {"text": "x"}),
        ]
        self.assertIsNone(providers.session_id_from_events(events))

    def test_session_id_from_events_empty_list(self):
        self.assertIsNone(providers.session_id_from_events([]))


class ResumeSessionTest(unittest.TestCase):
    def test_matching_provider_returns_id(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            providers.save_session(
                path, providers.SessionState("opencode", "m", "abc", "")
            )
            self.assertEqual(goline_cli._resume_session(path, "opencode"), "abc")

    def test_provider_mismatch_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            providers.save_session(
                path, providers.SessionState("claude", "m", "abc", "")
            )
            self.assertIsNone(goline_cli._resume_session(path, "opencode"))

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(
                goline_cli._resume_session(os.path.join(d, "a.json"), "opencode")
            )

    def test_corrupt_file_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{oops")
            self.assertIsNone(goline_cli._resume_session(path, "opencode"))


class HandoverSessionCLITest(unittest.TestCase):
    def _run(self, args, proc_stdout=SESSION_LINE):
        calls = {}

        def fake_executor(argv, cwd):
            calls["argv"] = list(argv)
            return _make_proc(0, proc_stdout)

        def fake_write(pack):
            return "/tmp/ctx.txt"

        with mock.patch.object(providers, "_default_executor",
                               side_effect=fake_executor), \
             mock.patch.object(goline_cli.goline_context, "build_context",
                               return_value="PACK"), \
             mock.patch.object(providers, "write_context_file",
                               side_effect=fake_write):
            code = goline_cli.main(args)
        return code, calls.get("argv", [])

    def test_continue_resumes_recorded_session(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".goline"), exist_ok=True)
            sf = os.path.join(d, ".goline", "session.json")
            providers.save_session(
                sf, providers.SessionState("opencode", "opencode/gpt-4o", "sess-1", "")
            )
            code, argv = self._run([
                "--handover", "--provider", "opencode", "--context", "engine",
                "--continue", "--session-file", sf, "--", "follow up"
            ])
            self.assertEqual(code, 0)
            logical = _logical(argv)
            self.assertIn("--session", logical)
            self.assertEqual(logical[logical.index("--session") + 1], "sess-1")
            self.assertLess(logical.index("--session"), logical.index("follow up"))
            loaded = providers.load_session(sf)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.session_id, "sess-2")
            self.assertEqual(loaded.provider, "opencode")

    def test_continue_mismatched_provider_starts_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".goline"), exist_ok=True)
            sf = os.path.join(d, ".goline", "session.json")
            providers.save_session(
                sf, providers.SessionState("claude", "m", "claude-t1", "")
            )
            code, argv = self._run([
                "--handover", "--provider", "opencode", "--context", "engine",
                "--continue", "--session-file", sf, "--", "hello"
            ])
            self.assertEqual(code, 0)
            self.assertNotIn("--session", _logical(argv))
            loaded = providers.load_session(sf)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.provider, "opencode")
            self.assertEqual(loaded.session_id, "sess-2")

    def test_handover_persists_session_from_events(self):
        with tempfile.TemporaryDirectory() as d:
            sf = os.path.join(d, "fresh.json")
            code, _ = self._run([
                "--handover", "--provider", "opencode", "--context", "engine",
                "--session-file", sf, "--", "hello"
            ])
            self.assertEqual(code, 0)
            loaded = providers.load_session(sf)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.session_id, "sess-2")

    def test_handover_without_session_id_writes_no_file(self):
        with tempfile.TemporaryDirectory() as d:
            sf = os.path.join(d, "fresh.json")
            code, _ = self._run(["--handover", "--provider", "opencode",
                                 "--context", "engine", "--session-file", sf,
                                 "--", "hello"],
                                proc_stdout="")
            self.assertEqual(code, 0)
            self.assertFalse(os.path.exists(sf))

    def test_default_session_file_is_workdir_scoped(self):
        with tempfile.TemporaryDirectory() as d:
            code, _ = self._run(["--handover", "--provider", "opencode",
                                 "--context", "engine", "--project", d,
                                 "--", "hello"])
            self.assertEqual(code, 0)
            self.assertTrue(
                os.path.exists(os.path.join(d, ".goline", "session.json"))
            )


if __name__ == "__main__":
    unittest.main()