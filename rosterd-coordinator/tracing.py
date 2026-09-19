"""OpenTelemetry setup, wrapped behind one thin interface -- same approach
as rosterd-kernel/tracing.py (see its docstring for the full rationale: no
SDK/collector means every call here degrades to a no-op instead of
crashing the service).

One addition over the kernel's version: `span_from_headers`. The kernel is
always the root of a dispatch trace (it only ever injects `traceparent`
onto outbound calls); the coordinator is always downstream of it, so
`POST /events` needs to *extract* the incoming trace context and continue
it, per the brief: "propagate incoming trace context from the kernel's
/events call so the coordinator's handling shows up as a child span of the
same dispatch trace."
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterator

logger = logging.getLogger("rosterd.coordinator.tracing")

try:
    from opentelemetry import propagate, trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

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
    def __init__(self, settings) -> None:
        self._settings = settings
        self._enabled = bool(settings.otel_enabled and _OTEL_IMPORTED)
        self._tracer = None
        if self._enabled:
            try:
                self._setup()
            except Exception:
                logger.exception("OpenTelemetry setup failed; continuing without tracing")
                self._enabled = False

    def _setup(self) -> None:
        resource = Resource.create({"service.name": self._settings.otel_service_name})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{self._settings.otel_endpoint}/v1/traces"))
        )
        trace.set_tracer_provider(provider)
        self._tracer = trace.get_tracer("rosterd.coordinator")
        # No custom metrics for the coordinator today -- only the kernel's
        # rosterd.agent.* / rosterd.budget.remaining gauges are specced.

    def instrument_app(self, app: Any) -> None:
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

    def span_from_headers(self, name: str, headers: dict[str, str] | None, **attributes: Any):
        """Continue whatever trace `headers` carries a `traceparent` for,
        instead of starting a new root span. Falls back to an ordinary
        (new-root) span if there's no valid incoming context, which is the
        right behavior for a debug curl with no headers at all."""
        if not self._enabled or self._tracer is None:
            return _null_span()
        context = propagate.extract(headers or {})
        return _AttrSpan(self._tracer.start_as_current_span(name, context=context), attributes)

    def inject_headers(self, headers: dict[str, str] | None = None) -> dict[str, str]:
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
