"""A fake Jaah node for connector and fixture tests: the client half of the `jaah.ai` ACP extension, with canned answers.

A test hands `FakeNode` a handler `(verb, params) -> answer` (raise `refuse(...)` to answer with an error) and drives
an agent two ways:

  * in-process — `node.turn(text, cwd=...)` builds the SDK's real `jaah_agent.Turn` over a real `Extension` whose
    calls this node answers, so an agent's `prompt()` can be awaited directly (and its module patched);
  * over ACP — `node.run([...command], prompts, cwd=...)` spawns the agent's bundle (its folder, as the node runs it)
    and speaks the protocol to it, one prompt per turn, until it settles or the prompts run out.

Every call is recorded in `calls`, every `session/update` in `updates`. Nothing here reaches a network or a Jaah node.
Needs `jaah_agent` and `acp` importable: a test puts a generated agent folder (examples/vendor.py, its dependencies
installed into it) on `sys.path`.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Iterable, Sequence
from types import SimpleNamespace
from typing import Any

import acp
import jaah_agent
from acp.schema import ClientCapabilities, Implementation, RequestPermissionResponse

NAMESPACE = "jaah.ai"
#: The verbs a connector here calls; the node advertises exactly these.
VERBS = ("task.get", "project.get", "task.complete", "task.fail", "artifact.upload", "input.request",
         "credential.list", "credential.get", "pull_request.enqueue")
SETTLES = ("task.complete", "task.fail")
#: The personas whose task may call a `project.*` verb; the control plane refuses every other one with a 403
#: (`PROJECT_SETTINGS_LICENCE_BY_PERSONA` in the Jaah control plane's persona registry).
PROJECT_SETTINGS_PERSONAS = ("onboard",)
Handler = Callable[[str, dict[str, Any]], dict[str, Any]]


def refuse(message: str, code: int = -32602) -> acp.RequestError:
    """The error a handler raises to refuse a call, as the node answers one it will not take."""
    return acp.RequestError(code, message)


def settled(verb: str, params: dict[str, Any]) -> dict[str, Any]:
    """The default answer: settles are accepted, anything else gets an empty object."""
    return {"stored": True, "settled": True, "state": "done"} if verb in SETTLES else {}


class _Conn:
    def __init__(self, node: FakeNode) -> None:
        self._node = node

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self._node.updates.append(update)


class FakeNode:
    def __init__(self, handler: Handler = settled, verbs: Iterable[str] = VERBS, *, persona: str = "coder") -> None:
        self.handler = handler
        self.persona = persona  # the task's persona: a `project.*` call is refused unless it holds the licence
        self.methods = [jaah_agent.method_for(v) for v in verbs]
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.updates: list[Any] = []
        self.stop_reasons: list[str] = []
        self.returncode: int | None = None
        self._session: Any = None

    # --- what a test reads ---------------------------------------------------------------------------------------

    def verbs(self) -> list[str]:
        return [verb for verb, _ in self.calls]

    def params(self, verb: str) -> list[dict[str, Any]]:
        return [params for v, params in self.calls if v == verb]

    def said(self) -> str:
        """Every agent message chunk's text, joined."""
        return "".join(getattr(getattr(u, "content", None), "text", "") for u in self.updates
                       if getattr(u, "session_update", "") == "agent_message_chunk")

    def usage(self) -> list[dict[str, Any]]:
        """The token counts each `usage_update` carried in its `_meta["jaah.ai"]["usage"]`."""
        return [((u.field_meta or {}).get(NAMESPACE) or {}).get("usage") or {} for u in self.updates
                if getattr(u, "session_update", "") == "usage_update"]

    @property
    def settled(self) -> bool:
        return any(verb in SETTLES for verb in self.verbs())

    # --- the extension's client half ------------------------------------------------------------------------------

    def _answer(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method not in self.methods:
            raise acp.RequestError.method_not_found(method)
        verb = jaah_agent.verb_for(method)
        self.calls.append((verb, params))
        if verb.startswith("project.") and self.persona not in PROJECT_SETTINGS_PERSONAS:
            raise refuse(f"{method} refused: HTTP 403 task (persona={self.persona!r}) may not read or write its "
                         f"project's settings")
        return self.handler(verb, params)

    async def _send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._answer(method, params)
        except acp.RequestError as err:
            raise jaah_agent.ExtensionError(method, err.code, str(err), err.data) from None

    def turn(self, text: str, *, cwd: str) -> jaah_agent.Turn:
        """An in-process turn of this node's one session (created on the first call)."""
        if self._session is None:
            extension = jaah_agent.Extension(self._send, frozenset(self.methods))
            self._session = SimpleNamespace(id="s1", cwd=cwd, meta={}, extension=extension)
        return jaah_agent.Turn(_Conn(self), self._session, [acp.text_block(text)])

    # --- acp.Client, for a spawned agent --------------------------------------------------------------------------

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append(update)

    async def request_permission(self, session_id: str, tool_call: Any, options: list[Any], **kwargs: Any) -> Any:
        return RequestPermissionResponse(outcome={"outcome": "cancelled"})

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._answer(f"_{method}", params)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    def run(self, command: Sequence[str], prompts: Sequence[str], *, cwd: str, env: dict[str, str] | None = None,
            timeout: float = 60) -> FakeNode:
        """Spawn `command` in `cwd` and send `prompts` one turn each, stopping once the agent settles; the process's
        exit code lands in `returncode` (None while it was still running when the session closed)."""
        asyncio.run(self._run(list(command), list(prompts), cwd, {**os.environ, **(env or {})}, timeout))
        return self

    async def _run(self, command: list[str], prompts: list[str], cwd: str, env: dict[str, str],
                   timeout: float) -> None:
        async with acp.spawn_agent_process(self, command[0], *command[1:], cwd=cwd, env=env) as (conn, process):
            try:
                await asyncio.wait_for(conn.initialize(
                    protocol_version=acp.PROTOCOL_VERSION,
                    client_capabilities=ClientCapabilities(field_meta={NAMESPACE: {"version": 1,
                                                                                   "methods": self.methods}}),
                    client_info=Implementation(name="fake-node", version="2.0.0")), timeout)
                session = await asyncio.wait_for(conn.new_session(cwd=cwd, mcp_servers=[]), timeout)
                for text in prompts:
                    answer = await asyncio.wait_for(
                        conn.prompt(session_id=session.session_id, prompt=[acp.text_block(text)]), timeout)
                    self.stop_reasons.append(str(answer.stop_reason))
                    if self.settled:
                        break
            except (ConnectionError, acp.RequestError, asyncio.IncompleteReadError):
                pass  # the agent went away mid-session: its exit code says how
            try:
                self.returncode = await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                self.returncode = None
