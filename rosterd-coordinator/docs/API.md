# Coordinator API — curl-able examples

Assumes the coordinator is running on `:8300`.

## Post an event (what a kernel does after every run/scale event)

```bash
curl -s -X POST localhost:8300/events \
  -H 'content-type: application/json' \
  -d '{
        "site_id": "site-a", "run_id": "run-1", "agent_id": "refund",
        "status": "killed", "timestamp": "2026-09-19T20:00:00Z",
        "violation": {"rule": "tool_calls[*].args.amount lte 100", "expected": "lte 100", "actual": "5000"}
      }'
# {"received": true, "policy_update": null}      <- first sighting, no pattern yet
```

Post the *same* violation from a second `site_id` and the response carries
a tightened policy, which is also pushed to every other known kernel:

```bash
curl -s -X POST localhost:8300/events \
  -H 'content-type: application/json' \
  -d '{
        "site_id": "site-b", "run_id": "run-2", "agent_id": "refund",
        "status": "killed", "timestamp": "2026-09-19T20:00:05Z",
        "violation": {"rule": "tool_calls[*].args.amount lte 100", "expected": "lte 100", "actual": "5000"}
      }'
# {"received": true, "policy_update": {"rule": "tool_calls[*].args.amount lte", "value": 50.0}}
```

## Check site health

```bash
curl -s localhost:8300/sites
# [{"site_id": "site-a", "status": "violation", "last_event": "...", "score": 0.0},
#  {"site_id": "site-b", "status": "violation", "last_event": "...", "score": 0.0}]
```

## Manually broadcast a policy (what a human/frontend action calls)

```bash
curl -s -X POST localhost:8300/policy/push \
  -H 'content-type: application/json' \
  -d '{"rule": "tool_calls[*].args.amount lte", "value": 25, "reason": "tightened after review"}'
# {"pushed": {"site-a": true, "site-b": true}}
```

## Debug

```bash
curl -s localhost:8300/events                    # recent event log, newest first
curl -s "localhost:8300/events?site_id=site-a"    # filtered to one site
curl -s localhost:8300/healthz                    # liveness + known kernels
```
