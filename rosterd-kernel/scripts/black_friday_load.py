#!/usr/bin/env python3
"""Fire real, sustained, concurrent load at several agent pools at once --
order_intake, catalog, fulfillment, and payment, not just one -- and print
`agent_metrics` as it happens so the autoscaling response is watchable live.

Built after a real "why doesn't Monitor move" investigation turned up a
genuine timing gotcha, not a scaling bug (see rosterd-kernel/README.md's
"A load burst can finish faster than the scaler samples it"): the scaler
only samples `working + queued` once per ROSTERD_KERNEL_SCALER_INTERVAL_SEC
(default 5s), so a single small burst against a fast (AGENT_MODE=scripted)
demo-agent can fully drain between two ticks and never show up at all.

The fix here is NOT a shortcut around that -- it's sustaining real,
genuine load for longer than one tick interval, by repeatedly firing
overlapping /simulate-load bursts rather than one, so there is always
something in flight when the scaler actually looks.

**Defaults trimmed after a real report: this pegged a machine.** The
first version defaulted to `--burst 150 --interval 0.5 --seconds 8` with
NO throttle (`/simulate-load`'s own `rate_per_second=0` means "spawn all
`count` dispatch threads back to back, no sleep between them" -- see
simulate.py). At 4 agents x up to ~16 overlapping rounds x 150 threads
each, that's on the order of several thousand real OS threads spawned
*inside the kernel process* over a few seconds, each holding a real HTTP
connection open to demo-agent -- genuinely heavy for any machine, not
just a slow one. Still verified live at the original settings (scaled
all four to their ceiling simultaneously with real queue depth), but the
defaults below are deliberately much gentler: a real --rate throttle is
now threaded through to /simulate-load's own rate_per_second (spreading
each burst's thread creation out instead of firing it all at once), and
burst/round counts are both cut roughly 10x. Still enough overlapping
load to visibly scale a few agents past 1 replica; turn the numbers back
up with the flags below if your machine can take it and you want the
full double-digit ceiling climb.

Usage:
    .venv/bin/python scripts/black_friday_load.py
    .venv/bin/python scripts/black_friday_load.py --kernel http://localhost:8100 \\
        --seconds 6 --burst 15 --interval 1.0 --rate 8 \\
        --agents order_intake catalog fulfillment payment

agent_metrics lives in SpacetimeDB, not a kernel endpoint, so by default
this script just drives the load and leaves watching it to the caller
(`spacetime sql rosterd --server http://localhost:3000 "SELECT * FROM
agent_metrics"`, or the Monitor screen). Pass --watch to have it also tail
agent_metrics itself for the agents being loaded, if the `spacetime` CLI
is on PATH.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import json


def fire_burst(kernel: str, agent_id: str, count: int, rate_per_second: float) -> dict:
    url = f"{kernel.rstrip('/')}/agents/{agent_id}/simulate-load"
    body = json.dumps({"count": count, "rate_per_second": rate_per_second}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def sustain(kernel: str, agents: list[str], seconds: float, burst: int, interval: float, rate: float) -> None:
    """Keep firing overlapping bursts at every agent for `seconds`, so
    there is always something in flight regardless of how fast any one
    burst drains -- see the module docstring for why this matters.

    `rate` is a REAL throttle, not cosmetic: it's passed straight through
    as /simulate-load's own `rate_per_second`, which controls how fast the
    KERNEL spawns dispatch threads for one burst (simulate.py's
    `_fire_all`). `rate=0` there means "no sleep between thread spawns" --
    the thing that pegged a machine the first time this script existed.
    A real rate spreads each burst's own thread creation out instead of
    firing it all in one instant."""
    deadline = time.monotonic() + seconds
    round_num = 0
    while time.monotonic() < deadline:
        round_num += 1
        threads = []
        for agent_id in agents:
            t = threading.Thread(
                target=lambda a=agent_id: fire_burst(kernel, a, burst, rate_per_second=rate),
                daemon=True,
            )
            t.start()
            threads.append(t)
        print(f"round {round_num}: fired {burst} requests at each of {agents}, throttled to {rate}/s", file=sys.stderr)
        time.sleep(interval)


def _latest_row_per_agent(sql_output: str, agents: list[str]) -> dict[str, str]:
    """`agent_metrics` only ever grows (no retention policy -- same
    single-table-scan cost the kernel's own README flags elsewhere), so
    this deliberately does NOT try to tail it incrementally by id: at scan
    speed that's a real cost per poll, but simpler and more honestly bounded
    than a client-side "seen" filter would be, which re-scans the same
    growing output anyway and is easy to get subtly wrong. Keeps only the
    highest-id (last) row per agent from one full scan."""
    best: dict[str, tuple[int, str]] = {}
    for line in sql_output.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        row_id, agent_id = int(parts[0]), parts[2].strip('"')
        if agent_id in agents and (agent_id not in best or row_id > best[agent_id][0]):
            best[agent_id] = (row_id, line)
    return {agent: line for agent, (_, line) in best.items()}


def watch_spacetime(agents: list[str], module: str, server: str, seconds: float) -> None:
    if shutil.which("spacetime") is None:
        print("`spacetime` CLI not on PATH -- skipping the live watch; query agent_metrics yourself.", file=sys.stderr)
        return
    deadline = time.monotonic() + seconds + 3  # a little past the load to catch the scale-up landing
    last_printed: dict[str, str] = {}
    while time.monotonic() < deadline:
        try:
            out = subprocess.run(
                ["spacetime", "sql", module, "--server", server, "SELECT * FROM agent_metrics"],
                capture_output=True, text=True, timeout=10,
            ).stdout
        except Exception as exc:  # noqa: BLE001 - a watch failure shouldn't kill the load test
            print(f"(spacetime sql failed: {exc})", file=sys.stderr)
            time.sleep(1)
            continue
        for agent_id, line in _latest_row_per_agent(out, agents).items():
            if last_printed.get(agent_id) != line:
                last_printed[agent_id] = line
                print(line)
        time.sleep(1.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kernel", default="http://localhost:8100")
    parser.add_argument(
        "--agents", nargs="+", default=["order_intake", "catalog", "fulfillment", "payment"],
        help="Black Friday's high-traffic agents. refund_exception is deliberately excluded by default.",
    )
    parser.add_argument("--seconds", type=float, default=6.0, help="How long to sustain overlapping bursts.")
    parser.add_argument("--burst", type=int, default=15, help="Requests per agent per round.")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between rounds.")
    parser.add_argument(
        "--rate", type=float, default=8.0,
        help="Requests/sec within one burst, passed to /simulate-load's own throttle -- "
        "0 means unthrottled (all of --burst spawned as fast as the kernel can loop, which is "
        "what pegged a machine at the old defaults; see the module docstring before raising this).",
    )
    parser.add_argument("--watch", action="store_true", help="Tail agent_metrics via the spacetime CLI while loading.")
    parser.add_argument("--spacetime-module", default="rosterd")
    parser.add_argument("--spacetime-server", default="http://localhost:3000")
    args = parser.parse_args()

    print(
        f"Black Friday load: {args.agents} for {args.seconds}s "
        f"({args.burst}/round every {args.interval}s, throttled to {args.rate}/s per round)"
    )

    watcher = None
    if args.watch:
        watcher = threading.Thread(
            target=watch_spacetime,
            args=(args.agents, args.spacetime_module, args.spacetime_server, args.seconds),
            daemon=True,
        )
        watcher.start()

    sustain(args.kernel, args.agents, args.seconds, args.burst, args.interval, args.rate)

    if watcher is not None:
        watcher.join(timeout=args.seconds + 5)

    print(
        "Done firing. Replicas scale back down after "
        "scale_down_after_idle_seconds (default 30s) of no new load -- "
        "check GET /manifest or watch agent_metrics to see it settle."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
