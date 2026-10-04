"""OpenTelemetry setup for the HTTP API and its PostgreSQL connections."""

from __future__ import annotations

import os

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry import _logs
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_providers: tuple[TracerProvider, MeterProvider] | None = None
_logger_provider: LoggerProvider | None = None
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


def _otlp_log_exporter():
    if os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc").lower() == "http/protobuf":
        from opentelemetry.exporter.otlp.proto.http._log_exporter import (
            OTLPLogExporter,
        )
    else:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
            OTLPLogExporter,
        )
    return OTLPLogExporter


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


def configure_logging(
    *, logger_provider: LoggerProvider | None = None
) -> LoggerProvider:
    """Export standard-library logging records through the configured OTLP.

    The root handler is installed once so both API and CLI entry points can
    safely call this function. With no endpoint, records remain local.
    """
    global _logger_provider
    explicit_provider = logger_provider is not None
    if logger_provider is None:
        if _logger_provider is None:
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
            _logger_provider = LoggerProvider(resource=resource)
            if (
                os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
                and os.getenv("OTEL_LOGS_EXPORTER", "otlp").lower() != "none"
            ):
                OTLPLogExporter = _otlp_log_exporter()
                _logger_provider.add_log_record_processor(
                    BatchLogRecordProcessor(OTLPLogExporter())
                )
            _logs.set_logger_provider(_logger_provider)
        logger_provider = _logger_provider

    import logging

    if explicit_provider or (
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        and os.getenv("OTEL_LOGS_EXPORTER", "otlp").lower() != "none"
    ):
        root = logging.getLogger()
        if not any(isinstance(handler, LoggingHandler) for handler in root.handlers):
            root.addHandler(LoggingHandler(logger_provider=logger_provider))
    return logger_provider


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
