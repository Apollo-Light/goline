"""Tests for goline.cli.debugging (Stage 6 AI-assisted debugging)."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from goline.cli import providers
from goline.cli import debugging


class SourceExtractionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "src")
        os.makedirs(self.src)
        self.player = os.path.join(self.src, "player.gd")
        with open(self.player, "w", encoding="utf-8") as fh:
            fh.write("extends CharacterBody2D\nfunc _ready():\n\tpass\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_extracts_file_reference(self):
        diag = '  File "src/player.gd", line 3, in _ready\n    pass\n'
        res = debugging._extract_source_path(diag, self.tmp.name)
        self.assertEqual(os.path.normpath(res), os.path.normpath(self.player))

    def test_extracts_absolute_existing_path(self):
        diag = f"Some error at {self.player}:12\n"
        res = debugging._extract_source_path(diag, self.tmp.name)
        self.assertEqual(res, self.player)

    def test_returns_none_when_file_missing(self):
        diag = '  File "src/ghost.gd", line 1\n'
        res = debugging._extract_source_path(diag, self.tmp.name)
        self.assertIsNone(res)

    def test_returns_none_without_root(self):
        res = debugging._extract_source_path("File src/player.gd line 3", None)
        self.assertIsNone(res)

    def test_ignores_non_source_extensions(self):
        diag = "file docs/readme.txt line 4\n"
        res = debugging._extract_source_path(diag, self.tmp.name)
        self.assertIsNone(res)


class BuildDebugContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "src")
        os.makedirs(self.src)
        self.player = os.path.join(self.src, "player.gd")
        with open(self.player, "w", encoding="utf-8") as fh:
            fh.write("extends CharacterBody2D\nfunc _ready():\n\tpass\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_includes_diagnostics(self):
        with mock.patch.object(debugging, "_detect_repo_root", return_value=self.tmp.name), \
             mock.patch.object(debugging.workflows, "_git_last_change", return_value=None):
            pack, src = debugging.build_debug_context("Null access on instance", source_root=self.tmp.name)
        self.assertIn("Null access on instance", pack)
        self.assertIsNone(src)

    def test_empty_diagnostics_handled(self):
        with mock.patch.object(debugging, "_detect_repo_root", return_value=self.tmp.name), \
             mock.patch.object(debugging.workflows, "_git_last_change", return_value=None):
            pack, _ = debugging.build_debug_context("   ", source_root=self.tmp.name)
        self.assertIn("(no diagnostics supplied)", pack)

    def test_truncates_long_diagnostics(self):
        long_diag = "x" * 20000
        with mock.patch.object(debugging, "_detect_repo_root", return_value=self.tmp.name):
            pack, _ = debugging.build_debug_context(long_diag, source_root=self.tmp.name,
                                                     diagnostics_chars=1000)
        self.assertIn("truncated", pack)
        self.assertIn("x" * 1000, pack)

    def test_includes_referenced_source_and_policy(self):
        diag = f'  at {self.player}:2\n'
        with mock.patch.object(debugging, "_detect_repo_root", return_value=self.tmp.name), \
             mock.patch.object(debugging.workflows, "_git_last_change", return_value=None):
            pack, src = debugging.build_debug_context(diag, source_root=self.tmp.name)
        self.assertEqual(src, self.player)
        self.assertIn("## Referenced source", pack)
        self.assertIn("extends CharacterBody2D", pack)
        self.assertIn("Agent permission policy", pack)

    def test_includes_git_metadata_when_available(self):
        diag = f'  at {self.player}:2\n'
        with mock.patch.object(debugging, "_detect_repo_root", return_value=self.tmp.name), \
             mock.patch.object(debugging.workflows, "_git_last_change",
                               return_value="abc1234 2026-09-07 Dev: msg"):
            pack, _ = debugging.build_debug_context(diag, source_root=self.tmp.name)
        self.assertIn("source_last_change: abc1234 2026-09-07 Dev: msg", pack)


class BuildDebugPromptTest(unittest.TestCase):
    def test_prompt_contains_diagnostics_and_instructions(self):
        prompt = debugging.build_debug_prompt("Null access", "/repo")
        self.assertIn("Null access", prompt)
        self.assertIn("Root cause", prompt)
        self.assertIn("DO NOT modify files", prompt)

    def test_prompt_defaults_repo(self):
        with mock.patch("os.getcwd", return_value="/cwd"):
            prompt = debugging.build_debug_prompt("err", None)
        self.assertIn("/cwd", prompt)


class DebugWorkflowE2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "src")
        os.makedirs(self.src)
        self.player = os.path.join(self.src, "player.gd")
        with open(self.player, "w", encoding="utf-8") as fh:
            fh.write("extends CharacterBody2D\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _fake_driver(self, text="Root cause: null instance."):
        fake = mock.Mock(driver_kind="opencode")
        fake.dispatch.return_value = providers.DispatchResult(
            provider="opencode", model="m",
            events=[providers.ProviderEvent("content.delta", {"text": text})],
            exit_code=0,
        )
        return fake

    def test_success_returns_zero(self):
        with mock.patch.object(providers, "get_driver", return_value=self._fake_driver()), \
             mock.patch.object(providers, "write_context_file", return_value="/tmp/ctx.txt"), \
             mock.patch("builtins.print"):
            code = debugging.run_debug_workflow(
                "Null access on instance", provider="opencode",
                guard=False, workdir=self.tmp.name,
            )
        self.assertEqual(code, 0)

    def test_empty_diagnostics_returns_one(self):
        with mock.patch("builtins.print"):
            code = debugging.run_debug_workflow("   ", guard=False)
        self.assertEqual(code, 1)

    def test_guard_aborts_on_deny(self):
        events = [providers.ProviderEvent("tool", {"command": "rm -rf /tmp/x"})]
        fake = mock.Mock(driver_kind="opencode")
        fake.dispatch.return_value = providers.DispatchResult(
            provider="opencode", model="m", events=events, exit_code=0,
        )
        with mock.patch.object(providers, "get_driver", return_value=fake), \
             mock.patch.object(providers, "write_context_file", return_value="/tmp/ctx.txt"), \
             mock.patch("builtins.print"):
            code = debugging.run_debug_workflow(
                "Null access", provider="opencode", guard=True, workdir=self.tmp.name,
            )
        self.assertEqual(code, 2)

    def test_unknown_provider_returns_one(self):
        with mock.patch.object(providers, "get_driver", side_effect=ValueError("no such provider")), \
             mock.patch("builtins.print"):
            code = debugging.run_debug_workflow("err", provider="nope", guard=False,
                                                workdir=self.tmp.name)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()