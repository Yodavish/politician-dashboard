"""OpenTelemetry setup for the HTTP API and its PostgreSQL connections."""

from __future__ import annotations

import os

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_providers: tuple[TracerProvider, MeterProvider] | None = None
_psycopg_instrumented = False


def _otlp_exporters():
    protocol = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc").lower()
    if protocol == "http/protobuf":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
    else:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
    return OTLPSpanExporter, OTLPMetricExporter


def _configure_sdk() -> tuple[TracerProvider, MeterProvider]:
    """Build SDK providers; exporters stay disabled until an OTLP endpoint exists."""
    resource = Resource.create(
        {
            "service.name": os.getenv(
                "OTEL_SERVICE_NAME", "politician-dashboard-api"
            ),
            "service.namespace": os.getenv(
                "OTEL_SERVICE_NAMESPACE", "politician-dashboard"
            ),
            "deployment.environment": os.getenv(
                "OTEL_DEPLOYMENT_ENVIRONMENT", "development"
            ),
        }
    )
    tracer_provider = TracerProvider(resource=resource)
    metric_readers = []

    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        OTLPSpanExporter, OTLPMetricExporter = _otlp_exporters()
        if os.getenv("OTEL_TRACES_EXPORTER", "otlp").lower() != "none":
            tracer_provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter())
            )
        if os.getenv("OTEL_METRICS_EXPORTER", "otlp").lower() != "none":
            metric_readers.append(
                PeriodicExportingMetricReader(OTLPMetricExporter())
            )

    return tracer_provider, MeterProvider(
        resource=resource, metric_readers=metric_readers
    )


def instrument_app(
    app: FastAPI,
    *,
    tracer_provider: TracerProvider | None = None,
    meter_provider: MeterProvider | None = None,
) -> None:
    """Instrument an app, with injectable providers for local verification/tests."""
    global _providers, _psycopg_instrumented
    if tracer_provider is None or meter_provider is None:
        if _providers is None:
            _providers = _configure_sdk()
            trace.set_tracer_provider(_providers[0])
            metrics.set_meter_provider(_providers[1])
        configured_tracer, configured_meter = _providers
        tracer_provider = tracer_provider or configured_tracer
        meter_provider = meter_provider or configured_meter

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )
    if not _psycopg_instrumented:
        PsycopgInstrumentor().instrument(tracer_provider=tracer_provider)
        _psycopg_instrumented = True
