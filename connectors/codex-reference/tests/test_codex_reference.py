"""Codex (reference) end to end against a fake daemon, fake Codex and fake jaah-forge, with a real git origin."""
from __future__ import annotations

import os
import subprocess
import unittest
from unittest import mock

from harness import FRAGMENT, HELLO, PR_URL, SECRET, USAGE, Bench, result

import codex_run
import security


class DeliveryTest(Bench):
    def test_happy_path_commits_pushes_opens_pr_arms_auto_merge_and_settles_merged(self):
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()

        branch = subprocess.run(["git", "branch", "--list", "fleet/task-42"], cwd=self.origin,
                                capture_output=True, text=True).stdout
        self.assertIn("fleet/task-42", branch, "the branch was pushed to origin")
        self.assertEqual(self.forge_verbs(), ["pr create", "pr auto-merge", "pr view"])
        create = self.forge_calls()[0]["args"]
        self.assertNotIn("--draft", create)
        self.assertEqual(create[create.index("--base") + 1], "main")
        self.assertEqual(create[create.index("--head") + 1], "fleet/task-42")
        [settle] = self.calls("task.settle")
        self.assertEqual(settle["terminal_type"], "merged_pr")
        self.assertEqual(settle["pr_url"], PR_URL)
        self.assertEqual(self.calls("task.fail"), [])

    def test_codex_gets_the_key_in_its_own_env_only_and_never_the_sdk_socket(self):
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        [call] = self.codex_calls()
        self.assertTrue(call["key_ok"], "OPENAI_API_KEY reached the Codex child")
        self.assertTrue(call["codex_key_ok"], "CODEX_API_KEY carries the same key")
        self.assertFalse(call["socket_leaked"], "the Codex child cannot reach the SDK socket")
        self.assertNotIn(SECRET, " ".join(call["args"]), "the key is never an argument")
        self.assertFalse(call["home"].startswith(self.worktree), "CODEX_HOME is outside the worktree")
        self.assertIn("--output-schema", call["args"])
        self.assertEqual(call["args"][call["args"].index("--sandbox") + 1], "workspace-write")

    def test_usage_is_reported_with_cached_tokens_split_out(self):
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        [usage] = [e for e in self.events() if e["type"] == "usage"]
        self.assertEqual(usage["message"]["model"], "gpt-5-codex")
        self.assertEqual(usage["message"]["usage"]["input_tokens"], 600)
        self.assertEqual(usage["message"]["usage"]["cache_read_input_tokens"], 400)
        self.assertEqual(usage["message"]["usage"]["output_tokens"], 50)

    def test_model_override_env_is_used_and_reported(self):
        os.environ["JAAH_CODEX_MODEL"] = "o4-mini"
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        args = self.codex_calls()[0]["args"]
        self.assertEqual(args[args.index("--model") + 1], "o4-mini")
        self.assertEqual([e for e in self.events() if e["type"] == "usage"][0]["message"]["model"], "o4-mini")

    def test_no_change_settles_no_pr_with_evidence(self):
        self.set_plan({"result": result(status="no_change", summary="Already returns hello."), "usage": USAGE})
        self.run_agent()
        [settle] = self.calls("task.settle")
        self.assertEqual(settle["terminal_type"], "no_pr")
        self.assertEqual(settle["no_pr_reason"], "no_code_needed")
        self.assertIn("Already returns hello.", settle["no_pr_evidence"])
        self.assertEqual(self.forge_calls(), [])

    def test_auto_merge_unavailable_merges_itself_once_clean(self):
        with open(self.forge_state, "w") as fh:
            fh.write('{"arm_result": false}')
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertEqual(self.forge_verbs(), ["pr create", "pr auto-merge", "pr view", "pr merge", "pr view"])
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr")

    def test_a_stuck_pr_fails_naming_the_state_and_stays_open(self):
        with open(self.forge_state, "w") as fh:
            fh.write('{"merge_on_arm": false, "merge_state": "checks_failed", "checks": "failure"}')
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        [fail] = self.calls("task.fail")
        self.assertIn("checks_failed", fail["reason"])
        self.assertIn(PR_URL, fail["reason"])
        self.assertNotIn("pr close", self.forge_verbs())
        self.assertEqual(self.calls("task.settle"), [])

    def test_main_turns_a_forge_error_into_a_named_fail(self):
        import main

        with open(self.forge_state, "w") as fh:
            fh.write('{"fail_create": true}')
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        with mock.patch.dict(os.environ, {"JAAH_AGENT_SOCKET": self.daemon.path, "JAAH_AGENT_EVENTS": self.events_path}):
            self.assertEqual(main.main(), 0)
        [fail] = self.calls("task.fail")
        self.assertTrue(fail["reason"].startswith("Delivery failed: jaah-forge pr create exited 3"), fail["reason"])
        self.assertIn("forge error 502", fail["reason"])

    def test_push_disabled_fails_without_pushing(self):
        self.project["push_enabled"] = False
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertIn("push_enabled", self.calls("task.fail")[0]["reason"])
        self.assertEqual(self.forge_calls(), [])

    def test_a_resumed_attempt_continues_the_pushed_branch(self):
        subprocess.run(["git", "checkout", "-q", "-b", "fleet/task-42"], cwd=self.worktree, check=True)
        with open(os.path.join(self.worktree, "earlier.txt"), "w") as fh:
            fh.write("from attempt 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.worktree, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "wip"], cwd=self.worktree, check=True)
        subprocess.run(["git", "push", "-q", "origin", "fleet/task-42"], cwd=self.worktree, check=True)
        subprocess.run(["git", "checkout", "-q", "main"], cwd=self.worktree, check=True)
        self.set_plan({"result": result(status="no_change"), "usage": USAGE})   # nothing new this attempt
        self.run_agent()
        log = subprocess.run(["git", "log", "--oneline", "origin/fleet/task-42"], cwd=self.worktree,
                             capture_output=True, text=True).stdout
        self.assertIn("wip", log)
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr",
                         "the earlier attempt's commit is delivered, not dropped as 'no change'")


