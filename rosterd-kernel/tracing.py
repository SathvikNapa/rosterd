"""OpenTelemetry setup, wrapped behind one thin interface so the rest of the
kernel never touches the SDK directly.

Two things this buys:

1. Business code (dispatch.py, scaler.py, ...) calls `telemetry.span(...)`,
   `telemetry.inject_headers(...)`, `telemetry.current_trace_id()` and
   never imports `opentelemetry` itself -- so it's trivially unit-testable
   without a collector running.
2. If the SDK isn't installed, or the collector is unreachable, or setup
   throws for any reason, every call here degrades to a no-op instead of
   crashing the kernel. Tracing matters for the demo, but a misconfigured
   Jaeger endpoint should never be why /dispatch is down.

Span/metric naming follows the `rosterd.*` namespace this module defines --
the convention the rest of the team's own instrumentation follows too (see
README "Tracing conventions").
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterator

logger = logging.getLogger("rosterd.kernel.tracing")

try:
    from opentelemetry import metrics as metrics_api
    from opentelemetry import propagate, trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.metrics import Observation
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import Status, StatusCode

    _OTEL_IMPORTED = True
except Exception:  # pragma: no cover - exercised when the SDK isn't installed
    _OTEL_IMPORTED = False


class _NullSpan:
    def set_attribute(self, *_a: Any, **_k: Any) -> None:
        pass

    def set_status(self, *_a: Any, **_k: Any) -> None:
        pass

    def record_exception(self, *_a: Any, **_k: Any) -> None:
        pass


@contextlib.contextmanager
def _null_span(*_a: Any, **_k: Any) -> Iterator[_NullSpan]:
    yield _NullSpan()


class _AttrSpan:
    """Sets attributes at __enter__ time, then defers to the wrapped OTel
    context manager for everything else."""

    def __init__(self, context_manager: Any, attributes: dict[str, Any]) -> None:
        self._cm = context_manager
        self._attributes = attributes

    def __enter__(self) -> Any:
        span = self._cm.__enter__()
        for key, value in self._attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        return span

    def __exit__(self, *exc: Any) -> Any:
        return self._cm.__exit__(*exc)


class Telemetry:
    """One instance per process, built once in app.py's Container."""

    def __init__(self, settings) -> None:
        self._settings = settings
        self._enabled = bool(settings.otel_enabled and _OTEL_IMPORTED)
        self._tracer = None
        self._meter = None
        # (metric_name, series_key) -> (value, attributes), read by the
        # ObservableGauge callbacks registered in _setup().
        self._gauge_state: dict[tuple[str, str], tuple[float, dict]] = {}
        if self._enabled:
            try:
                self._setup()
            except Exception:
                logger.exception("OpenTelemetry setup failed; continuing without tracing/metrics")
                self._enabled = False

    def _setup(self) -> None:
        resource = Resource.create(
            {"service.name": self._settings.otel_service_name, "rosterd.site_id": self._settings.site_id}
        )

        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{self._settings.otel_endpoint}/v1/traces"))
        )
        trace.set_tracer_provider(provider)
        self._tracer = trace.get_tracer("rosterd.kernel")

        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{self._settings.otel_endpoint}/v1/metrics"),
            export_interval_millis=5000,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics_api.set_meter_provider(meter_provider)
        self._meter = metrics_api.get_meter("rosterd.kernel")

        def _make_callback(metric_name: str):
            def _callback(_options: Any) -> list[Observation]:
                return [
                    Observation(value, attrs)
                    for (name, _series), (value, attrs) in self._gauge_state.items()
                    if name == metric_name
                ]

            return _callback

        for metric_name in (
            "rosterd.agent.replicas",
            "rosterd.agent.load",
            "rosterd.agent.desired_replicas",
            "rosterd.budget.remaining",
        ):
            self._meter.create_observable_gauge(metric_name, callbacks=[_make_callback(metric_name)])

    def instrument_app(self, app: Any) -> None:
        """FastAPI auto-instrumentation: HTTP spans for every route, free."""
        if not self._enabled:
            return
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app)
        except Exception:
            logger.exception("FastAPI auto-instrumentation failed")

    def span(self, name: str, **attributes: Any):
        if not self._enabled or self._tracer is None:
            return _null_span()
        return _AttrSpan(self._tracer.start_as_current_span(name), attributes)

    def inject_headers(self, headers: dict[str, str] | None = None) -> dict[str, str]:
        """Propagate `traceparent` on every outbound call, per the brief:
        to the demo agent's /invoke and to the coordinator's /events."""
        headers = dict(headers or {})
        if self._enabled:
            propagate.inject(headers)
        return headers

    def current_trace_id(self) -> str | None:
        if not self._enabled:
            return None
        span_context = trace.get_current_span().get_span_context()
        if span_context is None or not span_context.is_valid:
            return None
        return format(span_context.trace_id, "032x")

    def record_scale_tick(
        self, agent_id: str, load: int, current_replicas: int, desired_replicas: int
    ) -> None:
        attrs = {"rosterd.site_id": self._settings.site_id, "rosterd.agent_id": agent_id}
        self._gauge_state[("rosterd.agent.replicas", agent_id)] = (current_replicas, attrs)
        self._gauge_state[("rosterd.agent.load", agent_id)] = (load, attrs)
        self._gauge_state[("rosterd.agent.desired_replicas", agent_id)] = (desired_replicas, attrs)

    def record_budget_remaining(self, value: float) -> None:
        attrs = {"rosterd.site_id": self._settings.site_id}
        self._gauge_state[("rosterd.budget.remaining", "_site")] = (value, attrs)


def mark_violation(span: Any, violation) -> None:
    """Set span status to error and attach violation.* attributes -- what
    lets the demo pull up the exact trace in Jaeger for a kill, instead of
    only showing a UI card."""
    if violation is None or isinstance(span, _NullSpan):
        return
    if _OTEL_IMPORTED:
        try:
            span.set_status(Status(StatusCode.ERROR))
        except Exception:  # noqa: BLE001 - never let telemetry break a kill
            pass
    span.set_attribute("violation.rule", violation.rule)
    span.set_attribute("violation.expected", violation.expected)
    span.set_attribute("violation.actual", violation.actual)
