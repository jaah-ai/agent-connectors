#!/usr/bin/env python3
"""Check connectors against the manifest standard (manifest 2, SDK v2) and the catalog's layout rules — before Jaah does.

    python3 tools/check_connectors.py                       # every connector under connectors/
    python3 tools/check_connectors.py path/to/agent ...     # just these agent roots
    python3 tools/check_connectors.py --bundled             # also: each folder carries the SDK its `command` imports

A contributor's pre-flight, and a CI gate. It mirrors the rules Jaah applies when an agent is registered
(the authority is Jaah's own validator; the manifest standard is written out in CONTRIBUTING.md), and
reports every problem as one sentence naming the field — the same shape Jaah's errors take. `--bundled` is for a
folder the Agent SDK's examples/vendor.py has generated (the published tree, or CI after generating): the SDK beside
the script and the dependency list the node installs from (requirements.txt, or package.json with
package-lock.json). Stdlib only.
Exit 0 when everything passes, 1 when anything fails."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import tomllib

MANIFEST = "jaah-agent.toml"
SDK_PERSONAS = ("coder", "business_analysis", "architecture", "plan", "finish", "researcher")
TYPES = ("coding", "non_coding", "cli_wrapper")
LANGUAGES = ("python", "typescript")
MANIFEST_VERSION = 2
#: Manifest 1 fields: a manifest 2 agent names `command`, and the node installs only what its folder lists.
V1_FIELDS = ("entrypoint", "sdk")
#: What a bundle carries beside the script `command` runs, when that script imports the SDK.
SDK_IMPORT = {"python": re.compile(r"^\s*(import|from)\s+jaah_agent\b", re.M),
              "typescript": re.compile(r"""from\s+["']@jaah/agent-sdk["']|require\(\s*["']@jaah/agent-sdk["']""")}
SCRIPT_SUFFIXES = {".py": "python", ".mjs": "typescript", ".js": "typescript", ".cjs": "typescript"}
SLUG = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")
MAINTAINER = re.compile(r"^(@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(/[A-Za-z0-9._-]+)?|[^<>]+ <[^<>@\s]+@[^<>@\s]+>)$")
CATALOG_DIR = "connectors"


def validate(root: str) -> list[str]:
    """Every problem with the agent rooted at `root`, as sentences. Empty means valid."""
    path = os.path.join(root, MANIFEST)
    if not os.path.isfile(path):
        return [f"No {MANIFEST} at the agent root ({root})"]
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        return [f"{MANIFEST} is not valid TOML: {exc}"]
    agent = data.get("agent")
    if not isinstance(agent, dict):
        return [f"{MANIFEST} has no [agent] table"]
    errors: list[str] = []

    def text(field: str) -> str | None:
        value = agent.get(field)
        if value is None:
            errors.append(f"`{field}` is missing")
            return None
        if not isinstance(value, str):
            errors.append(f"`{field}` must be a string, got {type(value).__name__}")
            return None
        return value

    name = text("name")
    if name is not None and not SLUG.match(name):
        errors.append(f"`name` must be a slug (a lowercase letter, then 1-39 lowercase letters, digits or "
                      f"hyphens), got {name!r}")
    display = text("display_name")
    if display is not None and not 1 <= len(display.strip()) <= 60:
        errors.append(f"`display_name` must be 1-60 characters, got {len(display.strip())}")
    description = agent.get("description", "")
    if not isinstance(description, str) or len(description) > 280:
        errors.append("`description` must be text of at most 280 characters")
    kind = text("type")
    if kind is not None and kind not in TYPES:
        errors.append(f"`type` must be {', '.join(TYPES[:-1])} or {TYPES[-1]}, got {kind}")
    language = text("language")
    if language is not None and language not in LANGUAGES:
        errors.append(f"`language` must be python or typescript, got {language}")
    manifest = agent.get("manifest")
    if manifest != MANIFEST_VERSION or type(manifest) is not int:
        errors.append(f"`manifest` must be {MANIFEST_VERSION} (SDK v2), got {manifest!r}")
    for field in V1_FIELDS:
        if field in agent:
            errors.append(f"`{field}` is a manifest 1 field; a manifest 2 agent names `command` instead")
    command = agent.get("command")
    if command is None:
        errors.append("`command` is missing")
    elif not isinstance(command, list) or not command or not all(isinstance(w, str) and w.strip() for w in command):
        shown = shlex.join(command) if isinstance(command, list) and all(isinstance(w, str) for w in command) \
            else command
        errors.append(f"`command` must be a list of words such as [\"python3\", \"agent.py\"], got {shown}")
    version = text("version")
    if version is not None and not SEMVER.match(version):
        errors.append(f"`version` must be semver (e.g. 0.1.0), got {version}")
    maintainer = text("maintainer")
    if maintainer is not None and not MAINTAINER.match(maintainer.strip()):
        errors.append(f"`maintainer` must be a GitHub handle (@name) or \"Name <email>\", got {maintainer!r}")
    personas = agent.get("personas")
    if not isinstance(personas, list) or not personas:
        errors.append("`personas` must list at least one persona")
    else:
        unknown = [p for p in personas if p not in SDK_PERSONAS]
        if unknown:
            errors.append(f"`personas` may only name {', '.join(SDK_PERSONAS)}; got {', '.join(map(str, unknown))}")
    runtime = data.get("runtime", {})
    if not isinstance(runtime, dict) or "install" in runtime:
        errors.append("`install` in [runtime] is a manifest 1 field: a manifest 2 agent lists its dependencies in "
                      "requirements.txt or package.json beside its script, and the node installs them")
    return errors


