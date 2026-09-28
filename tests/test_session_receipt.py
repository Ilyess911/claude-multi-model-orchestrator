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

    # --- worker telemetry -----------------------------------------------------------------
    def w(self, name):
        return self.st(name)["workers"]

    def outcome(self, name, worker):
        o = self.w(name)[worker]
        return {k: o[k] for k in ("ok", "fail", "timeout", "unavailable", "scope", "unknown") if o[k]}

    def test_codex_success(self):
        ws = self.w("codex_success")
        self.assertEqual(self.outcome("codex_success", "codex"), {"ok": 1})
        self.assertEqual(ws["codex"]["tokens"], {"in": 1200, "cache": 800, "out": 300, "think": 50})
        self.assertEqual((ws["retry"], ws["fallback"]), (0, 0))

    def test_codex_failure_falls_back_to_claude(self):
        ws = self.w("codex_failure")
        self.assertEqual(self.outcome("codex_failure", "codex"), {"fail": 1})
        self.assertIsNone(ws["codex"]["tokens"])
        self.assertEqual((ws["retry"], ws["fallback"]), (0, 1))

    def test_codex_retry_then_success(self):
        ws = self.w("codex_retry")
        self.assertEqual(self.outcome("codex_retry", "codex"), {"ok": 1, "fail": 1})
        self.assertEqual((ws["retry"], ws["fallback"]), (1, 0))

    def test_gemini_success(self):
        ws = self.w("gemini_success")
        self.assertEqual(self.outcome("gemini_success", "gemini"), {"ok": 1})
        self.assertEqual(ws["gemini"]["tokens"], {"in": 5000, "cache": 0, "out": 400, "think": 100})

    def test_gemini_failure_and_timeout(self):
        self.assertEqual(self.outcome("gemini_failure", "gemini"), {"fail": 1})
        self.assertEqual(self.outcome("gemini_timeout", "gemini"), {"timeout": 1})
        self.assertEqual(self.w("gemini_timeout")["fallback"], 1)

    def test_unavailable_worker_falls_back_to_claude(self):
        ws = self.w("worker_fallback")
        self.assertEqual(self.outcome("worker_fallback", "codex"), {"unavailable": 1})
        self.assertEqual(ws["fallback"], 1)
        self.assertIn("1 FAIL", sr.render(self.paths["worker_fallback"], SID))

    def test_mixed_workers(self):
        ws = self.w("workers_mixed")
        self.assertEqual(self.outcome("workers_mixed", "codex"), {"ok": 2, "unknown": 2})
        self.assertEqual(self.outcome("workers_mixed", "gemini"), {"timeout": 1})
        self.assertEqual((ws["retry"], ws["fallback"]), (0, 0))
        self.assertEqual(ws["codex"]["tokens"]["in"], 2400)
        self.assertIn("(partiel)", sr.render(self.paths["workers_mixed"], SID))

    def test_review_edge_cases(self):
        ws = self.w("workers_edge")
        # 1: last line wins (fail); 2: raw run unknown; 3: two runs, one line: both unknown;
        # 4: codex ok after gemini fail.
        self.assertEqual(self.outcome("workers_edge", "codex"), {"ok": 1, "fail": 1, "unknown": 3})
        self.assertEqual(self.outcome("workers_edge", "gemini"), {"fail": 1})
        self.assertEqual(ws["fallback"], 1)  # only case 1
        self.assertEqual(ws["codex"]["tokens"]["in"], 1200)  # the quoted 999 is never counted

    def test_scope_violation(self):
        ws = self.w("codex_scope")
        self.assertEqual(self.outcome("codex_scope", "codex"), {"scope": 1})
        self.assertEqual(ws["fallback"], 1)
        self.assertIn("1 SCOPE", sr.render(self.paths["codex_scope"], SID))
        self.assertIn("Codex 1 SCOPE", sr.telegram_text(self.st("codex_scope"), SID).split("\n")[3])

    def test_no_status_line_means_unknown(self):
        self.assertEqual(self.outcome("codex", "codex"), {"unknown": 1})
        self.assertEqual(self.w("codex")["fallback"], 0)

    def test_quiet_session_shows_no_incident(self):
        self.assertEqual(sr.worker_problems(self.st("codex_success")), [])
        self.assertNotIn("🔴", sr.telegram_text(self.st("codex_success"), SID))
        self.assertNotIn("🔴", sr.telegram_text(self.st("direct"), SID))

    def test_failure_is_first_on_telegram(self):
        text = sr.telegram_text(self.st("codex_failure"), SID)
        first = text.split("\n")[3]
        self.assertIn("Codex 1 FAIL", first)
        self.assertIn("fallback Claude", first)

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


class ReceiptQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(sr, "QUEUE_DIR", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def queued(self):
        return sorted(os.listdir(self.tmp.name))

    def run_child(self, payload, send_results):
        sent = []

        def fake_send(p):
            sent.append(p["sid"])
            return send_results.pop(0)
        with mock.patch.object(sr, "send_payload", fake_send), \
             mock.patch.object(sr, "log_error"), \
             mock.patch.object(sys, "argv", ["x", "--telegram-send"]), \
             mock.patch.object(sys, "stdin", mock.Mock(read=lambda: json.dumps(payload))):
            sr.main()
        return sent

    def test_offline_ticket_is_queued_then_sent_in_order(self):
        self.assertEqual(self.run_child({"sid": "a"}, ["network URLError"]), ["a"])
        self.assertEqual(len(self.queued()), 1)
        # still offline: the queue fails first, the new ticket joins it without a send
        self.assertEqual(self.run_child({"sid": "b"}, ["network URLError"]), ["a"])
        self.assertEqual(len(self.queued()), 2)
        # back online: a, b, then the current one
        self.assertEqual(self.run_child({"sid": "c"}, ["", "", ""]), ["a", "b", "c"])
        self.assertEqual(self.queued(), [])

    def test_api_error_is_not_queued(self):
        self.run_child({"sid": "a"}, ["http 403"])
        self.assertEqual(self.queued(), [])

    def test_queue_is_capped_and_expires(self):
        for i in range(sr.QUEUE_MAX + 3):
            sr.queue_put({"sid": f"s{i}"})
        self.assertEqual(len(self.queued()), sr.QUEUE_MAX)
        old = os.path.join(self.tmp.name, self.queued()[0])
        os.utime(old, (0, 0))
        sent = []
        with mock.patch.object(sr, "send_payload", lambda p: sent.append(p["sid"]) or ""):
            sr.queue_flush()
        self.assertEqual(len(sent), sr.QUEUE_MAX - 1)
        self.assertEqual(self.queued(), [])

    def test_claimed_file_is_skipped(self):
        sr.queue_put({"sid": "a"})
        name = self.queued()[0]
        os.rename(os.path.join(self.tmp.name, name), os.path.join(self.tmp.name, name + ".sending"))
        with mock.patch.object(sr, "send_payload", side_effect=AssertionError):
            self.assertEqual(sr.queue_flush(), "")


if __name__ == "__main__":
    unittest.main()
