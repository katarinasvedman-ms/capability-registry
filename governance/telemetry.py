from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult


class InsightsExporter(SpanExporter):
    """Export our OTel server spans using the documented Application Insights envelope."""

    def __init__(self, connection, service):
        values = dict(item.split("=", 1) for item in connection.split(";") if "=" in item)
        self.key = values["InstrumentationKey"]
        self.endpoint = values["IngestionEndpoint"].rstrip("/") + "/v2/track"
        self.service = service
        self.client = httpx.Client(timeout=10)

    def export(self, spans):
        rows = []
        for span in spans:
            if "http.response.status_code" not in span.attributes:
                continue
            elapsed = timedelta(microseconds=(span.end_time - span.start_time) / 1000)
            rows.append(
                {
                    "name": "Microsoft.ApplicationInsights.Request",
                    "time": datetime.fromtimestamp(span.start_time / 1e9, UTC).isoformat(),
                    "iKey": self.key,
                    "tags": {
                        "ai.cloud.role": self.service,
                        "ai.operation.id": f"{span.context.trace_id:032x}",
                    },
                    "data": {
                        "baseType": "RequestData",
                        "baseData": {
                            "ver": 2,
                            "id": f"{span.context.span_id:016x}",
                            "name": span.name,
                            "duration": str(elapsed),
                            "responseCode": str(
                                span.attributes.get("http.response.status_code", 500)
                            ),
                            "success": span.attributes.get("http.response.status_code", 500) < 400,
                            "properties": dict(span.attributes),
                        },
                    },
                }
            )
        if not rows:
            return SpanExportResult.SUCCESS
        try:
            response = self.client.post(self.endpoint, json=rows)
            return (
                SpanExportResult.SUCCESS
                if response.status_code == 200
                else SpanExportResult.FAILURE
            )
        except httpx.HTTPError:
            return SpanExportResult.FAILURE

    def shutdown(self):
        self.client.close()


def setup(connection, service):
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    provider.add_span_processor(BatchSpanProcessor(InsightsExporter(connection, service)))
    trace.set_tracer_provider(provider)
    return provider, provider.get_tracer("capability-registry-governance")
