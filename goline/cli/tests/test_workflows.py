"""Tests for goline.cli.workflows (Stage 5+7 file-scoped context + coding workflows)."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from goline.cli import providers
from goline.cli import workflows


class BuildFileContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.src = os.path.join(self.root, "src")
        os.makedirs(self.src)
        with open(os.path.join(self.src, "player.gd"), "w", encoding="utf-8") as fh:
            fh.write("extends CharacterBody2D\n\nfunc _ready():\n\tpass\n")
        with open(os.path.join(self.src, "enemy.gd"), "w", encoding="utf-8") as fh:
            fh.write("extends CharacterBody2D\n\nfunc _ready():\n\tpass\n")
        with open(os.path.join(self.src, "scene.tscn"), "w", encoding="utf-8") as fh:
            fh.write('[gd_scene] resource_name="Main"\n\n[node name="Player" type="Node"]\n')
        self.player = os.path.join(self.src, "player.gd")

    def tearDown(self):
        self.tmp.cleanup()

    def test_includes_file_content(self):
        with mock.patch.object(workflows, "_git_last_change", return_value=None):
            pack = workflows.build_file_context(self.player, root=self.root)
        self.assertIn("extends CharacterBody2D", pack)
        self.assertIn("func _ready():", pack)

    def test_includes_line_count(self):
        with mock.patch.object(workflows, "_git_last_change", return_value=None):
            pack = workflows.build_file_context(self.player, root=self.root)
        self.assertIn("lines_shown: 4", pack)

    def test_includes_siblings(self):
        with mock.patch.object(workflows, "_git_last_change", return_value=None):
            pack = workflows.build_file_context(self.player, root=self.root)
        self.assertIn("enemy.gd", pack)
        self.assertIn("scene.tscn", pack)
        self.assertNotIn("player.gd", pack.split("siblings")[1])  # excluded from own siblings

    def test_includes_references(self):
        # enemy.gd references "CharacterBody2D" but not "player.gd" literally.
        # Create a file that references player.gd.
        with open(os.path.join(self.src, "main.tscn"), "w", encoding="utf-8") as fh:
            fh.write('[ext_resource path="res://src/player.gd" type="Script"]\n')
        with mock.patch.object(workflows, "_git_last_change", return_value=None):
            pack = workflows.build_file_context(self.player, root=self.root)
        self.assertIn("main.tscn", pack)

    def test_references_bounded(self):
        # Create more referencing files than the limit.
        for i in range(25):
            with open(os.path.join(self.src, f"ref{i}.gd"), "w", encoding="utf-8") as fh:
                fh.write(f'# see player.gd for details\n')
        with mock.patch.object(workflows, "_git_last_change", return_value=None):
            pack = workflows.build_file_context(
                self.player, root=self.root, ref_limit=5
            )
        self.assertIn("5 found", pack)

    def test_includes_git_metadata_when_available(self):
        with mock.patch.object(workflows, "_git_last_change", return_value="abc1234 2026-09-07 Author: msg"):
            pack = workflows.build_file_context(self.player, root=self.root)
        self.assertIn("last_change: abc1234 2026-09-07 Author: msg", pack)

    def test_includes_permission_policy(self):
        with mock.patch.object(workflows, "_git_last_change", return_value=None):
            pack = workflows.build_file_context(self.player, root=self.root)
        self.assertIn("Agent permission policy", pack)
        self.assertIn("MUST NOT run", pack)

    def test_missing_file_returns_error(self):
        pack = workflows.build_file_context("/no/such/file.gd")
        self.assertTrue(pack.startswith("ERROR:"))

    def test_line_limit_truncates_content(self):
        with open(os.path.join(self.src, "big.py"), "w", encoding="utf-8") as fh:
            for i in range(100):
                fh.write(f"line {i}\n")
        big = os.path.join(self.src, "big.py")
        with mock.patch.object(workflows, "_git_last_change", return_value=None):
            pack = workflows.build_file_context(big, root=self.root, line_limit=5)
        self.assertIn("lines_shown: 5", pack)
        self.assertIn("line 0", pack)
        self.assertNotIn("line 5", pack)


class SiblingFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for n in ("a.txt", "b.txt", "c.txt"):
            with open(os.path.join(self.tmp.name, n), "w") as fh:
                fh.write("x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_sorted_and_excludes(self):
        sibs = workflows._sibling_files(self.tmp.name, exclude="b.txt")
        self.assertEqual(sibs, ["a.txt", "c.txt"])

    def test_bounded(self):
        sibs = workflows._sibling_files(self.tmp.name, limit=2)
        self.assertEqual(len(sibs), 2)

    def test_bad_dir_returns_empty(self):
        self.assertEqual(workflows._sibling_files("/no/such/dir"), [])


class FindReferencesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with open(os.path.join(self.tmp.name, "target.py"), "w") as fh:
            fh.write("x")
        with open(os.path.join(self.tmp.name, "ref1.py"), "w") as fh:
            fh.write("# see target.py for details\n")
        with open(os.path.join(self.tmp.name, "ref2.py"), "w") as fh:
            fh.write("# no mention at all\n")
        with open(os.path.join(self.tmp.name, "self.py"), "w") as fh:
            fh.write("# also see target.py\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_mentions(self):
        refs = workflows._find_references("target.py", self.tmp.name)
        self.assertIn("ref1.py", refs)
        self.assertIn("self.py", refs)  # also mentions target.py
        self.assertNotIn("ref2.py", refs)  # no mention

    def test_exclude_skips_a_file(self):
        refs = workflows._find_references("target.py", self.tmp.name,
                                           exclude=os.path.join(self.tmp.name, "self.py"))
        self.assertIn("ref1.py", refs)
        self.assertNotIn("self.py", refs)

    def test_bounded(self):
        for i in range(10):
            with open(os.path.join(self.tmp.name, f"extra{i}.py"), "w") as fh:
                fh.write("# see target.py\n")
        refs = workflows._find_references("target.py", self.tmp.name, limit=3)
        self.assertEqual(len(refs), 3)

    def test_skips_hidden_dirs(self):
        hidden = os.path.join(self.tmp.name, ".hidden")
        os.makedirs(hidden)
        with open(os.path.join(hidden, "ref.py"), "w") as fh:
            fh.write("import target\n")
        refs = workflows._find_references("target.py", self.tmp.name)
        self.assertFalse(any(".hidden" in r for r in refs))


class GitLastChangeTest(unittest.TestCase):
    def test_returns_formatted_string(self):
        with mock.patch("subprocess.run") as m:
            m.return_value = mock.Mock(stdout="abc1234 2026-09-07 Dev: msg\n", returncode=0)
            result = workflows._git_last_change("/fake/path")
        self.assertEqual(result, "abc1234 2026-09-07 Dev: msg")

    def test_returns_none_on_failure(self):
        with mock.patch("subprocess.run", side_effect=OSError("no git")):
            result = workflows._git_last_change("/fake/path")
        self.assertIsNone(result)


class ValidateEditResultTest(unittest.TestCase):
    def test_valid_diff_passes(self):
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1,3 +1,3 @@\n-old\n+new\n"
        ok, warnings = workflows.validate_edit_result(diff)
        self.assertTrue(ok)
        self.assertEqual(warnings, [])

    def test_empty_rejects(self):
        ok, warnings = workflows.validate_edit_result("")
        self.assertFalse(ok)

    def test_no_diff_markers_warns(self):
        ok, warnings = workflows.validate_edit_result("Here is my explanation of the code...")
        self.assertTrue(ok)
        self.assertTrue(any("no unified-diff" in w for w in warnings))

    def test_huge_diff_rejects(self):
        hunk = "@@ -1,3 +1,3 @@\n line\n"
        diff = "--- a/x\n+++ b/x\n" + hunk * 2000
        ok, warnings = workflows.validate_edit_result(diff)
        self.assertFalse(ok)

    def test_large_diff_warns(self):
        hunk = "@@ -1,3 +1,3 @@\n line\n"
        diff = "--- a/x\n+++ b/x\n" + hunk * 60
        ok, warnings = workflows.validate_edit_result(diff)
        self.assertTrue(ok)
        self.assertTrue(any("hunks" in w for w in warnings))

    def test_diffgit_detected(self):
        diff = "diff --git a/x b/x\nindex abc..def 100644\n--- a/x\n+++ b/x\n"
        ok, warnings = workflows.validate_edit_result(diff)
        self.assertTrue(ok)
        self.assertFalse(any("no unified" in w for w in warnings))


class PromptBuildersTest(unittest.TestCase):
    def test_edit_prompt_contains_instruction_and_rules(self):
        prompt = workflows.build_edit_prompt("/src/foo.gd", "add _ready")
        self.assertIn("add _ready", prompt)
        self.assertIn("AI development rules", prompt)
        self.assertIn("/src/foo.gd", prompt)

    def test_explain_prompt_contains_context(self):
        prompt = workflows.build_explain_prompt("/src/foo.gd", "content here")
        self.assertIn("content here", prompt)
        self.assertIn("/src/foo.gd", prompt)


class CodeWorkflowE2ETest(unittest.TestCase):
    """Mock provider driver to test run_code_workflow end-to-end."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = os.path.join(self.tmp.name, "test.gd")
        with open(self.file, "w", encoding="utf-8") as fh:
            fh.write("extends Node\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _fake_driver(self, text="--- a/test.gd\n+++ b/test.gd\n@@ -1 +1 @@\n-old\n+new\n"):
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
            code = workflows.run_code_workflow(
                self.file, "add _ready", provider="opencode",
                guard=False, workdir=self.tmp.name,
            )
        self.assertEqual(code, 0)

    def test_missing_file_returns_one(self):
        code = workflows.run_code_workflow("/no/such.gd", "fix", guard=False)
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
            code = workflows.run_code_workflow(
                self.file, "fix", provider="opencode",
                guard=True, workdir=self.tmp.name,
            )
        self.assertEqual(code, 2)

    def test_empty_agent_output_returns_two(self):
        with mock.patch.object(providers, "get_driver", return_value=self._fake_driver("")), \
             mock.patch.object(providers, "write_context_file", return_value="/tmp/ctx.txt"), \
             mock.patch("builtins.print"):
            code = workflows.run_code_workflow(
                self.file, "fix", provider="opencode",
                guard=False, workdir=self.tmp.name,
            )
        self.assertEqual(code, 2)


class ExplainWorkflowE2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = os.path.join(self.tmp.name, "test.gd")
        with open(self.file, "w", encoding="utf-8") as fh:
            fh.write("extends Node\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_success_returns_zero(self):
        fake = mock.Mock(driver_kind="opencode")
        fake.dispatch.return_value = providers.DispatchResult(
            provider="opencode", model="m",
            events=[providers.ProviderEvent("content.delta", {"text": "This file does X."})],
            exit_code=0,
        )
        with mock.patch.object(providers, "get_driver", return_value=fake), \
             mock.patch.object(providers, "write_context_file", return_value="/tmp/ctx.txt"), \
             mock.patch("builtins.print"):
            code = workflows.run_explain_workflow(self.file, provider="opencode", workdir=self.tmp.name)
        self.assertEqual(code, 0)

    def test_missing_file_returns_one(self):
        code = workflows.run_explain_workflow("/no/such.gd")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()