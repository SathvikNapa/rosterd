"""In-memory policy overrides applied on top of the confirmed manifest.

`POST /policy` (accepting a PolicyUpdateRequest pushed from the
coordinator, per the brief) writes here. Two things read it:

* `constraints.evaluate_all` -- an override keyed by a rule's own identity
  (`"{field} {op}"`) replaces that rule's `value` for every subsequent
  dispatch, so a coordinator-detected shared failure pattern (e.g. "two
  sites just saw the same misdirection attempt, tighten the refund cap")
  takes effect immediately, without waiting for Param to re-confirm a new
  manifest.
* `GET /policy` (a small debug addition, not in the contract table, same
  spirit as ingestion's own additive debug endpoints) so a human can see
  what's currently overridden.

Overrides are process-local and lost on restart -- there's no persistence
requirement in the brief, and re-applying a policy is exactly what the
coordinator does after detecting the pattern again.
"""
from __future__ import annotations

import logging
import threading

from kernel import PolicyUpdateRequest

logger = logging.getLogger("rosterd.kernel.policy")


class PolicyStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._overrides: dict[str, float | str | bool] = {}

    def apply(self, update: PolicyUpdateRequest) -> None:
        with self._lock:
            self._overrides[update.rule] = update.value
        logger.info("policy override applied: %s = %r (%s)", update.rule, update.value, update.reason)

    def get(self, rule: str, default=None):
        with self._lock:
            return self._overrides.get(rule, default)

    def all(self) -> dict[str, float | str | bool]:
        with self._lock:
            return dict(self._overrides)
