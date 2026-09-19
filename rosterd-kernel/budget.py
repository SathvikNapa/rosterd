"""Budget enforcer: tool-call count and elapsed time per run_id.

One RunBudget per in-flight or completed run. `GET /health`'s
`budget_remaining` and the `rosterd.budget.remaining` OTel gauge both read
`BudgetTracker.remaining_budget()`, the tightest remaining fraction across
every run currently in flight on this kernel (1.0, fully healthy, if
nothing is running).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from shared import Violation


@dataclass
class RunBudget:
    run_id: str
    started_at: datetime
    max_tool_calls: int
    max_seconds: float
    tool_call_count: int = 0
    ended_at: datetime | None = None

    def record_tool_calls(self, count: int) -> None:
        self.tool_call_count += count

    def finish(self, now: datetime | None = None) -> None:
        self.ended_at = now or datetime.now(timezone.utc)

    def elapsed_seconds(self, now: datetime | None = None) -> float:
        end = self.ended_at or now or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds()

    def remaining_fraction(self, now: datetime | None = None) -> float:
        call_fraction = 1.0
        if self.max_tool_calls > 0:
            call_fraction = 1.0 - min(self.tool_call_count / self.max_tool_calls, 1.0)
        time_fraction = 1.0
        if self.max_seconds > 0:
            time_fraction = 1.0 - min(self.elapsed_seconds(now) / self.max_seconds, 1.0)
        return max(0.0, min(call_fraction, time_fraction))

    def violation(self, now: datetime | None = None) -> Violation | None:
        if self.max_tool_calls > 0 and self.tool_call_count > self.max_tool_calls:
            return Violation(
                rule="budget.max_tool_calls",
                expected=f"<= {self.max_tool_calls}",
                actual=str(self.tool_call_count),
            )
        elapsed = self.elapsed_seconds(now)
        if self.max_seconds > 0 and elapsed > self.max_seconds:
            return Violation(
                rule="budget.max_run_seconds",
                expected=f"<= {self.max_seconds}s",
                actual=f"{elapsed:.1f}s",
            )
        return None


class BudgetTracker:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._runs: dict[str, RunBudget] = {}
        self._lock = threading.Lock()

    def start(self, run_id: str) -> None:
        with self._lock:
            self._runs[run_id] = RunBudget(
                run_id=run_id,
                started_at=datetime.now(timezone.utc),
                max_tool_calls=self._settings.max_tool_calls_per_run,
                max_seconds=self._settings.max_run_seconds,
            )

    def record_tool_calls(self, run_id: str, count: int) -> None:
        with self._lock:
            budget = self._runs.get(run_id)
            if budget is not None:
                budget.record_tool_calls(count)

    def finish(self, run_id: str) -> None:
        with self._lock:
            budget = self._runs.get(run_id)
            if budget is not None:
                budget.finish()

    def check(self, run_id: str) -> Violation | None:
        with self._lock:
            budget = self._runs.get(run_id)
            if budget is None:
                return None
            return budget.violation()

    def remaining_budget(self) -> float:
        with self._lock:
            active = [b for b in self._runs.values() if b.ended_at is None]
            if not active:
                return 1.0
            return min(b.remaining_fraction() for b in active)
