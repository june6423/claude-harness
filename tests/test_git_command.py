import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness.cli import main
from harness.store import init_data_root


def git(*args, cwd=None):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


class GitCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = init_data_root(base / "data", write_pointer=False)
        self.origin = base / "origin.git"
        git("init", "-q", "--bare", "-b", "main", str(self.origin))
        self.repo = self.root / "workspace" / "demo"
        git("clone", "-q", str(self.origin), str(self.repo))
        (self.repo / "a.txt").write_text("a")
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "a", cwd=self.repo)
        config = {"workspaces": {"demo": {"path": "workspace/demo", "remote": str(self.origin)}}}
        (self.root / "config.json").write_text(json.dumps(config))

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        return main(["--data-root", str(self.root), "git", str(self.repo), *args])

    def test_push_and_ls_remote(self):
        self.assertEqual(self.run_cli("push", "main"), 0)
        head = git("rev-parse", "HEAD", cwd=self.repo).strip()
        self.assertIn(head, git("--git-dir", str(self.origin), "rev-parse", "main"))

    def test_force_and_delete_are_refused(self):
        for args in (("push", "--force", "main"), ("push", "+main"), ("push", ":main"), ("push", "--delete", "main"),
                     ("push", "--mirror")):
            self.assertEqual(self.run_cli(*args), 2, args)

    def test_changed_origin_is_refused(self):
        git("remote", "set-url", "origin", "https://example.com/x.git", cwd=self.repo)
        self.assertEqual(self.run_cli("ls-remote"), 2)

    def test_unconfigured_repository_is_refused(self):
        other = self.root / "workspace" / "other"
        git("init", "-q", str(other))
        self.assertEqual(main(["--data-root", str(self.root), "git", str(other), "fetch"]), 2)


if __name__ == "__main__":
    unittest.main()
