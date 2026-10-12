"""tools/check_connectors.py: one sentence per bad field, asserted verbatim."""
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
    "type": '"non_coding"', "language": '"python"', "entrypoint": '"main.py"', "version": '"0.1.0"',
    "maintainer": '"@octocat"', "personas": '["coder"]', "sdk": '">=0.1,<0.2"',
}


class Agent:
    def __init__(self, test: unittest.TestCase, **overrides):
        self.root = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, self.root, True)
        fields = {**VALID, **overrides}
        body = "[agent]\n" + "".join(f"{k} = {v}\n" for k, v in fields.items() if v is not None)
        self.write(cc.MANIFEST, body)
        self.write("main.py", "print('hi')\n")

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
            "sdk": ('"latest"', "`sdk` must be a version range such as \">=0.1,<0.2\", got 'latest'"),
        }
        for field, (value, sentence) in cases.items():
            self.assertEqual(self.errors(**{field: value}), [sentence], field)

    def test_entrypoint_rules(self):
        self.assertEqual(self.errors(entrypoint='"agent/run.py"'), ["`entrypoint` agent/run.py is not in the agent root"])
        self.assertEqual(self.errors(entrypoint='"../main.py"'),
                         ["`entrypoint` must be a relative path inside the agent root, got ../main.py"])
        self.assertEqual(self.errors(entrypoint='"/etc/passwd"'),
                         ["`entrypoint` must be a relative path inside the agent root, got /etc/passwd"])

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

    def test_runtime_install_must_be_a_boolean(self):
        agent = Agent(self)
        with open(os.path.join(agent.root, cc.MANIFEST), "a") as fh:
            fh.write('[runtime]\ninstall = "yes"\n')
        self.assertEqual(cc.validate(agent.root), ["`runtime.install` must be true or false"])

    def test_the_sdk_in_an_install_file_is_refused(self):
        cases = {
            "requirements.txt": ("requests==2.32\njaah-agent>=0.1\n", "jaah-agent"),
            "pyproject.toml": ('[project]\ndependencies = ["jaah_agent"]\n', "jaah_agent"),
            "package.json": ('{"dependencies": {"@jaah/agent-sdk": "^0.1.0"}}', "@jaah/agent-sdk"),
            "package-lock.json": ('{"packages": {"node_modules/@jaah/agent-sdk": {}}}', "@jaah/agent-sdk"),
        }
        for name, (body, package) in cases.items():
            agent = Agent(self).write(name, body)
            self.assertEqual(cc.validate(agent.root), [f"Remove {package} from {name}: the node provides the Jaah SDK"])

    def test_a_similar_package_name_is_not_the_sdk(self):
        agent = Agent(self).write("requirements.txt", "jaah-agent-tools==1.0\nmy_jaah_agent_helpers\n")
        self.assertEqual(cc.validate(agent.root), [])


class RangeTest(unittest.TestCase):
    def test_admits(self):
        self.assertTrue(cc.admits(">=0.1,<0.2", "0.1.0"))
        self.assertTrue(cc.admits(">=0.1,<0.2", "0.1.7"))
        self.assertFalse(cc.admits(">=0.1,<0.2", "0.2.0"))
        self.assertTrue(cc.admits("~=0.1.2", "0.1.9"))
        self.assertFalse(cc.admits("~=0.1.2", "0.2.0"))
        self.assertTrue(cc.admits("==0.1.0", "0.1"))
        self.assertTrue(cc.admits("!=0.1.1", "0.1.0"))
        self.assertFalse(cc.admits(">0.1, <=0.1.5", "0.1.6"))
        self.assertIsNone(cc.parse_range(">=0.1,"))


class CatalogTest(unittest.TestCase):
    def run_main(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = cc.main(list(argv))
        return code, out.getvalue()

    def test_every_catalog_connector_passes_against_the_sdk_under_test(self):
        code, out = self.run_main("--sdk-version", "0.1.0", "--json")
        report = json.loads(out)
        self.assertGreaterEqual(len(report), 2)
        self.assertEqual({k: v for k, v in report.items() if v}, {})
        self.assertEqual(code, 0)

    def test_an_sdk_version_outside_the_range_fails(self):
        code, out = self.run_main("--sdk-version", "0.2.0")
        self.assertEqual(code, 1)
        self.assertIn("does not admit the SDK version under test, 0.2.0", out)

    def test_catalog_directory_must_be_named_after_the_manifest(self):
        agent = Agent(self).write("README.md", "# x\n").write("tests/test_x.py", "")
        self.assertEqual(cc.catalog_errors(agent.root),
                         ["The connector directory must be named after `name`: connectors/hello-agent"])

    def test_an_entrypoint_symlinked_out_of_the_root_is_not_in_it(self):
        agent = Agent(self, entrypoint='"main2.py"')
        outside = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
        self.addCleanup(os.unlink, outside.name)
        os.symlink(outside.name, os.path.join(agent.root, "main2.py"))
        self.assertEqual(cc.validate(agent.root), ["`entrypoint` main2.py is not in the agent root"])

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
