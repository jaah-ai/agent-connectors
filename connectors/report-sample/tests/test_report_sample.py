"""Report sample against a fake node (tools/fake_node.py), under the SDK its generated folder carries."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [ROOT, os.path.join(ROOT, "..", "..", "tools")]
if not os.path.isfile(os.path.join(ROOT, "jaah_agent", "__init__.py")):
    raise unittest.SkipTest("this folder carries no SDK: run the Agent SDK's examples/vendor.py on it first")

import agent  # noqa: E402
from fake_node import FakeNode, refuse, settled  # noqa: E402

TASK = {"id": 9, "title": "Summarise <Q3> churn", "body": "Explain churn & <script>alert(1)</script>.",
        "project_id": 1, "persona": "researcher", "autonomous": True, "state": "active",
        "prerequisite_artifacts": [{"id": 3, "task_id": 8, "kind": "prd", "size_bytes": 1200}],
        "attachments": [{"id": 4, "task_id": 9, "filename": "notes.pdf", "content_type": "application/pdf",
                         "size_bytes": 99}]}
#: The bundle as a node runs it: `command` from this folder, nothing but this folder on the path.
COMMAND = [sys.executable, "-S", "-s", "-E", os.path.join(ROOT, "agent.py")]


class ReportSampleTest(unittest.TestCase):
    def setUp(self):
        self.report_dir = tempfile.mkdtemp()
        patcher = mock.patch.dict(os.environ, {"JAAH_REPORT_DIR": self.report_dir})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.node = FakeNode(self.handle)

    def handle(self, verb, params):
        if verb == "task.get":
            return TASK
        return settled(verb, params)

    def run_agent(self) -> None:
        asyncio.run(agent.ReportSample().prompt(self.node.turn("Work Jaah task 9.", cwd=self.report_dir)))

    def page(self) -> str:
        with open(os.path.join(self.report_dir, agent.REPORT_NAME), encoding="utf-8") as fh:
            return fh.read()

    def test_uploads_the_report_then_settles_last(self):
        self.run_agent()
        self.assertEqual(self.node.verbs(), ["task.get", "artifact.upload", "task.complete"])
        self.assertEqual(self.node.params("artifact.upload"),
                         [{"kind": "report", "path": os.path.join(self.report_dir, agent.REPORT_NAME)}])
        [settle] = self.node.params("task.complete")
        self.assertEqual(settle["terminal_type"], "no_pr")
        self.assertEqual(settle["no_pr_reason"], "no_code_needed")
        self.assertIn("byte HTML report", settle["no_pr_evidence"])
        self.assertIn("2 input document(s)", settle["no_pr_evidence"])

    def test_the_report_is_self_contained_escaped_html(self):
        self.run_agent()
        page = self.page()
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn("Summarise &lt;Q3&gt; churn", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>", page, "task text never becomes markup")
        self.assertNotIn("http", page.split("<main>")[0].replace("width=device-width", ""),
                         "no external asset: the Artifacts tab CSP blocks them")
        self.assertIn("Full Auto", page)
        self.assertIn("<dd>#1</dd>", page)

    def test_input_documents_are_listed(self):
        self.run_agent()
        page = self.page()
        self.assertIn("<li>prd: from task #8 (1200 bytes)</li>", page)
        self.assertIn("<li>file: notes.pdf</li>", page)

    def test_a_long_body_is_truncated_and_an_empty_one_is_named(self):
        long_task = {**TASK, "body": "x" * (agent.BODY_PREVIEW_CHARS + 10)}
        with open(agent.write_report(long_task, self.report_dir), encoding="utf-8") as fh:
            self.assertIn("x…</pre>", fh.read())
        with open(agent.write_report({**TASK, "body": "", "prerequisite_artifacts": [], "attachments": []},
                                     self.report_dir), encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn("The task has no body.", page)
        self.assertIn("None — this task has no prerequisite documents.", page)

    def test_without_a_report_dir_a_dry_run_uses_a_temp_dir(self):
        with mock.patch.dict(os.environ, {"JAAH_REPORT_DIR": ""}):
            self.assertTrue(os.path.isdir(agent._report_dir()))

    def test_under_a_plain_acp_client_it_says_so_and_calls_nothing(self):
        node = FakeNode(self.handle, verbs=())
        asyncio.run(agent.ReportSample().prompt(node.turn("hello", cwd=self.report_dir)))
        self.assertEqual(node.verbs(), [])
        self.assertIn("no task", node.said())

    def test_a_refused_upload_ends_in_one_named_fail(self):
        def handle(verb, params):
            if verb == "artifact.upload":
                raise refuse("HTTP 413 too large")
            return self.handle(verb, params)

        self.node.handler = handle
        self.run_agent()
        self.assertNotIn("task.complete", self.node.verbs())
        [fail] = self.node.params("task.fail")
        self.assertIn("ExtensionError", fail["reason"])
        self.assertIn("HTTP 413 too large", fail["reason"])

    def test_over_acp_the_bundle_uploads_and_settles_no_pr(self):
        """The bundle as a node runs it, over real ACP: one turn, the report uploaded, settled `no_pr`."""
        self.node.run(COMMAND, ["Work Jaah task 9."], cwd=self.report_dir)
        self.assertEqual(self.node.stop_reasons, ["end_turn"])
        self.assertEqual(self.node.verbs(), ["task.get", "artifact.upload", "task.complete"])
        self.assertEqual(self.node.params("task.complete")[0]["terminal_type"], "no_pr")
        self.assertIn("Summarise &lt;Q3&gt; churn", self.page())


if __name__ == "__main__":
    unittest.main()