class AskTest(Bench):
    def test_blocked_asks_once_then_reruns_with_the_answer(self):
        self.answers = [{"status": "answered", "selections": ["Use UTC"]}]
        self.set_plan({"result": result(status="blocked", question="Which timezone?", options=["Use UTC", "Local"],
                                        blocker_kind="decision"), "usage": USAGE},
                      {"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        [ask] = self.asked
        self.assertEqual(ask["questions"][0]["header"], "Decision")
        self.assertEqual([o["label"] for o in ask["questions"][0]["options"]], ["Use UTC", "Local"])
        second = self.codex_calls()[1]["prompt"]
        self.assertIn("Q: Which timezone?\nA: Use UTC", second)
        self.assertEqual(len([e for e in self.events() if e["type"] == "usage"]), 2, "both passes reported")
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr")

    def test_full_auto_never_asks_a_decision(self):
        self.autonomous = True
        self.set_plan({"result": result(status="blocked", question="Which timezone?", options=["UTC", "Local"],
                                        blocker_kind="decision"), "usage": USAGE},
                      {"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertEqual(self.asked, [])
        self.assertIn("(Full Auto)", self.codex_calls()[1]["prompt"])

    def test_full_auto_still_asks_a_blocker_with_its_licensed_header(self):
        self.autonomous = True
        self.answers = [{"status": "answered", "selections": ["Granted"]}]
        self.set_plan({"result": result(status="blocked", question="Grant repo access?", options=["Granted", "No"],
                                        blocker_kind="Access"), "usage": USAGE},
                      {"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertEqual(self.asked[0]["questions"][0]["header"], "Access")

    def test_a_dismissed_round_tells_codex_to_proceed_on_an_assumption(self):
        self.answers = [{"status": "dismissed"}]
        self.set_plan({"result": result(status="blocked", question="Which?", blocker_kind="decision"), "usage": USAGE},
                      {"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertEqual([o["label"] for o in self.asked[0]["questions"][0]["options"]], ["Proceed", "Stop"])
        self.assertIn("dismissed by the operator", self.codex_calls()[1]["prompt"])

    def test_still_blocked_after_the_round_limit_fails(self):
        self.answers = [{"status": "answered", "selections": ["A"]}] * 3
        self.set_plan({"result": result(status="blocked", question="Again?", options=["A", "B"],
                                        blocker_kind="decision"), "usage": USAGE})
        self.run_agent()
        self.assertEqual(len(self.asked), 3)
        self.assertIn("still blocked after 3 questions", self.calls("task.fail")[0]["reason"])

    def test_no_openai_credential_asks_for_one_then_fails_naming_it(self):
        self.roster = [{"name": "acme/github", "type": "github", "description": "", "fields": ["token"]}]
        self.answers = [{"status": "answered", "selections": ["Stop"]}]
        self.set_plan({"result": result(), "usage": USAGE})
        self.run_agent()
        self.assertEqual(self.asked[0]["questions"][0]["header"], "Credential")
        self.assertIn("No OpenAI API key credential", self.calls("task.fail")[0]["reason"])
        self.assertEqual(self.codex_calls(), [], "Codex never ran without a key")

    def test_a_credential_added_after_the_ask_is_picked_up(self):
        self.roster = []
        self.answers = [{"status": "answered", "selections": ["Added it"]}]
        original = self.handle

        def handle(verb, args):
            if verb == "agent.answers":
                self.roster = [{"name": "jaah/system/openai-api-key", "type": "generic_api_key",
                                "description": "", "fields": ["api_key"]}]
            return original(verb, args)

        self.daemon.handler = handle
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertEqual(self.calls("task.credential_get")[0]["name"], "jaah/system/openai-api-key")
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr")


class SecurityGateTest(Bench):
    AUTH_CHANGE = {"src/auth/login.py": "def login():\n    return True\n"}

    def test_not_autonomous_asks_one_security_round_and_merges_only_on_approve(self):
        self.answers = [{"status": "answered", "selections": ["Approve merge"]}]
        self.set_plan({"files": self.AUTH_CHANGE, "result": result(), "usage": USAGE})
        self.run_agent()
        [ask] = self.asked
        q = ask["questions"][0]
        self.assertEqual(q["header"], "Security")
        self.assertEqual([o["label"] for o in q["options"]], ["Approve merge", "Do not merge"])
        self.assertIn(security.AUTH, q["question"])
        self.assertIn(PR_URL, q["question"])
        self.assertNotIn("pr auto-merge", self.forge_verbs(), "auto-merge is never armed on a security diff")
        self.assertIn("pr merge", self.forge_verbs())
        self.assertNotIn("--draft", self.forge_calls()[0]["args"])
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr")

    def test_not_autonomous_do_not_merge_leaves_the_pr_open_and_unmerged(self):
        self.answers = [{"status": "answered", "selections": ["Do not merge"]}]
        self.set_plan({"files": self.AUTH_CHANGE, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertEqual(self.forge_verbs(), ["pr create"])
        [fail] = self.calls("task.fail")
        self.assertTrue(fail["reason"].startswith(f"Security surface: {security.AUTH}"))
        self.assertIn(PR_URL, fail["reason"])
        self.assertEqual(self.calls("task.settle"), [])

    def test_autonomous_opens_a_draft_and_fails_naming_the_surface_first(self):
        self.autonomous = True
        self.set_plan({"files": self.AUTH_CHANGE, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertEqual(self.asked, [], "Full Auto asks no decision")
        self.assertEqual(self.forge_verbs(), ["pr create"], "no auto-merge, no merge")
        self.assertIn("--draft", self.forge_calls()[0]["args"])
        [fail] = self.calls("task.fail")
        self.assertTrue(fail["reason"].startswith(f"Security surface: {security.AUTH}"), fail["reason"])
        self.assertIn(PR_URL, fail["reason"])
        self.assertIn("DRAFT", fail["reason"])
        self.assertEqual(self.calls("task.settle"), [])

    def test_codex_self_report_puts_an_innocent_path_on_the_surface(self):
        self.autonomous = True
        self.set_plan({"files": HELLO, "result": result(security_surfaces=["changes a DB migration"]),
                       "usage": USAGE})
        self.run_agent()
        self.assertTrue(self.calls("task.fail")[0]["reason"].startswith(f"Security surface: {security.MIGRATIONS}"))

    def test_the_pr_body_names_the_surface(self):
        self.answers = [{"status": "answered", "selections": ["Do not merge"]}]
        self.set_plan({"files": self.AUTH_CHANGE, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertIn("**Security surface:** sign-in/auth/authz", self.forge_calls()[0]["body"])


class GateTest(Bench):
    def test_a_red_gate_gets_one_fix_pass_with_its_output(self):
        self.project["gate_quick_cmd"] = "test -f src/fixed.py || (echo 'missing fixed.py'; exit 1)"
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE},
                      {"files": {"src/fixed.py": "x = 1\n"}, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertIn("missing fixed.py", self.codex_calls()[1]["prompt"])
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr")

    def test_a_gate_still_red_after_the_fix_pass_fails(self):
        self.project["gate_full_cmd"] = "echo nope; exit 1"
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertIn("still red", self.calls("task.fail")[0]["reason"])
        self.assertEqual(self.forge_calls(), [], "nothing red is pushed as a PR")


class SecretTest(Bench):
    """N8: the OpenAI key value never reaches a log, a call, a commit or a PR — not even Codex's fragment."""

    def assert_never_written(self):
        written = self.everything_written()
        self.assertNotIn(SECRET, written)
        self.assertNotIn(FRAGMENT, written)
        self.assertNotIn(SECRET[:8], written)

    def test_codex_echoing_the_key_on_failure_is_scrubbed_from_the_fail_reason(self):
        self.set_plan({"leak": True, "exit": 1, "usage": USAGE})
        self.run_agent()
        [fail] = self.calls("task.fail")
        self.assertIn("Codex did not finish (exit 1)", fail["reason"])
        self.assertIn("<redacted:openai-key>", fail["reason"])
        self.assert_never_written()

    def test_the_whole_happy_path_never_writes_the_key(self):
        self.set_plan({"leak": True, "files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr")
        self.assert_never_written()

    def test_the_credential_event_carries_the_name_only(self):
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        logged = [e for e in self.events() if e.get("message") == "using OpenAI credential"]
        self.assertEqual(logged[0]["data"], {"credential": "acme/openai"})
        self.assert_never_written()


class UnitTest(unittest.TestCase):
    def test_scrub_removes_the_value_and_codex_fragments(self):
        text = f"key={SECRET} and Incorrect API key provided: {FRAGMENT}."
        out = codex_run.scrub(text, SECRET)
        self.assertNotIn(SECRET, out)
        self.assertNotIn(FRAGMENT, out)
        self.assertEqual(codex_run.scrub("plain", SECRET), "plain")
        self.assertEqual(codex_run.scrub("x", ""), "x")

    def test_parse_events_sums_turns_and_collects_errors(self):
        stdout = "\n".join(['{"type":"turn.completed","usage":{"input_tokens":3,"cached_input_tokens":1,'
                            '"output_tokens":2}}', 'not json', '[1]',
                            '{"type":"turn.completed","usage":{"input_tokens":4,"output_tokens":1}}',
                            '{"type":"turn.failed","error":{"message":"boom"}}', '{"type":"error"}'])
        usage, errors = codex_run.parse_events(stdout, SECRET)
        self.assertEqual((usage.input_tokens, usage.cached_input_tokens, usage.output_tokens), (7, 1, 3))
        self.assertEqual(errors, ["boom", "error"])

    def test_a_malformed_final_message_is_no_result(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write('{"status": "weird"}')
        self.assertIsNone(codex_run._read_result(fh.name))
        self.assertIsNone(codex_run._read_result(fh.name + ".missing"))
        os.unlink(fh.name)

    def test_security_paths(self):
        cases = {
            "src/auth/login.py": [security.AUTH], "app/routes/sessions.py": [security.AUTH],
            "config/secrets.yaml": [security.SECRETS], ".env.production": [security.SECRETS],
            "certs/server.pem": [security.SECRETS], "db/migrations/0042_add.py": [security.MIGRATIONS],
            "schema/init.sql": [security.MIGRATIONS], "src/gdpr/export.py": [security.PII],
            "src/hello.py": [], "docs/authoring.md": [], "src/tokenizer.py": [],
        }
        for path, want in cases.items():
            self.assertEqual(security.surfaces_for_paths([path]), want, path)

    def test_classify_normalises_codex_reports_and_keeps_unknown_ones(self):
        self.assertEqual(security.classify([], ["touches the login flow", "stores a password"]),
                         [security.AUTH, security.SECRETS])
        self.assertEqual(security.classify([], ["billing webhook"]), ["billing webhook"])
        self.assertEqual(security.classify(["src/hello.py"], ["", "  "]), [])


if __name__ == "__main__":
    unittest.main()
