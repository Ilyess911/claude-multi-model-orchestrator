"""Tests for bin/scope-check.py and the --scope flag of bin/codex-worker.sh.

Temporary git repositories only. The worker runs against a fake `codex` executable put
first on PATH and a fake HOME with a ChatGPT login, so no model is ever called.
Run: python3 -m unittest            (from the repository root)
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "..", "bin")
SCOPE = os.path.join(BIN, "scope-check.py")
WORKER = os.path.join(BIN, "codex-worker.sh")

# Fake codex: writes the files named in FAKE_WRITES (relative to -C), commits when
# FAKE_COMMIT=1, answers "done" through -o, prints one usage event like `codex exec --json`.
FAKE_CODEX = r'''#!/bin/sh
OUT=""; CD="."
while [ $# -gt 0 ]; do
  case "$1" in -o) OUT="$2"; shift 2 ;; -C) CD="$2"; shift 2 ;; *) shift ;; esac
done
cat > /dev/null
for f in $FAKE_WRITES; do mkdir -p "$(dirname "$CD/$f")"; echo changed >> "$CD/$f"; done
[ "$FAKE_COMMIT" = 1 ] && git -C "$CD" commit -qam fake >/dev/null 2>&1
echo '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":0,"output_tokens":2,"reasoning_output_tokens":0}}'
[ -n "$OUT" ] && [ -z "$FAKE_NO_OUT" ] && echo done > "$OUT"
exit ${FAKE_RC:-0}
'''


def git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(self.repo, "src"))
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "demo@example.invalid")
        git(self.repo, "config", "user.name", "demo")
        for f in ("src/a.py", "src/b.py", "README.md", "setup.cfg"):
            self.write(f, "base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "init")

    def write(self, rel, text="changed\n", mode="w"):
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, mode) as f:
            f.write(text)


class ScopeCheckTest(RepoCase):
    def snap(self):
        path = os.path.join(self.tmp.name, "snap.json")
        with open(path, "w") as f:
            subprocess.run([sys.executable, SCOPE, "snapshot", self.repo], stdout=f, check=True)
        return path

    def check(self, snap, *scopes):
        r = subprocess.run([sys.executable, SCOPE, "check", self.repo, snap, *scopes],
                           capture_output=True, text=True)
        return r.returncode, [l.strip() for l in r.stdout.splitlines() if l.strip()]

    def test_in_scope_change_passes(self):
        s = self.snap()
        self.write("src/a.py")
        self.assertEqual(self.check(s, "src/a.py"), (0, []))

    def test_out_of_scope_edit_new_and_deleted(self):
        s = self.snap()
        self.write("src/a.py")
        self.write("README.md")
        self.write("notes/new.txt")
        os.remove(os.path.join(self.repo, "setup.cfg"))
        rc, lines = self.check(s, "src/a.py")
        self.assertEqual(rc, 1)
        self.assertEqual(lines, ["README.md  (modified)", "notes/new.txt  (new file)",
                                 "setup.cfg  (deleted)"])

    def test_directory_glob_and_spelling(self):
        s = self.snap()
        self.write("src/a.py")
        self.write("src/deep/c.py")
        self.write("README.md")
        self.assertEqual(self.check(s, "./src/", "*.md")[0], 0)
        self.assertEqual(self.check(s, "src/*.py", "README.md")[1], ["src/deep/c.py  (new file)"])
        self.assertEqual(self.check(s, "src/**/*.py", "README.md"), (0, []))
        self.assertEqual(self.check(s, "src/?.py", "src/**", "README.md"), (0, []))

    def test_scope_is_not_a_prefix_match(self):
        s = self.snap()
        self.write("src2/x.py")
        self.assertEqual(self.check(s, "src")[1], ["src2/x.py  (new file)"])

    def test_preexisting_dirty_file_is_not_blamed_but_edits_are(self):
        self.write("README.md", "user work in progress\n")
        s = self.snap()
        self.assertEqual(self.check(s, "src"), (0, []))
        self.write("README.md", "more\n", mode="a")
        self.assertEqual(self.check(s, "src")[1], ["README.md  (modified)"])

    def test_restoring_a_dirty_file_is_caught(self):
        self.write("README.md", "user work\n")
        s = self.snap()
        git(self.repo, "checkout", "--", "README.md")
        self.assertEqual(self.check(s, "src")[1], ["README.md  (restored or removed)"])

    def test_index_only_change_is_caught(self):
        self.write("README.md", "staged by the person\n")
        git(self.repo, "add", "README.md")
        self.write("README.md", "work tree edit\n")
        s = self.snap()
        with open(os.path.join(self.repo, "README.md")) as f:
            wt = f.read()
        self.write("README.md", "worker version\n")
        git(self.repo, "add", "README.md")
        self.write("README.md", wt)  # same work tree bytes as before, other staged blob
        self.assertEqual(self.check(s, "src")[1], ["README.md  (staged content changed)"])

    def test_head_moved(self):
        s = self.snap()
        self.write("src/a.py")
        git(self.repo, "commit", "-qam", "x")
        rc, lines = self.check(s, "src")
        self.assertEqual(rc, 1)
        self.assertIn("HEAD  (moved (commit, reset or checkout))", lines)

    def test_scopes_from_file(self):
        s = self.snap()
        self.write("src/a.py")
        self.write("README.md")
        lst = os.path.join(self.tmp.name, "scopes")
        with open(lst, "w") as f:
            f.write("src/a.py\n\nREADME.md\n")
        self.assertEqual(self.check(s, "@" + lst), (0, []))

    def test_not_a_repository(self):
        r = subprocess.run([sys.executable, SCOPE, "snapshot", self.tmp.name], capture_output=True)
        self.assertEqual(r.returncode, 2)


class WorkerScopeTest(RepoCase):
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(os.path.join(self.home, ".codex"))
        with open(os.path.join(self.home, ".codex", "auth.json"), "w") as f:
            json.dump({"auth_mode": "chatgpt"}, f)
        fake_bin = os.path.join(self.tmp.name, "fakebin")
        os.makedirs(fake_bin)
        codex = os.path.join(fake_bin, "codex")
        with open(codex, "w") as f:
            f.write(FAKE_CODEX)
        os.chmod(codex, os.stat(codex).st_mode | stat.S_IEXEC)
        self.env = dict(os.environ, HOME=self.home, PATH=fake_bin + os.pathsep + os.environ["PATH"])
        for k in ("OPENAI_API_KEY", "FAKE_WRITES", "FAKE_COMMIT", "FAKE_RC", "FAKE_NO_OUT"):
            self.env.pop(k, None)

    def run_worker(self, *args, writes="", cd=None, **env):
        e = dict(self.env, FAKE_WRITES=writes, **env)
        r = subprocess.run(["sh", WORKER, "--mode", "implement", "--cd", cd or self.repo, *args],
                           input="demo task", capture_output=True, text=True, env=e, timeout=60)
        return r.returncode, r.stdout, r.stderr.strip().splitlines()[-1]

    def test_in_scope(self):
        rc, out, status = self.run_worker("--scope", "src", writes="src/a.py")
        self.assertEqual(rc, 0)
        self.assertNotIn("[scope]", out)
        self.assertIn("status=ok exit=0", status)

    def test_out_of_scope_exits_5_and_keeps_changes(self):
        rc, out, status = self.run_worker("--scope", "src/a.py", writes="src/a.py README.md")
        self.assertEqual(rc, 5)
        self.assertIn("done", out)
        self.assertIn("README.md  (modified)", out)
        self.assertIn("Nothing was reverted", out)
        self.assertIn("status=scope exit=5 tokens=in:10,cache:0,out:2,think:0", status)
        with open(os.path.join(self.repo, "README.md")) as f:
            self.assertIn("changed", f.read())

    def test_commit_by_worker_is_caught(self):
        rc, out, _ = self.run_worker("--scope", "src", writes="src/a.py", FAKE_COMMIT="1")
        self.assertEqual(rc, 5)
        self.assertIn("HEAD", out)

    def test_failed_run_that_wrote_out_of_scope_exits_5(self):
        rc, _, status = self.run_worker("--scope", "src", writes="README.md", FAKE_RC="1", FAKE_NO_OUT="1")
        self.assertEqual(rc, 5)
        self.assertIn("status=scope exit=5", status)

    def test_scope_outside_git_is_refused(self):
        plain = os.path.join(self.tmp.name, "plain")
        os.makedirs(plain)
        rc, _, status = self.run_worker("--scope", "x", cd=plain)
        self.assertEqual(rc, 2)
        self.assertIn("status=fail exit=2", status)

    def test_without_scope_nothing_changes(self):
        rc, out, status = self.run_worker(writes="README.md")
        self.assertEqual(rc, 0)
        self.assertNotIn("[scope]", out)
        self.assertIn("status=ok", status)

    def test_review_mode_ignores_scope(self):
        e = dict(self.env, FAKE_WRITES="")
        r = subprocess.run(["sh", WORKER, "--mode", "review", "--cd", self.tmp.name, "--scope", "x"],
                           input="demo", capture_output=True, text=True, env=e, timeout=60)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
