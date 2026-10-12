"""Run the Codex CLI headlessly (`codex exec --json`) and read what it did.

The OpenAI key reaches Codex ONLY through the child's environment. It is never an argument (argv is
world-readable in /proc), never written to a file, and every piece of Codex output this module hands back
has been scrubbed of it first — Codex itself echoes a partial key (`sk-ab**wxyz`) in its own 401 errors,
which the SDK's exact-value masking would not catch."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from gitops import safe_env

DEFAULT_MODEL = "gpt-5-codex"
# The env var a project (or a local dry run) may set to pick another Codex model.
MODEL_ENV = "JAAH_CODEX_MODEL"
CODEX_TIMEOUT_S = 60 * 60
# What Codex's final message must look like: the connector's whole contract with the model.
RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "commit_title", "question", "options", "blocker_kind",
                 "security_surfaces"],
    "properties": {
        "status": {"type": "string", "enum": ["done", "no_change", "blocked"]},
        "summary": {"type": "string"},
        "commit_title": {"type": "string"},
        "question": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}},
        "blocker_kind": {"type": "string",
                         "enum": ["none", "decision", "Credential", "Access", "Human act", "External"]},
        "security_surfaces": {"type": "array", "items": {"type": "string"}},
    },
}
# Codex's own setting for what its model-run shell commands inherit: only the core variables (PATH, HOME…),
# so a command the model runs never sees OPENAI_API_KEY or anything else in this process's environment.
SHELL_POLICY = 'shell_environment_policy.inherit="core"'


@dataclass
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: dict) -> None:
        self.input_tokens += int(other.get("input_tokens") or 0)
        self.cached_input_tokens += int(other.get("cached_input_tokens") or 0)
        self.output_tokens += int(other.get("output_tokens") or 0)


@dataclass
class CodexResult:
    exit_code: int
    result: dict | None          # the schema-shaped final message, or None when Codex gave none
    usage: Usage = field(default_factory=Usage)
    errors: list[str] = field(default_factory=list)   # scrubbed error messages from the event stream
    stderr_tail: str = ""        # scrubbed


def scrub(text: str, secret: str) -> str:
    """Remove the secret AND the fragments Codex prints of it (`sk-abcd**wxyz`)."""
    if not secret or not text:
        return text
    out = text.replace(secret, "<redacted:openai-key>")
    if len(secret) >= 12:
        head, tail = re.escape(secret[:8]), re.escape(secret[-4:])
        out = re.sub(rf"{head}\S*|\S*\*\*+{tail}", "<redacted:openai-key>", out)
    return out


def model() -> str:
    return os.environ.get(MODEL_ENV) or DEFAULT_MODEL


def child_env(api_key: str, codex_home: str) -> dict:
    env = safe_env()   # never the SDK socket: model-run commands must not settle or fail the task
    env["OPENAI_API_KEY"] = api_key
    env["CODEX_API_KEY"] = api_key   # `codex exec` reads this one for headless API-key auth
    env["CODEX_HOME"] = codex_home   # a private home: never the node's own Codex login (no Jaah Accounts)
    return env


def command(prompt: str, worktree: str, schema_path: str, last_message_path: str) -> list[str]:
    return ["codex", "exec", "--json", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
            "--sandbox", "workspace-write", "-c", SHELL_POLICY, "--model", model(), "--cd", worktree,
            "--output-schema", schema_path, "--output-last-message", last_message_path, prompt]


def parse_events(stdout: str, secret: str) -> tuple[Usage, list[str]]:
    usage, errors = Usage(), []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "turn.completed":
            usage.add(event.get("usage") or {})
        elif kind == "turn.failed":
            errors.append(scrub(str((event.get("error") or {}).get("message") or "turn failed"), secret))
        elif kind == "error":
            errors.append(scrub(str(event.get("message") or "error"), secret))
    return usage, errors


def run(prompt: str, worktree: str, api_key: str, *, timeout: float = CODEX_TIMEOUT_S) -> CodexResult:
    scratch = tempfile.mkdtemp(prefix="jaah-codex-")   # outside the worktree: nothing here gets committed
    try:
        home = os.path.join(scratch, "home")
        os.mkdir(home, 0o700)
        schema_path = os.path.join(scratch, "schema.json")
        last_path = os.path.join(scratch, "last.json")
        with open(schema_path, "w", encoding="utf-8") as fh:
            json.dump(RESULT_SCHEMA, fh)
        try:
            proc = subprocess.run(command(prompt, worktree, schema_path, last_path), cwd=worktree,
                                  env=child_env(api_key, home), stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=timeout)
            code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except OSError as exc:   # no `codex` on PATH
            code, stdout, stderr = 127, "", f"could not run codex: {exc}"
        except subprocess.TimeoutExpired as exc:
            code, stdout = 124, _text(exc.stdout)
            stderr = _text(exc.stderr) + f"\ncodex exec timed out after {timeout:g}s"
        usage, errors = parse_events(stdout, api_key)
        return CodexResult(exit_code=code, result=_read_result(last_path), usage=usage, errors=errors,
                           stderr_tail=scrub(stderr, api_key)[-2000:])
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _text(data: bytes | str | None) -> str:
    if data is None:
        return ""
    return data.decode("utf-8", "replace") if isinstance(data, bytes) else data


def _read_result(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.loads(fh.read())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("status") not in ("done", "no_change", "blocked"):
        return None
    out = {k: str(data.get(k) or "") for k in ("status", "summary", "commit_title", "question", "blocker_kind")}
    for k in ("options", "security_surfaces"):
        out[k] = [str(x) for x in data.get(k) or [] if isinstance(x, (str, int, float))]
    return out


def scrub_result(result: dict, secret: str) -> dict:
    """Codex's final message goes into commits, the PR and settle text: the key must not ride along."""
    return {k: (scrub(v, secret) if isinstance(v, str) else [scrub(x, secret) for x in v])
            for k, v in result.items()}


def leaks(text: str, secret: str) -> bool:
    return scrub(text, secret) != text
