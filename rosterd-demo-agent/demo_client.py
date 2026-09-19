"""Poke the running service:  python demo_client.py misdirection [--llm]

Also does a tiny stand-in for the kernel's check: compares each tool call's
args to the limits published by the tool schemas (tools.py)."""

import copy
import json
import sys
import urllib.request

from scenarios import ALL
from tools import issue_refund, reserve_inventory

BASE = "http://localhost:8000"
TOOLS = {t.name: t for t in (reserve_inventory, issue_refund)}


def post(path, body):
    req = urllib.request.Request(BASE + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def check(tool_calls):
    for c in tool_calls:
        props = TOOLS[c["tool"]].args_schema.model_json_schema()["properties"]
        for arg, val in c["args"].items():
            cap = props.get(arg, {}).get("maximum")
            if cap is not None and val > cap:
                print(f"  VIOLATION: {c['tool']}.{arg} = {val} > allowed {cap}   (rule inferred from the tool schema)")
                return
    print("  all tool-call args within schema limits")


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "misdirection"
    body = copy.deepcopy(ALL[name])
    if "--llm" in sys.argv:
        body["input"].setdefault("context", {})["mode"] = "llm"
    out = post("/invoke", body)
    print(json.dumps(out, indent=2))
    check(out["tool_calls"])
