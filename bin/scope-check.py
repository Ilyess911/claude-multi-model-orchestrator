#!/usr/bin/env python3
"""Write-scope check for codex-worker.sh --mode implement.

  scope-check.py snapshot <repo_dir>                  JSON state of the work tree on stdout
  scope-check.py check <repo_dir> <snapshot> <scope>... out-of-scope changes since the snapshot
  (a scope argument `@FILE` reads one scope per line from FILE)

The snapshot records HEAD and, for every path git reports as changed or untracked, its
status code, a hash of the work-tree content and the staged blob id, so a change to the
index alone (staging over the person's staged work) is caught too. `check` takes a second snapshot and lists every path whose
entry appeared, disappeared or changed, then keeps those outside the allowed scopes.
Files that were already dirty before the run are compared by content, so a pre-existing
uncommitted change is never blamed on the worker, and an edit to it is still caught.

A scope is a path relative to the repository root: an exact file, a directory (covers
everything below it) or a glob. In a glob `*` and `?` stay inside one directory level and
`**` crosses levels: `src/*.py` does not cover `src/deep/c.py`, `src/**/*.py` does. `check` prints one line per
violation and exits 1 when there is any, 0 otherwise. Nothing is ever reverted.
Not visible to git, so not checked: gitignored files and paths outside the repository.
"""
import hashlib
import json
import os
import re
import subprocess
import sys


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, check=True).stdout


def snapshot(repo):
    """{'head': sha or None, 'files': {path: [status, content hash or None]}}."""
    root = git(repo, "rev-parse", "--show-toplevel").decode().strip()
    try:
        head = git(root, "rev-parse", "--verify", "-q", "HEAD").decode().strip() or None
    except subprocess.CalledProcessError:
        head = None  # repository without a commit yet
    staged = {}  # path -> "mode blob" of the index entry, for paths staged against HEAD
    diff = git(root, "diff", "--cached", "--raw", "-z", "--no-abbrev", "--no-renames").split(b"\0")
    for meta, path in zip(diff[0::2], diff[1::2]):
        f = meta.decode().split()
        if len(f) >= 4:
            staged[path.decode("utf-8", "surrogateescape")] = f[1] + " " + f[3]
    files = {}
    raw = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    for entry in raw.split(b"\0"):
        if len(entry) < 4:
            continue
        code, path = entry[:2].decode(), entry[3:].decode("utf-8", "surrogateescape")
        full = os.path.join(root, path)
        digest = None
        if os.path.isfile(full) and not os.path.islink(full):
            with open(full, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
        elif os.path.islink(full):
            digest = "link:" + os.readlink(full)
        files[path] = [code, digest, staged.get(path)]
    return {"root": root, "head": head, "files": files}


def norm_scope(s):
    s = s.strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s.rstrip("/")


def glob_re(pattern):
    """Regex for a scope glob: `**` crosses directories, `*` and `?` do not."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def in_scope(path, scopes):
    for s in scopes:
        if not s:
            continue
        if path == s or path.startswith(s + "/"):
            return True
        if any(c in s for c in "*?") and glob_re(s).match(path):
            return True
    return False


def violations(before, after, scopes):
    """Sorted [(path, what)] of changes since `before` that fall outside `scopes`."""
    scopes = [norm_scope(s) for s in scopes]
    out = []
    b, a = before["files"], after["files"]
    for path in sorted(set(b) | set(a)):
        if b.get(path) == a.get(path) or in_scope(path, scopes):
            continue
        if path not in b:
            what = ("new file" if a[path][0] == "??" else
                    "deleted" if a[path][1] is None else "modified")
        elif path not in a:
            what = "restored or removed"
        elif a[path][1] is None:
            what = "deleted"
        elif b[path][1] == a[path][1]:
            what = "staged content changed"
        else:
            what = "modified"
        out.append((path, what))
    if before.get("head") != after.get("head"):
        out.append(("HEAD", "moved (commit, reset or checkout)"))
    return out


def main(argv):
    if len(argv) >= 2 and argv[0] == "snapshot":
        json.dump(snapshot(argv[1]), sys.stdout)
        return 0
    if len(argv) >= 3 and argv[0] == "check":
        with open(argv[2], encoding="utf-8") as f:
            before = json.load(f)
        scopes = []
        for a in argv[3:]:
            if a.startswith("@"):
                with open(a[1:], encoding="utf-8") as f:
                    scopes += [l for l in f.read().splitlines() if l.strip()]
            else:
                scopes.append(a)
        found = violations(before, snapshot(argv[1]), scopes)
        for path, what in found:
            print(f"  {path}  ({what})")
        return 1 if found else 0
    sys.stderr.write(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (subprocess.CalledProcessError, OSError, ValueError) as err:
        sys.stderr.write(f"scope-check: {type(err).__name__}: {err}\n")
        sys.exit(2)
