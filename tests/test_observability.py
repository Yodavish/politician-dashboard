from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from politician_dashboard.observability import _configure_sdk, instrument_app


def test_resource_attributes_use_otel_environment(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_SERVICE_NAME", "dashboard-test")
    monkeypatch.setenv("OTEL_SERVICE_NAMESPACE", "test-namespace")
    monkeypatch.setenv("OTEL_DEPLOYMENT_ENVIRONMENT", "test")

    tracer_provider, meter_provider = _configure_sdk()

    resources = (tracer_provider.resource, meter_provider._sdk_config.resource)
    for resource in resources:
        assert resource.attributes["service.name"] == "dashboard-test"
        assert resource.attributes["service.namespace"] == "test-namespace"
        assert resource.attributes["deployment.environment"] == "test"
    tracer_provider.shutdown()
    meter_provider.shutdown()


def test_resource_attributes_have_development_defaults(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    for name in (
        "OTEL_SERVICE_NAME",
        "OTEL_SERVICE_NAMESPACE",
        "OTEL_DEPLOYMENT_ENVIRONMENT",
    ):
        monkeypatch.delenv(name, raising=False)

    tracer_provider, meter_provider = _configure_sdk()

    resources = (tracer_provider.resource, meter_provider._sdk_config.resource)
    for resource in resources:
        assert resource.attributes["service.name"] == "politician-dashboard-api"
        assert resource.attributes["service.namespace"] == "politician-dashboard"
        assert resource.attributes["deployment.environment"] == "development"
    tracer_provider.shutdown()
    meter_provider.shutdown()


def test_fastapi_request_is_recorded_in_memory(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    app = FastAPI()
    app.get("/observed")(lambda: {"ok": True})
    instrument_app(app, tracer_provider=tracer_provider, meter_provider=meter_provider)

    with TestClient(app) as client:
        response = client.get("/observed")

    assert response.status_code == 200
    server_span = next(span for span in spans.get_finished_spans() if span.kind.name == "SERVER")
    status = server_span.attributes.get(
        "http.response.status_code", server_span.attributes.get("http.status_code")
    )
    assert status == 200

    metrics = metric_reader.get_metrics_data().resource_metrics[0].scope_metrics[0].metrics
    duration = next(
        metric
        for metric in metrics
        if metric.name in {"http.server.request.duration", "http.server.duration"}
    )
    assert sum(point.count for point in duration.data.data_points) == 1


def test_api_app_starts_without_telemetry_endpoint(monkeypatch):
    from politician_dashboard.api import db
    from politician_dashboard.api.main import create_app

    class Pool:
        def wait(self):
            pass

        def close(self):
            pass

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(db, "create_pool", lambda _url: Pool())
    app = create_app(database_url="postgresql://unused")

    with TestClient(app):
        assert app.state.pool is not None
