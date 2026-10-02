"""Shared-failure-pattern detection: "a violation on the same rule shows up
at 2+ distinct sites" -> tighten that rule and push it everywhere, per the
brief's `POST /policy/push` design.

A kernel's `Violation.rule` (see rosterd-kernel/constraints.py) is rendered
as `"{field} {op} {bound}"`, e.g. `"tool_calls[*].args.amount lte 100"` --
the exact string a demo agent's misdirection attempt tripped. `parse_rule`
splits that back into the kernel's own policy-override key (`"{field}
{op}"`, what `PolicyStore.get()` looks up) and the bound that was in force
when the violation happened, so a detected pattern's response is phrased in
exactly the vocabulary the kernel already knows how to apply.
"""
from __future__ import annotations

import logging
import re
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger("rosterd.coordinator.patterns")

_RULE_RE = re.compile(r"^(?P<field>.+) (?P<op>lte|gte|eq|in|not_in) (?P<value>.+)$")


def parse_rule(rule: str) -> tuple[str, str, str] | None:
    """`"tool_calls[*].args.amount lte 100"` -> `("tool_calls[*].args.amount", "lte", "100")`.
    None if `rule` isn't in that shape (e.g. `"dispatch.timeout"`, `"budget.max_tool_calls"`,
    or `"manual_kill"` -- none of those name a manifest constraint, so there's
    nothing here for a policy override to tighten)."""
    match = _RULE_RE.match(rule)
    if not match:
        return None
    return match.group("field"), match.group("op"), match.group("value")


def _tighten(op: str, bound: str, factor: float) -> float | None:
    """A pattern's response is only meaningful for a numeric comparison.
    lte shrinks the ceiling (new = old * factor); gte raises the floor
    (new = old / factor). eq/in/not_in have no numeric "tighter" -- return
    None and the caller skips auto-tightening for them (the pattern is
    still recorded/logged, just not turned into a policy push)."""
    try:
        numeric = float(bound)
    except ValueError:
        return None
    if op == "lte":
        return round(numeric * factor, 4)
    if op == "gte":
        return round(numeric / factor, 4) if factor else None
    return None


@dataclass
class _Sighting:
    site_id: str
    bound: str
    timestamp: datetime


@dataclass
class PatternMatch:
    rule_key: str  # "{field} {op}" -- the kernel PolicyStore override key
    value: float
    distinct_sites: int
    reason: str


class PatternDetector:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._sightings: dict[str, deque[_Sighting]] = {}
        self._last_pushed_at: dict[str, datetime] = {}

    def _prune(self, key: str, now: datetime) -> None:
        window = self._sightings[key]
        cutoff = now.timestamp() - self._settings.pattern_window_sec
        while window and window[0].timestamp.timestamp() < cutoff:
            window.popleft()

    def record_violation(self, rule: str, site_id: str, timestamp: datetime) -> PatternMatch | None:
        parsed = parse_rule(rule)
        if parsed is None:
            return None
        field_path, op, bound = parsed
        key = f"{field_path} {op}"
        now = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)

        with self._lock:
            window = self._sightings.setdefault(key, deque())
            window.append(_Sighting(site_id=site_id, bound=bound, timestamp=now))
            self._prune(key, now)

            distinct_sites = {s.site_id for s in window}
            if len(distinct_sites) < self._settings.pattern_site_threshold:
                return None

            last_pushed = self._last_pushed_at.get(key)
            if last_pushed is not None:
                elapsed = (now - last_pushed).total_seconds()
                if elapsed < self._settings.pattern_push_cooldown_sec:
                    return None

            new_value = _tighten(op, bound, self._settings.pattern_tighten_factor)
            if new_value is None:
                logger.info(
                    "pattern detected on %s at %d sites, but op=%s has no numeric tightening -- not pushing",
                    key, len(distinct_sites), op,
                )
                return None

            self._last_pushed_at[key] = now
            reason = (
                f"shared failure pattern: {key} violated at {len(distinct_sites)} sites "
                f"within {self._settings.pattern_window_sec:.0f}s (was {bound}, now {new_value})"
            )
            return PatternMatch(rule_key=key, value=new_value, distinct_sites=len(distinct_sites), reason=reason)
