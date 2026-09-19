# Kernel API — curl-able examples

Assumes the kernel is running on `:8100` with a manifest already loaded
(either a real `ROSTERD_KERNEL_MANIFEST_ID` pointed at ingestion, or, for
local poking, use the test suite's fixtures as a reference for shape).

## Dispatch a task

```bash
curl -s -X POST localhost:8100/dispatch \
  -H 'content-type: application/json' \
  -d '{
        "agent_id": "fulfillment",
        "task": {
          "id": "t-1", "title": "Reserve order #4482",
          "description": "reserve 3x SKU-991 for order 4482"
        },
        "assignees": ["ops-bot"]
      }'
# {"run_id": "run_...", "status": "accepted", "reason": null}
```

## Check the run

```bash
curl -s localhost:8100/runs/run_XXXXXXXXXXXXXXXX
# {"run_id": "...", "agent_id": "fulfillment", "status": "done",
#  "started_at": "...", "ended_at": "...", "violation": null,
#  "output": "...", "trace_id": "..."}
```

A killed run instead has `"status": "killed"` and a populated `violation`:

```json
{
  "status": "killed",
  "violation": {
    "rule": "tool_calls[*].args.amount lte 100",
    "expected": "lte 100",
    "actual": "5000"
  }
}
```

## Force-kill a run

```bash
curl -s -X POST localhost:8100/runs/run_XXXXXXXXXXXXXXXX/kill
# {"run_id": "...", "status": "killed", "reason": "manual kill requested"}
```

## Push a policy update (what the coordinator calls)

```bash
curl -s -X POST localhost:8100/policy \
  -H 'content-type: application/json' \
  -d '{"rule": "tool_calls[*].args.amount lte", "value": 50, "reason": "seen at 2 sites"}'
# {"applied": true}
```

## Simulate a flash sale (what the Federation dashboard's button calls)

```bash
curl -s -X POST localhost:8100/agents/fulfillment/simulate-load \
  -H 'content-type: application/json' \
  -d '{"count": 20, "rate_per_second": 5, "scale_down_after_idle_seconds_override": 20}'
# {"agent_id": "fulfillment", "dispatched": 20}
```

Then watch `GET /agents/fulfillment/instances` (or the `rosterd.agent.*`
OTel gauges / the Monitor screen's SpacetimeDB subscription) climb and
settle back down.

## Health / debug

```bash
curl -s localhost:8100/health     # {"status": "ok", "budget_remaining": 1.0}
curl -s localhost:8100/manifest   # what's currently confirmed for this site
curl -s localhost:8100/policy     # active policy overrides
curl -s localhost:8100/healthz    # liveness + effective settings
```
