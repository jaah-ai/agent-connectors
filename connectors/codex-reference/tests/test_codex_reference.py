"""Codex (reference) end to end against a fake daemon, fake Codex and fake jaah-forge, with a real git origin.

Every test runs the real `main()` and the bench asserts exactly one terminal call (settle or fail)."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest import mock

from harness import FRAGMENT, HELLO, PR_URL, SECRET, USAGE, Bench, result

import codex_run
import gitops
import security

CREATED = ["pr view", "pr create"]   # the branch has no PR yet, so one is created


def origin_log(bench, ref="fleet/task-42") -> str:
    return subprocess.run(["git", "log", "--name-only", "--format=%s", ref], cwd=bench.origin,
                          capture_output=True, text=True).stdout


class DeliveryTest(Bench):
    def test_happy_path_commits_pushes_opens_pr_arms_auto_merge_and_settles_merged(self):
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertIn("src/hello.py", origin_log(self), "the commit was pushed to origin")
        self.assertEqual(self.forge_verbs(), CREATED + ["pr auto-merge", "pr view"])
        create = self.forge_calls()[1]["args"]
        self.assertNotIn("--draft", create)
        self.assertEqual(create[create.index("--base") + 1], "main")
        self.assertEqual(create[create.index("--head") + 1], "fleet/task-42")
        [settle] = self.calls("task.settle")
        self.assertEqual(settle["terminal_type"], "merged_pr")
        self.assertEqual(settle["pr_url"], PR_URL)

    def test_the_branch_carries_the_project_branch_prefix(self):
        self.project["branch_prefix"] = "sn/"
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        create = self.forge_calls()[1]["args"]
        self.assertEqual(create[create.index("--head") + 1], "sn/fleet/task-42")
        self.assertIn("src/hello.py", origin_log(self, "sn/fleet/task-42"))

    def test_a_fresh_branch_starts_from_origin_base_not_the_worktree_head(self):
        subprocess.run(["git", "checkout", "-q", "-b", "stale"], cwd=self.worktree, check=True)
        with open(os.path.join(self.worktree, "stale.txt"), "w") as fh:
            fh.write("left over from another task\n")
        subprocess.run(["git", "add", "-A"], cwd=self.worktree, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "stale"], cwd=self.worktree, check=True)
        self.set_plan({"result": result(status="no_change"), "usage": USAGE})
        self.run_agent()
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "no_pr", "stale work is not delivered")

    def test_repository_hooks_never_run(self):
        """A hook is repo content a model could plant: git must not run it (it would see no sandbox)."""
        marker = os.path.join(self.tmp, "hook-ran")
        for hook in ("pre-commit", "commit-msg", "pre-push", "post-checkout"):
            path = os.path.join(self.worktree, ".git", "hooks", hook)
            with open(path, "w") as fh:
                fh.write(f"#!/bin/sh\nenv > {marker}\nexit 1\n")
            os.chmod(path, 0o755)
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertFalse(os.path.exists(marker), "no hook ran")
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr")

    def test_codex_gets_the_key_in_its_own_env_only_and_never_the_sdk_socket(self):
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        [call] = self.codex_calls()
        self.assertTrue(call["key_ok"], "OPENAI_API_KEY reached the Codex child")
        self.assertTrue(call["codex_key_ok"], "CODEX_API_KEY carries the same key")
        self.assertFalse(call["socket_leaked"], "the Codex child cannot reach the SDK socket")
        self.assertNotIn(SECRET, " ".join(call["args"]), "the key is never an argument")
        self.assertFalse(call["home"].startswith(self.worktree), "CODEX_HOME is outside the worktree")
        self.assertIn('shell_environment_policy.inherit="core"', call["args"], "model shells get no key")
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
        self.forge_state_set(arm_result=False)
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertEqual(self.forge_verbs(), CREATED + ["pr auto-merge", "pr view", "pr merge", "pr view"])
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr")

    def test_an_auto_merge_error_falls_back_to_merging_itself(self):
        self.forge_state_set(fail_arm=True)
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertIn("pr merge", self.forge_verbs())
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr")

    def test_an_armed_pr_is_watched_until_the_forge_reports_it_merged(self):
        self.forge_state_set(merge_on_arm=False, merge_state="pending", checks="pending", merge_after_views=3)
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertEqual(self.forge_verbs().count("pr view"), 1 + 3)
        self.assertNotIn("pr merge", self.forge_verbs(), "an armed PR is never merged by hand")
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr")

    def test_a_stuck_pr_fails_naming_the_state_and_stays_open(self):
        self.forge_state_set(merge_on_arm=False, merge_state="checks_failed", checks="failure")
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        [fail] = self.calls("task.fail")
        self.assertIn("checks_failed", fail["reason"])
        self.assertIn(PR_URL, fail["reason"])
        self.assertNotIn("pr close", self.forge_verbs())

    def test_a_refused_merge_is_retried_then_named(self):
        self.forge_state_set(arm_result=False, fail_merge=True)
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertEqual(self.forge_verbs().count("pr merge"), gitops.MERGE_ATTEMPTS)
        [fail] = self.calls("task.fail")
        self.assertIn("base branch policy", fail["reason"])
        self.assertIn(PR_URL, fail["reason"])

    def test_a_resumed_attempt_reuses_its_open_pr(self):
        self.forge_state_set(created=True)
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertNotIn("pr create", self.forge_verbs())
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr")

    def test_push_disabled_fails_before_doing_anything(self):
        self.project["push_enabled"] = False
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertIn("push_enabled", self.calls("task.fail")[0]["reason"])
        self.assertEqual(self.codex_calls(), [])
        self.assertEqual(self.forge_calls(), [])

    def test_a_resumed_attempt_continues_the_pushed_branch(self):
        self.commit_on_origin_branch({"earlier.txt": "from attempt 1\n"})
        self.set_plan({"result": result(status="no_change"), "usage": USAGE})   # nothing new this attempt
        self.run_agent()
        self.assertIn("wip from attempt 1", origin_log(self))
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr",
                         "the earlier attempt's commit is delivered, not dropped as 'no change'")

    def test_a_forge_error_becomes_a_named_fail(self):
        self.forge_state_set(fail_create=True)
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        reason = self.calls("task.fail")[0]["reason"]
        self.assertTrue(reason.startswith("Codex (reference) stopped unexpectedly: ForgeError: jaah-forge pr create "
                                          "exited 3"), reason)
        self.assertIn("forge error 502", reason)

    def test_codex_missing_from_path_is_a_named_fail(self):
        os.remove(os.path.join(self.tmp, "bin", "codex"))
        with mock.patch.dict(os.environ, {"PATH": os.path.join(self.tmp, "bin") + os.pathsep + "/usr/bin:/bin"}):
            if any(os.path.exists(os.path.join(d, "codex")) for d in ("/usr/bin", "/bin")):
                self.skipTest("a real codex is installed system-wide")
            self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
            self.run_agent()
        self.assertIn("could not run codex", self.calls("task.fail")[0]["reason"])

    def test_a_blank_commit_title_falls_back_to_the_task_title(self):
        self.set_plan({"files": HELLO, "result": result(commit_title="  \n "), "usage": USAGE})
        self.run_agent()
        self.assertIn("Add a hello module", origin_log(self))

    def test_a_refused_settle_is_followed_by_a_named_fail(self):
        from fake_daemon import err

        original = self.handle
        self.daemon.handler = lambda verb, args: (err("rejected", "HTTP 409 task is not active")
                                                  if verb == "task.settle" else original(verb, args))
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        import main
        env = {"JAAH_AGENT_SOCKET": self.daemon.path, "JAAH_AGENT_EVENTS": self.events_path}
        with mock.patch.dict(os.environ, env), mock.patch("gitops.time.sleep", lambda _s: None):
            main.main()
        self.assertEqual(len(self.calls("task.settle")), 1)
        [fail] = self.calls("task.fail")
        self.assertIn("HTTP 409", fail["reason"])

    def test_an_unexpected_error_still_ends_in_one_fail(self):
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        with mock.patch.object(security, "classify", side_effect=RuntimeError("boom")):
            self.run_agent()
        self.assertIn("RuntimeError: boom", self.calls("task.fail")[0]["reason"])


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
        self.assertIn("Q: Which timezone?\nA: Use UTC", self.codex_calls()[1]["prompt"])
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

    def test_full_auto_cannot_loop_forever(self):
        self.autonomous = True
        self.set_plan({"result": result(status="blocked", question="Which?", blocker_kind="decision"), "usage": USAGE})
        self.run_agent()
        self.assertEqual(len(self.codex_calls()), 6)
        self.assertIn("did not finish within 6 passes", self.calls("task.fail")[0]["reason"])

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

    def test_not_autonomous_asks_one_security_round_and_merges_only_the_approved_head(self):
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
        [merge] = [c["args"] for c in self.forge_calls() if c["args"][:2] == ["pr", "merge"]]
        head = subprocess.run(["git", "rev-parse", "fleet/task-42"], cwd=self.origin, capture_output=True,
                              text=True).stdout.strip()
        self.assertEqual(merge[merge.index("--sha") + 1], head, "merge pinned to the head the human approved")
        self.assertNotIn("--draft", self.forge_calls()[1]["args"])
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr")

    def test_not_autonomous_do_not_merge_leaves_the_pr_open_and_unmerged(self):
        self.answers = [{"status": "answered", "selections": ["Do not merge"]}]
        self.set_plan({"files": self.AUTH_CHANGE, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertEqual(self.forge_verbs(), CREATED)
        [fail] = self.calls("task.fail")
        self.assertTrue(fail["reason"].startswith(f"Security surface: {security.AUTH}"))
        self.assertIn(PR_URL, fail["reason"])

    def test_autonomous_opens_a_draft_and_fails_naming_the_surface_first(self):
        self.autonomous = True
        self.set_plan({"files": self.AUTH_CHANGE, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertEqual(self.asked, [], "Full Auto asks no decision")
        self.assertEqual(self.forge_verbs(), CREATED, "no auto-merge, no merge")
        self.assertIn("--draft", self.forge_calls()[1]["args"])
        [fail] = self.calls("task.fail")
        self.assertTrue(fail["reason"].startswith(f"Security surface: {security.AUTH}"), fail["reason"])
        self.assertIn(PR_URL, fail["reason"])
        self.assertIn("DRAFT", fail["reason"])

    def test_moving_auth_code_out_of_auth_is_still_on_the_surface(self):
        self.autonomous = True
        subprocess.run(["mkdir", "-p", os.path.join(self.worktree, "src", "auth")], check=True)
        with open(os.path.join(self.worktree, "src", "auth", "guard.py"), "w") as fh:
            fh.write("def check():\n    return False\n")
        subprocess.run(["git", "add", "-A"], cwd=self.worktree, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "guard"], cwd=self.worktree, check=True)
        subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=self.worktree, check=True)
        self.set_plan({"rename": {"src/auth/guard.py": "src/util/helpers.py"},
                       "files": {"src/util/helpers.py": "def check():\n    return True\n"}, "result": result(),
                       "usage": USAGE})
        self.run_agent()
        self.assertTrue(self.calls("task.fail")[0]["reason"].startswith(f"Security surface: {security.AUTH}"))

    def test_an_earlier_attempts_security_commit_is_classified(self):
        self.commit_on_origin_branch(self.AUTH_CHANGE)
        self.set_plan({"files": {"README.md": "app, docs\n"}, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertNotIn("pr auto-merge", self.forge_verbs())
        self.assertEqual(self.asked[0]["questions"][0]["header"], "Security")

    def test_a_ci_workflow_change_is_on_the_surface(self):
        self.autonomous = True
        self.set_plan({"files": {".github/workflows/ci.yml": "on: push\n"}, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertTrue(self.calls("task.fail")[0]["reason"].startswith(f"Security surface: {security.CI}"))

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
        self.assertIn("**Security surface:** sign-in/auth/authz", self.forge_calls()[1]["body"])


class GateTest(Bench):
    def test_a_red_gate_gets_one_fix_pass_with_its_output(self):
        self.project["gate_quick_cmd"] = "test -f src/fixed.py || (echo 'missing fixed.py'; exit 1)"
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE},
                      {"files": {"src/fixed.py": "x = 1\n"}, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertIn("missing fixed.py", self.codex_calls()[1]["prompt"])
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr")

    def test_a_gate_still_red_after_the_fix_pass_fails_and_nothing_is_pushed(self):
        self.project["gate_full_cmd"] = "echo nope; exit 1"
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertIn("still red", self.calls("task.fail")[0]["reason"])
        self.assertEqual(self.forge_calls(), [])
        self.assertEqual(origin_log(self), "", "nothing red reached origin")

    def test_a_fix_pass_that_reports_no_change_is_still_gated(self):
        self.project["gate_full_cmd"] = "test -f src/fixed.py"
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE},
                      {"result": result(status="no_change"), "usage": USAGE})
        self.run_agent()
        self.assertIn("still red", self.calls("task.fail")[0]["reason"])
        self.assertEqual(self.forge_calls(), [])

    def test_files_the_gate_writes_are_never_committed(self):
        self.project["gate_quick_cmd"] = "echo cov > gate-output.txt; test -f src/fixed.py"
        self.set_plan({"files": HELLO, "result": result(), "usage": USAGE},
                      {"files": {"src/fixed.py": "x = 1\n"}, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertNotIn("gate-output.txt", origin_log(self))
        self.assertIn("src/fixed.py", origin_log(self))


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

    def test_a_key_in_codexs_answer_is_scrubbed_from_commit_pr_and_settle(self):
        self.set_plan({"files": HELLO, "result": result(summary=f"Done. Debug key {SECRET}",
                                                        commit_title=f"feat: {SECRET}"), "usage": USAGE})
        self.run_agent()
        self.assertEqual(self.calls("task.settle")[0]["terminal_type"], "merged_pr")
        self.assertIn("<redacted:openai-key>", self.forge_calls()[1]["body"])
        self.assert_never_written()

    def test_a_key_written_into_the_change_ships_nothing(self):
        self.set_plan({"files": {"docs/debug.md": "key: {KEY}\n"}, "result": result(), "usage": USAGE})
        self.run_agent()
        self.assertIn("Codex wrote the OpenAI key", self.calls("task.fail")[0]["reason"])
        self.assertEqual(self.forge_calls(), [])
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

    def test_the_final_message_is_validated_and_coerced(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write('{"status": "weird"}')
        self.assertIsNone(codex_run._read_result(fh.name))
        self.assertIsNone(codex_run._read_result(fh.name + ".missing"))
        with open(fh.name, "w") as out:
            out.write('{"status": "done", "summary": 5, "options": ["a", {"x": 1}], "security_surfaces": null}')
        got = codex_run._read_result(fh.name)
        self.assertEqual((got["summary"], got["options"], got["security_surfaces"], got["commit_title"]),
                         ("5", ["a"], [], ""))
        os.unlink(fh.name)

    def test_security_paths(self):
        cases = {
            "src/auth/login.py": [security.AUTH], "app/routes/sessions.py": [security.AUTH],
            "src/authentication/x.py": [security.AUTH], "src/AuthMiddleware.ts": [security.AUTH],
            "LoginController.java": [security.AUTH], "src/crypto/hmac.go": [security.AUTH],
            "config/secrets.yaml": [security.SECRETS], ".env.production": [security.SECRETS],
            "certs/server.pem": [security.SECRETS], "certs/server.crt": [security.SECRETS],
            "id_rsa": [security.SECRETS], ".npmrc": [security.SECRETS],
            "db/migrations/0042_add.py": [security.MIGRATIONS], "schema/init.sql": [security.MIGRATIONS],
            "prisma/schema.prisma": [security.MIGRATIONS], "db/schema.rb": [security.MIGRATIONS],
            "src/gdpr/export.py": [security.PII],
            ".github/workflows/ci.yml": [security.CI], "CODEOWNERS": [security.CI], "infra/main.tf": [security.CI],
            "src/hello.py": [], "src/tokenizer.py": [], "docs/keyboard.md": [],
        }
        for path, want in cases.items():
            self.assertEqual(security.surfaces_for_paths([path]), want, path)

    def test_classify_normalises_codex_reports(self):
        self.assertEqual(security.classify([], ["touches the login flow", "stores a password"]),
                         [security.AUTH, security.SECRETS])
        self.assertEqual(security.classify([], ["billing webhook"]), ["billing webhook"])
        self.assertEqual(security.classify(["src/hello.py"], ["", "  ", "None", "N/A."]), [])
        self.assertEqual(security.classify([], ["new keyboard shortcut"]), ["new keyboard shortcut"])


if __name__ == "__main__":
    unittest.main()
