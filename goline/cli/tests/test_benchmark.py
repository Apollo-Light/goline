"""Smoke tests for the Stage 9 benchmark harness (offline, deterministic)."""

from __future__ import annotations

import unittest

from goline.cli.benchmarks import benchmark


class BenchmarkHarnessTest(unittest.TestCase):
    def test_run_returns_expected_metrics_offline(self):
        # include_engine=False keeps the harness hermetic (no real git).
        results = benchmark.run(iterations=3, include_engine=False)
        for key in (
            "import_warm_ms",
            "import_cold_ms",
            "classify_us_per_op",
            "classify_ops_per_s",
            "classify_batch_p50_ms",
            "classify_batch_p95_ms",
            "classify_peak_mb",
            "scan_pipeline_p50_ms",
            "scan_pipeline_p95_ms",
            "parse_p50_us",
            "parse_p95_us",
        ):
            self.assertIsInstance(results[key], (int, float), key)
        self.assertEqual(results["engine_ctx_p50_ms"], None)

    def test_scan_pipeline_faster_than_classify_corpus_sloppily(self):
        # Just a sanity upper bound so a regression cannot silently hide.
        results = benchmark.run(iterations=3, include_engine=False)
        self.assertLess(results["scan_pipeline_p50_ms"], 1000.0)

    def test_game_ctx_measured_when_sample_project_present(self):
        results = benchmark.run(iterations=3, include_engine=False)
        # The bundled sample game always exists in a checkout; p50/p95 numeric.
        self.assertIsInstance(results["game_ctx_p50_ms"], float)
        self.assertIsInstance(results["game_ctx_p95_ms"], float)

    def test_render_produces_report(self):
        results = benchmark.run(iterations=3, include_engine=False)
        text = benchmark.render(results)
        self.assertIn("Stage 9 performance", text)
        self.assertIn("classify", text)
        self.assertIn("ms", text)


if __name__ == "__main__":
    unittest.main()