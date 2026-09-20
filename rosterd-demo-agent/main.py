"""FastAPI service. Endpoints required by the contract: POST /invoke, GET /graph.
Additive extras (don't affect the contract): GET /health, POST /resume."""

import uuid

from fastapi import FastAPI, HTTPException
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import BaseModel

from brain import current_mode, llm_key_present
from demo_agent import GraphResponse, InvokeRequest, InvokeResponse, ToolCall
from graph import build_graph, extract_graph_spec

app = FastAPI(title="rosterd demo agent (e-commerce)")
_graph = build_graph(checkpointer=MemorySaver())  # in-memory: run with ONE worker


def _to_response(result: dict, thread_id: str) -> InvokeResponse:
    tool_calls = [ToolCall(**c) for c in result.get("tool_calls", [])]
    if result.get("__interrupt__"):
        info = result["__interrupt__"][0].value
        return InvokeResponse(
            output=f"PENDING_HUMAN_APPROVAL [thread_id={thread_id}]: {info.get('reason')}",
            tool_calls=tool_calls,
            next_node=None,
        )
    return InvokeResponse(
        output=" ".join(result.get("outputs", [])) or "done",
        tool_calls=tool_calls,
        next_node=result.get("next_node"),
    )


@app.post("/invoke", response_model=InvokeResponse)
def invoke(req: InvokeRequest) -> InvokeResponse:
    ctx = req.input.context or {}
    thread_id = str(ctx.get("thread_id") or uuid.uuid4().hex)
    state = {
        "entry_node": req.entry_node.value,
        "text": req.input.text,
        "context": ctx,
        "mode": ctx.get("mode"),  # optional per-request override: "scripted" | "llm"
        "tool_calls": [],
        "outputs": [],
    }
    result = _graph.invoke(state, {"configurable": {"thread_id": thread_id}})
    return _to_response(result, thread_id)


@app.get("/graph", response_model=GraphResponse)
def get_graph() -> GraphResponse:
    return GraphResponse(**extract_graph_spec(_graph).model_dump())


class ResumeRequest(BaseModel):
    thread_id: str
    approved: bool


@app.post("/resume", response_model=InvokeResponse)
def resume(req: ResumeRequest) -> InvokeResponse:
    cfg = {"configurable": {"thread_id": req.thread_id}}
    if not _graph.get_state(cfg).next:
        raise HTTPException(status_code=404, detail="no paused run for this thread_id")
    result = _graph.invoke(Command(resume={"approved": req.approved}), cfg)
    return _to_response(result, req.thread_id)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mode": current_mode(), "llm_key_present": llm_key_present()}
