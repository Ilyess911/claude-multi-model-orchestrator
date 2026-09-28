"""Snapshot and value tests for hooks/session_receipt.py on synthetic transcripts.

Run: python3 -m unittest            (from the repository root)
Refresh snapshots after an intended change: UPDATE_SNAPSHOTS=1 python3 -m unittest
No network: the Telegram path is only rendered, never sent.
"""
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
sys.path.insert(0, HERE)

import fixtures  # noqa: E402
import session_receipt as sr  # noqa: E402

SNAP = os.path.join(HERE, "snapshots")
SID = "fixture0-0000-0000-0000-000000000000"
CLOCK = re.compile(r"\d{2}/\d{2}(/\d{4}   | · )\d{2}:\d{2}(:\d{2})?")


def stable(text):
    """Mask the wall-clock time printed in the ticket and the Telegram header."""
    return CLOCK.sub("<now>", text)


def summary_json(st):
    st = dict(st, unpriced=sorted(st["unpriced"]),
              equiv=round(st["equiv"], 6), real=round(st["real"], 6),
              bill={k: {f: [n, round(v, 6)] for f, (n, v) in b.items()} for k, b in st["bill"].items()})
    return json.dumps(st, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


class ReceiptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = fixtures.write_all(cls.tmp.name)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        os.environ.pop("ANTHROPIC_API_KEY", None)
        cls.addClassCleanup(env.stop)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def st(self, name):
        return sr.summarize(self.paths[name])

    # --- snapshots ------------------------------------------------------------------------
    def check_snapshot(self, fname, actual):
        path = os.path.join(SNAP, fname)
        if os.environ.get("UPDATE_SNAPSHOTS") == "1" or not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(actual)
            return
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), actual, f"snapshot {fname} differs; "
                             "rerun with UPDATE_SNAPSHOTS=1 if the change is intended")

    def test_snapshots(self):
        for name, path in self.paths.items():
            with self.subTest(fixture=name):
                st = sr.summarize(path)
                self.check_snapshot(f"{name}.summary.json", summary_json(st))
                self.check_snapshot(f"{name}.ticket.txt", stable(sr.render(path, SID, st=st)) + "\n")
                self.check_snapshot(f"{name}.telegram.txt", stable(sr.telegram_text(st, SID)) + "\n")

    # --- transcript parsing ---------------------------------------------------------------
    def test_streamed_message_counted_once(self):
        st = self.st("direct")
        self.assertEqual(st["models"], {"claude-opus-5-5": 2})
        self.assertEqual(st["tool_counts"], {"Read": 1, "Skill": 1})
        self.assertEqual(st["skills"], {"demo-skill": 1})

    def test_prompt_detection_skips_meta_commands_results_summaries(self):
        self.assertEqual(sum(self.st("mixed")["routes"].values()), 3)

    def test_duration_spans_subagent_files(self):
        self.assertEqual(self.st("direct")["dur"], "1min05")
        self.assertEqual(self.st("mixed")["secs"], 3600)

    # --- routing --------------------------------------------------------------------------
    def routes(self, name):
        return {k: v for k, v in self.st(name)["routes"].items() if v}

    def test_direct(self):
        self.assertEqual(self.routes("direct"), {"DIRECT": 1})

    def test_codex_delegation_ignores_mentions(self):
        st = self.st("codex")
        self.assertEqual((st["codex"], st["gemini"]), (1, 0))
        self.assertEqual(self.routes("codex"), {"CODEX": 1})

    def test_gemini_delegation(self):
        st = self.st("gemini")
        self.assertEqual((st["codex"], st["gemini"]), (0, 1))
        self.assertEqual(self.routes("gemini"), {"GEMINI": 1})

    def test_cross_review(self):
        self.assertEqual(self.routes("cross_review"), {"REVIEW": 1})

    def test_parallel(self):
        self.assertEqual(self.routes("parallel"), {"PARALLEL": 1})

    def test_mixed_turns_and_subagents(self):
        st = self.st("mixed")
        self.assertEqual(self.routes("mixed"), {"DIRECT": 1, "CODEX": 1, "REVIEW": 1})
        self.assertEqual((st["codex"], st["jev"]), (2, 1))
        self.assertEqual(st["models"], {"claude-opus-5-5": 3, "claude-haiku-4-5": 1})

    # --- accounting -----------------------------------------------------------------------
    def test_token_and_cache_totals(self):
        st = self.st("direct")
        self.assertEqual((st["tin"], st["tout"], st["cr"], st["cw"]), (1010, 240, 11000, 300))

    def test_api_equivalent(self):
        # Opus 5.5: 4 in, 5 cache write 5m, 0.20 cache read, 20 out, per million.
        want = (1010 * 4 + 300 * 5 + 11000 * 0.2 + 240 * 20) / 1e6
        self.assertAlmostEqual(self.st("direct")["equiv"], want, places=9)

    def test_fast_geo_web_1h_cache_and_unknown_model(self):
        st = self.st("pricing")
        fast = 1.0 * 8 + 0.1 * 40                       # fast mode input and output
        geo = (1.0 * 4 + 1.0 * 8 + 50e-6 * 20) * 1.1 + 3 * 0.01  # US x1.1, 1h cache, 50 out, 3 searches
        self.assertAlmostEqual(st["equiv"], fast + geo, places=6)
        self.assertEqual(st["unpriced"], {"demo-model-unknown"})
        self.assertNotIn("<synthetic>", st["models"])

    # --- billing --------------------------------------------------------------------------
    def test_subscription_bills_nothing(self):
        st = self.st("direct")
        self.assertFalse(st["metered"])
        self.assertEqual(st["real"], 0.0)
        self.assertIn("aucune", sr.render(self.paths["direct"], SID, st=st))

    def test_api_key_is_metered(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "placeholder"}):
            st = self.st("direct")
        self.assertTrue(st["metered"])
        self.assertAlmostEqual(st["real"], st["equiv"])
        self.assertIn("OUI", sr.render(self.paths["direct"], SID, st=st))

    def test_jev_is_extra_billing(self):
        st = self.st("mixed")
        self.assertIn("Jev", sr.telegram_text(st, SID))
        self.assertIn("OUI", sr.render(self.paths["mixed"], SID, st=st))

    # --- Telegram payload -----------------------------------------------------------------
    def test_payload_carries_no_content(self):
        payload = sr.telegram_payload(self.st("mixed"), SID)
        for leak in ("demo task", "/work/demo", "synthetic reply", "codex-worker.sh"):
            self.assertNotIn(leak, payload)

    def test_headless_session_gets_no_telegram(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}):
            self.assertFalse(sr.interactive_exit({}, self.paths["direct"]))
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "cli"}):
            self.assertTrue(sr.interactive_exit({}, self.paths["direct"]))
            self.assertFalse(sr.interactive_exit({"reason": "clear"}, self.paths["direct"]))


if __name__ == "__main__":
    unittest.main()
