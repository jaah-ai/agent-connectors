"""The fixtures misbehave exactly as fixtures/README.md promises — and in no other way."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "tools"))

import check_connectors as cc  # noqa: E402
from fake_daemon import FakeDaemon, ok  # noqa: E402  (the SDK checkout's python/tests on PYTHONPATH)


class BrokenManifestTest(unittest.TestCase):
    def test_exactly_the_two_expected_errors(self):
        self.assertEqual(cc.validate(os.path.join(REPO, "fixtures", "broken-manifest")), [
            "`language` must be python or typescript, got ruby",
            "`entrypoint` main.rb is not in the agent root",
        ])


class ExitUnsettledTest(unittest.TestCase):
    def test_logs_one_line_and_exits_1_without_a_terminal_call(self):
        root = os.path.join(REPO, "fixtures", "exit-unsettled")
        self.assertEqual(cc.validate(root), [], "the manifest itself is valid")
        daemon = FakeDaemon(lambda verb, args: ok({"id": 1}))
        self.addCleanup(daemon.close)
        events = os.path.join(tempfile.mkdtemp(), "events.jsonl")
        env = {**os.environ, "JAAH_AGENT_SOCKET": daemon.path, "JAAH_AGENT_EVENTS": events}
        proc = subprocess.run([sys.executable, os.path.join(root, "main.py")], env=env, capture_output=True,
                              text=True, timeout=60)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(daemon.verbs(), ["task.get"], "connected, then no settle and no fail")
        with open(events, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh]
        self.assertEqual([(e["type"], e["level"]) for e in lines], [("log", "error")])
        self.assertIn("without settling", lines[0]["message"])


if __name__ == "__main__":
    unittest.main()
