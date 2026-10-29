"""Test fixture: a Python hello agent, plus two probes — an exact echo of the operator's answer, and a line carrying
every secret of a `k10-planted-*` credential, which the node must mask in the transcript.

Attended, it asks first; the answer arrives as the next prompt, and the run goes on from there."""
import html
import os
import sys

import jaah_agent
from acp.schema import UsageUpdate

QUESTION = "Say hello for task {id}?"


class PyHello(jaah_agent.Agent):
    name = "py-hello"
    version = "2.0.0"

    def __init__(self) -> None:
        self.asked: dict[str, str] = {}   # session id -> the question it is waiting on

    async def prompt(self, turn: jaah_agent.Turn) -> None:
        task = await turn.jaah.call("task.get")
        print(f"py-hello: starting task {task['id']} ({task['persona']})", file=sys.stderr, flush=True)
        question = self.asked.pop(turn.session_id, None)
        if question is None:
            await probe(turn)
            if not task["autonomous"]:          # Full Auto: decide yourself, never ask
                question = QUESTION.format(id=task["id"])
                await turn.jaah.ask(question, header="Confirm", options=[{"label": "Yes"}, {"label": "No"}])
                self.asked[turn.session_id] = question
                return None                     # the answer is the next prompt
        else:
            prefix = f"Answer to your question '{question}': "
            answer = turn.text.removeprefix(prefix).strip()
            await turn.say(f"answer received: {answer}\n")
            if answer != "Yes":
                await finish(turn, task, "The operator declined the hello.", "Operator answered other than Yes.")
                return None
        await turn.say("Hello from Py hello!\n")
        await turn.update(UsageUpdate(session_update="usage_update", used=0, size=0, field_meta={
            "jaah.ai": {"usage": {"model": "my-model-1", "input_tokens": 120, "output_tokens": 40}}}))
        await finish(turn, task, "Said hello.", "Hello agent: no code change requested.")
        return None


async def probe(turn: jaah_agent.Turn) -> None:
    """These values must reach the transcript as <redacted:…>."""
    for entry in (await turn.jaah.credentials()).get("credentials") or []:
        if entry["name"].startswith("k10-planted"):
            config = (await turn.jaah.credential(entry["name"])).get("config") or {}
            await turn.say(f"planted secret probe ({entry['name']}): {' '.join(str(v) for v in config.values())}\n")


async def finish(turn: jaah_agent.Turn, task: dict, summary: str, evidence: str) -> None:
    # Upload a report on EVERY path, the decline included: some kinds of task refuse a settle without one.
    report = os.path.join(os.environ["JAAH_REPORT_DIR"], "report.html")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"<!doctype html><title>Hello</title><p>{html.escape(task['title'])}: {html.escape(summary)}</p>")
    await turn.jaah.call("artifact.upload", {"kind": "report", "path": report})
    await turn.complete(summary, "no_pr", no_pr_reason="no_code_needed", no_pr_evidence=evidence)


if __name__ == "__main__":
    jaah_agent.run(PyHello())