def bundle_errors(root: str) -> list[str]:
    """The node installs only what a folder lists: a `command` whose script imports the SDK must run from a folder
    carrying it and its dependency list; a bundled script needs the package.json its imports install from."""
    with open(os.path.join(root, MANIFEST), "rb") as fh:
        command = tomllib.load(fh)["agent"]["command"]
    for word in command[1:]:
        kind = SCRIPT_SUFFIXES.get(os.path.splitext(word)[1])
        script = os.path.join(root, word)
        if kind and os.path.isfile(script):
            break
    else:
        return [f"`command` {shlex.join(command)} runs no script in the agent root"]
    here = os.path.dirname(script)
    with open(script, encoding="utf-8", errors="replace") as fh:
        imports_sdk = bool(SDK_IMPORT[kind].search(fh.read()))
    if kind == "python" and imports_sdk:
        missing = [name for name in ("jaah_agent/__init__.py", "requirements.txt")
                   if not os.path.isfile(os.path.join(here, name))]
        if missing:
            return [f"`command` runs {word}, which imports the Jaah SDK, but {' and '.join(missing)} missing "
                    f"beside it: run the Agent SDK's examples/vendor.py {root}"]
    if kind == "typescript" and imports_sdk:
        return [f"`command` runs {word}, which imports the Jaah SDK instead of inlining it: run the Agent SDK's "
                f"examples/vendor.py {root}"]
    if kind == "typescript" and not all(os.path.isfile(os.path.join(here, name))
                                        for name in ("package.json", "package-lock.json")):
        return [f"`command` runs {word}, whose dependencies the node installs from a package.json and "
                f"package-lock.json beside it, and one is missing"]
    return []


def catalog_errors(root: str) -> list[str]:
    """What the catalog asks of a connector beyond a valid manifest (CONTRIBUTING.md, "Connector structure")."""
    out = []
    try:
        with open(os.path.join(root, MANIFEST), "rb") as fh:
            name = tomllib.load(fh).get("agent", {}).get("name")
    except (OSError, tomllib.TOMLDecodeError):
        name = None
    if name and name != os.path.basename(os.path.normpath(root)):
        out.append(f"The connector directory must be named after `name`: connectors/{name}")
    if not os.path.isfile(os.path.join(root, "README.md")):
        out.append("A catalog connector needs a README.md")
    tests = os.path.join(root, "tests")
    if not os.path.isdir(tests) or not any(f.startswith("test") for f in os.listdir(tests)):
        out.append("A catalog connector needs tests under tests/ (test_*.py or *.test.js)")
    return out


def catalog_roots(repo: str) -> list[str]:
    base = os.path.join(repo, CATALOG_DIR)
    return sorted(os.path.join(base, d) for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("roots", nargs="*", help="agent roots (default: every connector under connectors/)")
    parser.add_argument("--bundled", action="store_true",
                        help="also require each agent's folder to carry the SDK its `command` imports")
    parser.add_argument("--json", action="store_true", help="print {root: [errors]} as JSON")
    args = parser.parse_args(argv)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots = args.roots or catalog_roots(repo)
    report: dict[str, list[str]] = {}
    for root in roots:
        errors = validate(root)
        if os.path.abspath(root).startswith(os.path.join(repo, CATALOG_DIR) + os.sep):
            errors += catalog_errors(root)
        if args.bundled and not errors:
            errors += bundle_errors(root)
        report[os.path.relpath(root, repo)] = errors
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for root, errors in report.items():
            print(f"{'FAIL' if errors else 'ok  '} {root}")
            for error in errors:
                print(f"     - {error}")
    return 1 if any(report.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
