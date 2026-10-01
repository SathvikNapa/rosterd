"""Pushes a policy update to every kernel the coordinator knows about, via
each one's own `POST /policy` (see rosterd-kernel/kernel.py's
PolicyUpdateRequest -- `rule`, `value`, `reason`).

Two callers: `POST /policy/push` (a human or the frontend explicitly asking
for a broadcast) and `patterns.PatternDetector` (an automatic push once a
shared failure pattern crosses the site threshold). Both go through this
one function so there's exactly one place that knows how to reach a kernel.

Which kernels exist at all is config (`Settings.site_kernels`), not
discovery -- there's no service registry in this repo, and a site posting
to `POST /events` doesn't carry its own kernel URL in the payload (that
would mean widening EventRequest, which is Joy's contract, not something to
do unilaterally). See the README for how to wire up a second site.

Every push is best-effort: one kernel being unreachable must not stop the
others from getting the update, and must not fail the request that
triggered the push in the first place.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("rosterd.coordinator.broadcaster")


class PolicyBroadcaster:
    def __init__(self, settings, telemetry) -> None:
        self._settings = settings
        self._telemetry = telemetry

    def push(
        self, rule: str, value: float | str | bool, reason: str, *, exclude_site: str | None = None
    ) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for site_id, kernel_url in self._settings.site_kernels.items():
            if site_id == exclude_site:
                continue
            results[site_id] = self._push_one(kernel_url, rule, value, reason)
        return results

    def _push_one(self, kernel_url: str, rule: str, value: float | str | bool, reason: str) -> bool:
        with self._telemetry.span("push_policy", **{"rosterd.kernel_url": kernel_url, "rosterd.rule": rule}):
            headers = self._telemetry.inject_headers({"content-type": "application/json"})
            try:
                response = httpx.post(
                    f"{kernel_url.rstrip('/')}/policy",
                    json={"rule": rule, "value": value, "reason": reason},
                    headers=headers,
                    timeout=self._settings.push_timeout_sec,
                )
                response.raise_for_status()
                return True
            except Exception:  # noqa: BLE001 - best-effort, see module docstring
                logger.warning("POST %s/policy failed (continuing)", kernel_url, exc_info=True)
                return False
