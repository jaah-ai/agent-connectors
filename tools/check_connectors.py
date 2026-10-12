#!/usr/bin/env python3
"""Check connectors against the manifest standard and the catalog's layout rules — before Jaah does.

    python3 tools/check_connectors.py                       # every connector under connectors/
    python3 tools/check_connectors.py path/to/agent ...     # just these agent roots
    python3 tools/check_connectors.py --sdk-version 0.1.0   # also: each `sdk` range admits this version

A contributor's pre-flight, and a CI gate. It mirrors the rules Jaah applies when an agent is registered
(the authority is Jaah's own validator; the manifest standard is written out in CONTRIBUTING.md), and
reports every problem as one sentence naming the field — the same shape Jaah's errors take. Stdlib only.
Exit 0 when everything passes, 1 when anything fails."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib

MANIFEST = "jaah-agent.toml"
SDK_PERSONAS = ("coder", "business_analysis", "architecture", "plan", "finish", "researcher")
TYPES = ("coding", "non_coding", "cli_wrapper")
LANGUAGES = ("python", "typescript")
SDK_PACKAGES = ("jaah-agent", "jaah_agent", "@jaah/agent-sdk")
INSTALL_FILES = ("requirements.txt", "pyproject.toml", "package.json", "package-lock.json")
SLUG = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")
MAINTAINER = re.compile(r"^(@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(/[A-Za-z0-9._-]+)?|[^<>]+ <[^<>@\s]+@[^<>@\s]+>)$")
SPECIFIER = re.compile(r"^\s*(~=|==|!=|<=|>=|<|>)\s*(\d+(?:\.\d+)*)\s*$")
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
    entry = text("entrypoint")
    if entry is not None:
        parts = entry.replace("\\", "/").split("/")
        if entry.startswith("/") or ".." in parts or not entry.strip():
            errors.append(f"`entrypoint` must be a relative path inside the agent root, got {entry}")
        elif not os.path.isfile(os.path.join(root, entry)) or not _inside(root, os.path.join(root, entry)):
            errors.append(f"`entrypoint` {entry} is not in the agent root")
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
    sdk = text("sdk")
    if sdk is not None and parse_range(sdk) is None:
        errors.append(f"`sdk` must be a version range such as \">=0.1,<0.2\", got {sdk!r}")
    runtime = data.get("runtime", {})
    if not isinstance(runtime, dict) or not isinstance(runtime.get("install", True), bool):
        errors.append("`runtime.install` must be true or false")
    errors += sdk_dependency_errors(root)
    return errors


def _inside(root: str, path: str) -> bool:
    """`path` resolves inside `root` (a symlink pointing out of the agent root does not count)."""
    real_root = os.path.realpath(root)
    return os.path.realpath(path).startswith(real_root + os.sep)


def sdk_dependency_errors(root: str) -> list[str]:
    """The node provides the SDK: naming it in an install file is refused at registration and at launch."""
    out = []
    for name in INSTALL_FILES:
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        for package in SDK_PACKAGES:
            if re.search(rf"(^|[\"'\s/]){re.escape(package)}([\"'\s=<>~!\[;@]|$)", body, re.M):
                out.append(f"Remove {package} from {name}: the node provides the Jaah SDK")
                break
    return out


def parse_range(spec: str) -> list[tuple[str, tuple[int, ...]]] | None:
    clauses = []
    for part in spec.split(","):
        m = SPECIFIER.match(part)
        if not m:
            return None
        clauses.append((m.group(1), tuple(int(x) for x in m.group(2).split("."))))
    return clauses or None


def admits(spec: str, version: str) -> bool:
    v = tuple(int(x) for x in re.match(r"\d+(?:\.\d+)*", version).group(0).split("."))
    for op, bound in parse_range(spec) or []:
        width = max(len(v), len(bound))
        a, b = v + (0,) * (width - len(v)), bound + (0,) * (width - len(bound))
        if op == "~=":
            ok = a >= b and a[: len(bound) - 1] == bound[:-1]
        else:
            ok = {"==": a == b, "!=": a != b, "<=": a <= b, ">=": a >= b, "<": a < b, ">": a > b}[op]
        if not ok:
            return False
    return True


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
    parser.add_argument("--sdk-version", help="also require each `sdk` range to admit this SDK version")
    parser.add_argument("--json", action="store_true", help="print {root: [errors]} as JSON")
    args = parser.parse_args(argv)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots = args.roots or catalog_roots(repo)
    report: dict[str, list[str]] = {}
    for root in roots:
        errors = validate(root)
        if os.path.abspath(root).startswith(os.path.join(repo, CATALOG_DIR) + os.sep):
            errors += catalog_errors(root)
        if args.sdk_version and not errors:
            with open(os.path.join(root, MANIFEST), "rb") as fh:
                spec = tomllib.load(fh)["agent"]["sdk"]
            if not admits(spec, args.sdk_version):
                errors.append(f"`sdk` {spec} does not admit the SDK version under test, {args.sdk_version}")
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
