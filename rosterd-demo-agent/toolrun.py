"""Run a tool and record the call exactly as the agent attempted it.

The recorded `args` are what the agent PROPOSED, even if the tool's own
schema rejects them. The kernel evaluates its inferred rules against these
args, so they must never be stripped, clamped or rewritten.
"""

from pydantic import ValidationError


def attempt_tool(tool, args: dict) -> dict:
    recorded = dict(args)
    try:
        result = str(tool.invoke(args))
    except ValidationError as exc:
        err = exc.errors()[0]
        field = ".".join(str(p) for p in err["loc"])
        result = f"REJECTED by tool schema: {field} {err['msg']}"
    return {"tool": tool.name, "args": recorded, "result": result}
