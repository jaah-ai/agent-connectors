"""tools/check_connectors.py: one sentence per bad field (manifest 2), asserted verbatim."""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, TOOLS)

import check_connectors as cc  # noqa: E402

VALID = {
    "name": '"hello-agent"', "display_name": '"Hello agent"', "description": '"Says hello."',
    "type": '"non_coding"', "language": '"python"', "manifest": "2", "command": '["python3", "agent.py"]',
    "version": '"2.0.0"', "maintainer": '"@octocat"', "personas": '["coder"]',
}


class Agent:
    def __init__(self, test: unittest.TestCase, **overrides):
        self.root = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, self.root, True)
        fields = {**VALID, **overrides}
        body = "[agent]\n" + "".join(f"{k} = {v}\n" for k, v in fields.items() if v is not None)
        self.write(cc.MANIFEST, body)
        self.write("agent.py", "import jaah_agent\n")

    def write(self, name: str, body: str) -> "Agent":
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return self


class ValidateTest(unittest.TestCase):
    def errors(self, **overrides) -> list[str]:
        return cc.validate(Agent(self, **overrides).root)

    def test_a_valid_manifest_has_no_errors(self):
        self.assertEqual(self.errors(), [])

    def test_each_field_is_one_named_sentence(self):
        cases = {
            "name": ('"Hello_Agent"', "`name` must be a slug (a lowercase letter, then 1-39 lowercase letters, "
                                      "digits or hyphens), got 'Hello_Agent'"),
            "display_name": ('""', "`display_name` must be 1-60 characters, got 0"),
            "description": ('"' + "x" * 281 + '"', "`description` must be text of at most 280 characters"),
            "type": ('"bot"', "`type` must be coding, non_coding or cli_wrapper, got bot"),
            "language": ('"ruby"', "`language` must be python or typescript, got ruby"),
            "version": ('"1.0"', "`version` must be semver (e.g. 0.1.0), got 1.0"),
            "maintainer": ('"someone"', "`maintainer` must be a GitHub handle (@name) or \"Name <email>\", got 'someone'"),
            "personas": ('["coder", "advisor"]', "`personas` may only name coder, business_analysis, architecture, "
                                                 "plan, finish, researcher; got advisor"),
            "manifest": ("1", "`manifest` must be 2 (SDK v2), got 1"),
            "command": ('"python3 agent.py"', "`command` must be a list of words such as [\"python3\", \"agent.py\"], "
                                              "got python3 agent.py"),
        }
        for field, (value, sentence) in cases.items():
            self.assertEqual(self.errors(**{field: value}), [sentence], field)

    def test_manifest_1_fields_are_refused(self):
        self.assertEqual(self.errors(entrypoint='"agent.py"', sdk='">=0.1,<0.2"'), [
            "`entrypoint` is a manifest 1 field; a manifest 2 agent names `command` instead",
            "`sdk` is a manifest 1 field; a manifest 2 agent names `command` instead"])
        self.assertEqual(self.errors(manifest=None), ["`manifest` must be 2 (SDK v2), got None"])
        self.assertEqual(self.errors(command=None), ["`command` is missing"])
        self.assertEqual(self.errors(command="[]"), ["`command` must be a list of words such as [\"python3\", "
                                                     "\"agent.py\"], got "])

    def test_missing_and_mistyped_fields(self):
        self.assertEqual(self.errors(name=None), ["`name` is missing"])
        self.assertEqual(self.errors(version="1"), ["`version` must be a string, got int"])
        self.assertEqual(self.errors(personas="[]"), ["`personas` must list at least one persona"])

    def test_maintainer_forms(self):
        for ok in ('"@octocat"', '"@jaah-ai/maintainers"', '"Ada Lovelace <ada@example.com>"'):
            self.assertEqual(self.errors(maintainer=ok), [], ok)

    def test_no_manifest_bad_toml_and_no_agent_table(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        self.assertEqual(cc.validate(root), [f"No jaah-agent.toml at the agent root ({root})"])
        agent = Agent(self).write(cc.MANIFEST, "[agent\n")
        self.assertTrue(cc.validate(agent.root)[0].startswith("jaah-agent.toml is not valid TOML"))
        agent.write(cc.MANIFEST, "[runtime]\ninstall = true\n")
        self.assertEqual(cc.validate(agent.root), ["jaah-agent.toml has no [agent] table"])

    def test_runtime_install_is_a_manifest_1_field(self):
        agent = Agent(self)
        with open(os.path.join(agent.root, cc.MANIFEST), "a") as fh:
            fh.write("[runtime]\ninstall = false\n")
        self.assertEqual(cc.validate(agent.root), [
            "`install` in [runtime] is a manifest 1 field: a manifest 2 agent lists its dependencies in "
            "requirements.txt or package.json beside its script, and the node installs them"])



class BundleTest(unittest.TestCase):
    def test_a_python_folder_must_carry_jaah_agent_and_requirements_beside_its_script(self):
        agent = Agent(self)
        self.assertEqual(cc.bundle_errors(agent.root), [
            "`command` runs agent.py, which imports the Jaah SDK, but jaah_agent/__init__.py and requirements.txt "
            f"missing beside it: run the Agent SDK's examples/vendor.py {agent.root}"])
        agent.write("jaah_agent/__init__.py", "")
        self.assertIn("but requirements.txt missing beside it", cc.bundle_errors(agent.root)[0])
        agent.write("requirements.txt", "")
        self.assertEqual(cc.bundle_errors(agent.root), [])

    def test_a_script_that_imports_no_sdk_needs_none(self):
        agent = Agent(self).write("agent.py", "print('hi')\n")
        self.assertEqual(cc.bundle_errors(agent.root), [])

    def test_a_typescript_script_must_inline_the_sdk(self):
        agent = Agent(self, language='"typescript"', command='["node", "agent.mjs"]')
        agent.write("agent.mjs", 'import { run } from "@jaah/agent-sdk";\n')
        self.assertEqual(cc.bundle_errors(agent.root), [
            "`command` runs agent.mjs, which imports the Jaah SDK instead of inlining it: run the Agent SDK's "
            f"examples/vendor.py {agent.root}"])
        agent.write("agent.mjs", "// bundled\nclass Extension {}\n")
        self.assertEqual(cc.bundle_errors(agent.root), [
            "`command` runs agent.mjs, whose dependencies the node installs from a package.json and "
            "package-lock.json beside it, and one is missing"])
        agent.write("package.json", "{}").write("package-lock.json", "{}")
        self.assertEqual(cc.bundle_errors(agent.root), [])

    def test_a_command_running_no_script_is_named(self):
        agent = Agent(self, command='["python3", "missing.py"]')
        self.assertEqual(cc.bundle_errors(agent.root), ["`command` python3 missing.py runs no script in the agent root"])


class CatalogTest(unittest.TestCase):
    def run_main(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = cc.main(list(argv))
        return code, out.getvalue()

    def test_every_catalog_connector_passes(self):
        code, out = self.run_main("--json")
        report = json.loads(out)
        self.assertGreaterEqual(len(report), 2)
        self.assertEqual({k: v for k, v in report.items() if v}, {})
        self.assertEqual(code, 0)

    def test_catalog_directory_must_be_named_after_the_manifest(self):
        agent = Agent(self).write("README.md", "# x\n").write("tests/test_x.py", "")
        self.assertEqual(cc.catalog_errors(agent.root),
                         ["The connector directory must be named after `name`: connectors/hello-agent"])

    def test_catalog_layout_needs_a_readme_and_tests(self):
        agent = Agent(self)
        named = os.path.join(os.path.dirname(agent.root), "hello-agent")
        shutil.rmtree(named, ignore_errors=True)
        os.rename(agent.root, named)
        agent.root = named
        self.addCleanup(shutil.rmtree, named, True)
        self.assertEqual(cc.catalog_errors(agent.root), [
            "A catalog connector needs a README.md",
            "A catalog connector needs tests under tests/ (test_*.py or *.test.js)"])
        agent.write("README.md", "# x\n").write("tests/test_x.py", "")
        self.assertEqual(cc.catalog_errors(agent.root), [])

    def test_text_output_names_each_failure(self):
        agent = Agent(self, language='"ruby"')
        code, out = self.run_main(agent.root)
        self.assertEqual(code, 1)
        self.assertIn("- `language` must be python or typescript, got ruby", out)


if __name__ == "__main__":
    unittest.main()
