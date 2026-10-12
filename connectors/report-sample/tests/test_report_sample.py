"""Report sample against a fake node daemon (the SDK's own `fake_daemon`)."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)

import jaah_agent  # noqa: E402
from fake_daemon import FakeDaemon, ok  # noqa: E402

import main  # noqa: E402

TASK = {"id": 9, "title": "Summarise <Q3> churn", "body": "Explain churn & <script>alert(1)</script>.",
        "persona": "researcher", "autonomous": True, "out_of_scope": "Pricing."}


class ReportSampleTest(unittest.TestCase):
    def setUp(self):
        self.report_dir = tempfile.mkdtemp()
        self.inputs = []
        self.daemon = FakeDaemon(self.handle)
        self.addCleanup(self.daemon.close)
        patcher = mock.patch.dict(os.environ, {"JAAH_REPORT_DIR": self.report_dir})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.events = os.path.join(tempfile.mkdtemp(), "events.jsonl")

    def handle(self, verb, args):
        if verb == "agent.task":
            return ok({"task": TASK, "project": {"name": "acme"},
                       "agent": {"id": "a", "display_name": "Report sample", "version": "0.1.0", "commit": "c"},
                       "security_gate": "human"})
        if verb == "task.settle":
            return ok({"stored": True, "settled": True, "state": "done"})
        return ok({})

    def run_agent(self) -> str:
        agent = jaah_agent.connect(socket_path=self.daemon.path, events_path=self.events)
        return main.run(agent)

    def verbs(self):
        return [v for v, _ in self.daemon.calls]

    def test_uploads_the_report_then_settles_last(self):
        path = self.run_agent()
        self.assertEqual(self.verbs(), ["task.get", "agent.task", "task.artifact", "task.settle"])
        artifact = self.daemon.calls[2][1]
        self.assertEqual(artifact, {"kind": "report", "path": os.path.abspath(path)})
        self.assertEqual(os.path.dirname(path), self.report_dir, "written under $JAAH_REPORT_DIR")
        settle = self.daemon.calls[3][1]
        self.assertEqual(settle["terminal_type"], "no_pr")
        self.assertEqual(settle["no_pr_reason"], "no_code_needed")
        self.assertIn("byte HTML report", settle["no_pr_evidence"])

    def test_the_report_is_self_contained_escaped_html(self):
        with open(self.run_agent(), encoding="utf-8") as fh:
            page = fh.read()
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn("Summarise &lt;Q3&gt; churn", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>", page, "task text never becomes markup")
        self.assertNotIn("http", page.split("<main>")[0].replace("width=device-width", ""),
                         "no external asset: the Artifacts tab CSP blocks them")
        self.assertIn("Full Auto", page)
        self.assertIn("Pricing.", page)

    def test_input_documents_are_listed(self):
        ctx = {"task": TASK, "inputs": [{"kind": "prd", "name": "prd.md", "path": "/x/prd.md"}]}
        with open(main.write_report(ctx, self.report_dir), encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn("<li>prd: prd.md</li>", page)

    def test_a_long_body_is_truncated_and_an_empty_one_is_named(self):
        long_task = {**TASK, "body": "x" * (main.BODY_PREVIEW_CHARS + 10), "out_of_scope": ""}
        with open(main.write_report({"task": long_task}, self.report_dir), encoding="utf-8") as fh:
            self.assertIn("x…</pre>", fh.read())
        with open(main.write_report({"task": {**TASK, "body": ""}}, self.report_dir), encoding="utf-8") as fh:
            self.assertIn("The task has no body.", fh.read())

    def test_without_a_report_dir_a_dry_run_uses_a_temp_dir(self):
        with mock.patch.dict(os.environ, {"JAAH_REPORT_DIR": ""}):
            self.assertTrue(os.path.isdir(main._report_dir()))

    def test_main_outside_a_node_is_not_a_runner(self):
        env = {k: v for k, v in os.environ.items() if k != "JAAH_AGENT_SOCKET"}
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "main.py")], env=env, capture_output=True,
                              text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("NotARunner", proc.stderr)

    def test_a_refused_upload_ends_in_one_named_fail(self):
        from fake_daemon import err

        original = self.handle
        self.daemon.handler = lambda verb, args: (err("rejected", "HTTP 413 too large") if verb == "task.artifact"
                                                  else original(verb, args))
        with mock.patch.dict(os.environ, {"JAAH_AGENT_SOCKET": self.daemon.path, "JAAH_AGENT_EVENTS": self.events}):
            self.assertEqual(main.main(), 0)
        self.assertNotIn("task.settle", self.verbs())
        [reason] = [a["reason"] for v, a in self.daemon.calls if v == "task.fail"]
        self.assertIn("JaahRefused", reason)

    def test_main_entrypoint_runs_against_the_socket(self):
        with mock.patch.dict(os.environ, {"JAAH_AGENT_SOCKET": self.daemon.path, "JAAH_AGENT_EVENTS": self.events}):
            self.assertEqual(main.main(), 0)
        self.assertIn("task.settle", self.verbs())


if __name__ == "__main__":
    unittest.main()
