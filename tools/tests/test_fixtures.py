"""The fixtures misbehave exactly as fixtures/README.md promises — and in no other way.

Each runs as a node runs it — `command` from its folder, nothing but that folder on the path — over real ACP against
the fake node (tools/fake_node.py). Their folders are generated first (the Agent SDK's examples/vendor.py)."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
FIXTURES = os.path.join(REPO, "fixtures")
# fake_node imports the SDK: borrow py-hello's generated copy.
sys.path[:0] = [os.path.join(REPO, "tools"), os.path.join(FIXTURES, "py-hello")]
if not os.path.isfile(os.path.join(FIXTURES, "py-hello", "jaah_agent", "__init__.py")):
    raise unittest.SkipTest("the fixtures carry no SDK: run the Agent SDK's examples/vendor.py on them first")

import check_connectors as cc  # noqa: E402
from fake_node import FakeNode, settled  # noqa: E402

TASK = {"id": 5, "title": "Say <hi>", "persona": "coder", "autonomous": False, "state": "active"}
PLANTED = "k10-planted-value-0123"


def python(fixture: str) -> list[str]:
    return [sys.executable, "-S", "-s", "-E", os.path.join(FIXTURES, fixture, "agent.py")]


class FixtureBench(unittest.TestCase):
    def setUp(self):
        self.report_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.report_dir, True)
        self.task = dict(TASK)

    def handle(self, verb, params):
        if verb == "task.get":
            return self.task
        if verb == "credential.list":
            return {"credentials": [{"name": "k10-planted-key", "type": "generic_api_key"}]}
        if verb == "credential.get":
            return {"name": params["name"], "config": {"api_key": PLANTED}}
        return settled(verb, params)

    def run_fixture(self, command: list[str], prompts: list[str]) -> FakeNode:
        return FakeNode(self.handle).run(command, prompts, cwd=self.report_dir,
                                         env={"JAAH_REPORT_DIR": self.report_dir})


class BrokenManifestTest(unittest.TestCase):
    def test_one_sentence_per_bad_field_and_nothing_else(self):
        self.assertEqual(cc.validate(os.path.join(FIXTURES, "broken-manifest")), [
            "`language` must be python or typescript, got ruby",
            "`command` must be a list of words such as [\"python3\", \"agent.py\"], got ruby main.rb",
        ])


class ExitUnsettledTest(FixtureBench):
    def test_exits_1_on_its_first_prompt_without_a_terminal_call(self):
        root = os.path.join(FIXTURES, "exit-unsettled")
        self.assertEqual(cc.validate(root), [], "the manifest itself is valid")
        node = self.run_fixture(python("exit-unsettled"), ["go"])
        self.assertEqual(node.returncode, 1)
        self.assertEqual(node.verbs(), [], "no settle and no fail")
        self.assertEqual(node.stop_reasons, [], "the turn never ended")


class PyHelloTest(FixtureBench):
    def answer(self, text: str) -> str:
        return f"Answer to your question 'Say hello for task 5?': {text}"

    def test_attended_asks_then_echoes_the_answer_says_hello_and_settles(self):
        node = self.run_fixture(python("py-hello"), ["Work task 5.", self.answer("Yes")])
        self.assertEqual(node.stop_reasons, ["end_turn", "end_turn"], "the question ended the first turn")
        self.assertEqual(node.params("input.request"), [{"question": "Say hello for task 5?", "header": "Confirm",
                                                         "options": [{"label": "Yes"}, {"label": "No"}]}])
        self.assertIn("answer received: Yes\n", node.said())
        self.assertIn("Hello from Py hello!", node.said())
        self.assertEqual(node.usage(), [{"model": "my-model-1", "input_tokens": 120, "output_tokens": 40}])
        self.assertEqual(node.verbs()[-2:], ["artifact.upload", "task.complete"])
        self.assertEqual(node.params("task.complete")[0]["summary"], "Said hello.")

    def test_the_planted_secret_is_said_so_the_transcript_must_mask_it(self):
        node = self.run_fixture(python("py-hello"), ["Work task 5.", self.answer("No")])
        self.assertIn(f"planted secret probe (k10-planted-key): {PLANTED}", node.said())
        self.assertEqual(node.params("task.complete")[0]["summary"], "The operator declined the hello.")

    def test_full_auto_never_asks(self):
        self.task["autonomous"] = True
        node = self.run_fixture(python("py-hello"), ["Work task 5."])
        self.assertNotIn("input.request", node.verbs())
        self.assertEqual(node.params("task.complete")[0]["summary"], "Said hello.")
        with open(os.path.join(self.report_dir, "report.html"), encoding="utf-8") as fh:
            self.assertIn("Say &lt;hi&gt;: Said hello.", fh.read())


@unittest.skipUnless(shutil.which("node"), "no node on PATH")
class TsHelloTest(FixtureBench):
    COMMAND = ["node", os.path.join(FIXTURES, "ts-hello", "agent.mjs")]

    def test_attended_asks_says_hello_reports_usage_uploads_and_settles(self):
        node = self.run_fixture(self.COMMAND, ["Work task 5.", "Answer to your question 'Say hello for task 5?': Yes"])
        self.assertEqual(node.stop_reasons, ["end_turn", "end_turn"])
        self.assertEqual(node.params("input.request")[0]["header"], "Confirm")
        self.assertIn("Hello from TS hello!", node.said())
        self.assertEqual(node.usage(), [{"model": "my-model-1", "input_tokens": 120, "output_tokens": 40}])
        self.assertEqual(node.verbs()[-2:], ["artifact.upload", "task.complete"])

    def test_a_no_declines_and_still_uploads_a_report(self):
        node = self.run_fixture(self.COMMAND, ["Work task 5.", "Answer to your question 'Say hello for task 5?': No"])
        self.assertEqual(node.params("task.complete")[0]["summary"], "The operator declined the hello.")
        self.assertEqual(node.params("artifact.upload")[0]["kind"], "report")


if __name__ == "__main__":
    unittest.main()
