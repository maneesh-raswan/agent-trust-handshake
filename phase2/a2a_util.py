"""
Thin helpers over the A2A SDK so each agent service stays small.

Wire convention used by every agent in this project:
  request  = one A2A Message with one data part: {"skill": <id>, "args_json": "<json>"}
  response = an A2A Task whose final state carries the decision
             (COMPLETED, REJECTED, INPUT_REQUIRED, FAILED) and one data artifact
             {"result_json": "<json>"}.

Payloads travel as JSON *strings* inside the data part on purpose. A2A data parts
are protobuf Values, where every number is a double, so 1792440103 would come back
as 1792440103.0 and 12500.0 as 12500. Signatures are over canonical JSON, so any
such drift would silently break verification.
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
import uvicorn
from google.protobuf.json_format import MessageToDict
from starlette.applications import Starlette

from a2a.client import ClientConfig, create_client
from a2a.helpers import new_data_artifact, new_data_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Message,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.types import Part  # noqa: F401  (re-exported for callers)

COMPLETED = TaskState.TASK_STATE_COMPLETED
REJECTED = TaskState.TASK_STATE_REJECTED
INPUT_REQUIRED = TaskState.TASK_STATE_INPUT_REQUIRED
FAILED = TaskState.TASK_STATE_FAILED

STATE_NAMES = {
    COMPLETED: "completed",
    REJECTED: "rejected",
    INPUT_REQUIRED: "input-required",
    FAILED: "failed",
}


@dataclass
class Outcome:
    """What a skill handler returns."""
    state: int
    data: dict = field(default_factory=dict)
    text: str = ""


Handler = Callable[[dict], Awaitable[Outcome]]


class SkillExecutor(AgentExecutor):
    """Dispatches an incoming data message to a registered async skill handler."""

    def __init__(self, handlers: dict[str, Handler]):
        self.handlers = handlers

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id, context_id = context.task_id, context.context_id
        try:
            request = _first_data(context.message)
            skill, args = request["skill"], json.loads(request.get("args_json") or "{}")
            handler = self.handlers[skill]
        except (KeyError, TypeError, IndexError, ValueError):
            outcome = Outcome(FAILED, {}, "malformed request or unknown skill")
        else:
            try:
                outcome = await handler(args)
            except Exception as exc:  # a skill bug must not kill the service
                outcome = Outcome(FAILED, {}, f"internal error: {exc}")

        await event_queue.enqueue_event(Task(
            id=task_id, context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        ))
        if outcome.data:
            await event_queue.enqueue_event(TaskArtifactUpdateEvent(
                task_id=task_id, context_id=context_id,
                artifact=new_data_artifact("result", {"result_json": json.dumps(outcome.data)}),
            ))
        await event_queue.enqueue_event(TaskStatusUpdateEvent(
            task_id=task_id, context_id=context_id,
            status=TaskStatus(
                state=outcome.state,
                message=Message(
                    role=Role.ROLE_AGENT, message_id=str(uuid.uuid4()),
                    task_id=task_id, context_id=context_id,
                    parts=[_text_part(outcome.text)] if outcome.text else [],
                ),
            ),
        ))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None


def _text_part(text: str):
    from a2a.helpers import new_text_part
    return new_text_part(text)


def _first_data(message: Message) -> dict:
    for part in message.parts:
        if part.HasField("data"):
            return MessageToDict(part.data)
    raise KeyError("no data part")


def make_card(name: str, description: str, port: int, skills: list[tuple[str, str]],
              extensions: list | None = None) -> AgentCard:
    from a2a.types import AgentExtension
    exts = [AgentExtension(uri=u, description=d, params=p) for u, d, p in (extensions or [])]
    return AgentCard(
        name=name,
        description=description,
        version="0.2.0",
        supported_interfaces=[AgentInterface(
            url=f"http://127.0.0.1:{port}/", protocol_binding="JSONRPC", protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=False, extensions=exts),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[AgentSkill(id=i, name=i, description=d, tags=["trust"]) for i, d in skills],
    )


def build_app(card: AgentCard, handlers: dict[str, Handler]) -> Starlette:
    handler = DefaultRequestHandler(
        agent_executor=SkillExecutor(handlers),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    routes = create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/")
    return Starlette(routes=routes)


async def serve(app: Starlette, port: int) -> None:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    await uvicorn.Server(config).serve()


async def call(url: str, skill: str, args: dict | None = None, timeout: float = 15.0) -> Outcome:
    """Send one skill request to an A2A agent and return its final outcome."""
    async with httpx.AsyncClient(timeout=timeout) as http:
        client = await create_client(url, ClientConfig(streaming=False, httpx_client=http))
        request = SendMessageRequest(message=new_data_message(
            {"skill": skill, "args_json": json.dumps(args or {})}, role=Role.ROLE_USER))
        state, data, text = TaskState.TASK_STATE_UNSPECIFIED, {}, ""
        async for resp in client.send_message(request):
            task = resp.task if resp.HasField("task") else None
            if task is None:
                continue
            state = task.status.state
            if task.status.HasField("message"):
                text = " ".join(p.text for p in task.status.message.parts if p.HasField("text"))
            for art in task.artifacts:
                for part in art.parts:
                    if part.HasField("data"):
                        data = json.loads(MessageToDict(part.data)["result_json"])
        return Outcome(state, data, text)


async def fetch_card(url: str) -> dict:
    async with httpx.AsyncClient(timeout=5) as http:
        r = await http.get(url.rstrip("/") + "/.well-known/agent-card.json")
        r.raise_for_status()
        return r.json()


async def wait_ready(url: str, attempts: int = 60) -> None:
    for _ in range(attempts):
        try:
            await fetch_card(url)
            return
        except Exception:
            await asyncio.sleep(0.25)
    raise RuntimeError(f"agent at {url} did not become ready")


def log_event(path: str | None, actor: str, kind: str, **detail: Any) -> None:
    """Append one structured line to the shared run log the scenario runner tails."""
    entry = {"t": round(time.time(), 3), "actor": actor, "kind": kind, **detail}
    if path:
        with Path(path).open("a") as f:
            f.write(json.dumps(entry) + "\n")
