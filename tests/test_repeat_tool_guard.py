"""Tests for hooks/repeat_tool_guard.py. State goes to a temporary directory.

Run: python3 -m unittest            (from the repository root)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "repeat_tool_guard.py")
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))

import repeat_tool_guard as g  # noqa: E402


def pre(tool, inp, sid="s1", agent=None):
    d = {"hook_event_name": "PreToolUse", "session_id": sid, "tool_name": tool, "tool_input": inp}
    if agent:
        d["agent_id"] = agent
    return d


def kind(out):
    """None, 'remind', 'warn' or 'deny' from a hook output."""
    if not out:
        return None
    h = out["hookSpecificOutput"]
    if h.get("permissionDecision") == "deny":
        return "deny"
    return "warn" if "bloquée" in h["additionalContext"] else "remind"


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = mock.patch.dict(os.environ, {"REPEAT_GUARD_STATE_DIR": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_n(self, n, tool="Bash", inp=None, **kw):
        inp = inp or {"command": "npm test"}
        return [kind(g.handle(pre(tool, inp, **kw))) for _ in range(n)]

    def test_two_identical_calls_are_silent(self):
        self.assertEqual(self.run_n(2), [None, None])

    def test_third_call_reminds(self):
        self.assertEqual(self.run_n(3)[-1], "remind")

    def test_fifth_call_warns(self):
        self.assertEqual(self.run_n(5), [None, None, "remind", None, "warn"])

    def test_eighth_call_denied_and_stays_denied(self):
        out = self.run_n(9)
        self.assertEqual(out[5:], [None, None, "deny", "deny"])

    def test_messages_reach_claude(self):
        outs = [g.handle(pre("Bash", {"command": "ls"})) for _ in range(8)]
        self.assertIn("Vérifie si le résultat change", outs[2]["hookSpecificOutput"]["additionalContext"])
        self.assertIn("Change de stratégie", outs[4]["hookSpecificOutput"]["additionalContext"])
        self.assertIn("blocked", outs[7]["hookSpecificOutput"]["permissionDecisionReason"])

    def test_different_args_never_alert(self):
        outs = [kind(g.handle(pre("Read", {"file_path": f"/work/f{i}.py"}))) for i in range(10)]
        self.assertEqual(outs, [None] * 10)

    def test_pagination_never_alerts(self):
        outs = [kind(g.handle(pre("Read", {"file_path": "/work/big.py", "offset": i * 200, "limit": 200})))
                for i in range(10)]
        self.assertEqual(outs, [None] * 10)

    def test_changing_test_inputs_never_alert(self):
        outs = [kind(g.handle(pre("Bash", {"command": f"python3 -m unittest -k case{i}"})))
                for i in range(10)]
        self.assertEqual(outs, [None] * 10)

    def test_interleaved_call_breaks_chain(self):
        for _ in range(4):
            g.handle(pre("Read", {"file_path": "/work/a.py"}))
            g.handle(pre("Edit", {"file_path": "/work/a.py", "old_string": "x", "new_string": "y"}))
        self.assertIsNone(kind(g.handle(pre("Read", {"file_path": "/work/a.py"}))))

    def test_normalization_catches_cosmetic_variants(self):
        variants = [{"command": "npm test", "description": "run tests"},
                    {"command": "npm   test ", "description": "again"},
                    {"command": "npm test;"}]
        outs = [kind(g.handle(pre("Bash", v))) for v in variants]
        self.assertEqual(outs, [None, None, "remind"])

    def test_timeout_change_is_a_new_call(self):
        self.run_n(7, inp={"command": "npm test", "timeout": 60000})
        self.assertIsNone(kind(g.handle(pre("Bash", {"command": "npm test", "timeout": 600000}))))

    def test_whitespace_is_exact_outside_bash(self):
        outs = [kind(g.handle(pre("Edit", {"file_path": "/w/a.py", "old_string": "a" + " " * i,
                                           "new_string": "b"}))) for i in range(1, 10)]
        self.assertEqual(outs, [None] * 9)

    def test_polling_prompt_lifts_the_deny_only(self):
        g.handle({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                  "prompt": "Surveille le run CI jusqu'à ce qu'il finisse"})
        self.assertEqual(self.run_n(9), [None, None, "remind", None, "warn", None, None, None, None])
        g.handle({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "Corrige le bug"})
        self.assertEqual(self.run_n(8)[-1], "deny")

    def test_poll_words_match_whole_words(self):
        for text in ("poll the job", "attends la fin", "wait until done", "toutes les 5 minutes"):
            self.assertTrue(g.POLL_RE.search(text), text)
        for text in ("le résultat attendu", "fix the parser"):
            self.assertFalse(g.POLL_RE.search(text), text)

    def test_polling_tools_exempt(self):
        outs = [kind(g.handle(pre("Monitor", {"until": "done"}))) for _ in range(10)]
        self.assertEqual(outs, [None] * 10)

    def test_repeat_ok_marker_exempts_bash(self):
        outs = self.run_n(10, inp={"command": "gh run view 1  # repeat-ok"})
        self.assertEqual(outs, [None] * 10)

    def test_user_prompt_resets(self):
        self.run_n(7)
        g.handle({"hook_event_name": "UserPromptSubmit", "session_id": "s1"})
        self.assertEqual(self.run_n(2), [None, None])

    def test_long_pause_resets(self):
        now = time.time()
        for i in range(7):
            g.handle(pre("Bash", {"command": "ls"}), now=now + i)
        self.assertIsNone(kind(g.handle(pre("Bash", {"command": "ls"}), now=now + 7 + g.CHAIN_GAP + 1)))

    def test_sessions_and_subagents_are_separate(self):
        self.run_n(4, sid="s1")
        self.assertEqual(self.run_n(2, sid="s2"), [None, None])
        self.assertEqual(self.run_n(2, agent="sub-1"), [None, None])

    def test_state_is_bounded(self):
        old = time.time() - g.MAX_AGE - 10
        for i in range(g.MAX_FILES + 50):
            p = os.path.join(self.tmp.name, f"old{i}.json")
            open(p, "w").write("{}")
            os.utime(p, (old, old))
        g.handle(pre("Bash", {"command": "ls"}, sid="fresh"))
        files = [f for f in os.listdir(self.tmp.name) if f.endswith(".json")]
        self.assertEqual(files, ["fresh.json"])
        for i in range(g.MAX_AGENTS + 10):
            g.handle(pre("Bash", {"command": "ls"}, sid="fresh", agent=f"a{i}"))
        with open(os.path.join(self.tmp.name, "fresh.json")) as f:
            self.assertLessEqual(len(json.load(f)["chains"]), g.MAX_AGENTS)

    def test_script_fails_open_and_is_silent(self):
        env = dict(os.environ)
        for stdin in ("not json", json.dumps(pre("Bash", {"command": "ls"}))):
            r = subprocess.run([sys.executable, HOOK], input=stdin, capture_output=True,
                               text=True, env=env, timeout=10)
            self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_script_denies_on_eighth_call(self):
        env = dict(os.environ)
        data = json.dumps(pre("Bash", {"command": "ls"}, sid="cli"))
        for _ in range(8):
            r = subprocess.run([sys.executable, HOOK], input=data, capture_output=True,
                               text=True, env=env, timeout=10)
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_kill_switch(self):
        env = dict(os.environ, CLAUDE_REPEAT_GUARD="0")
        data = json.dumps(pre("Bash", {"command": "ls"}, sid="off"))
        for _ in range(9):
            r = subprocess.run([sys.executable, HOOK], input=data, capture_output=True,
                               text=True, env=env, timeout=10)
        self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
