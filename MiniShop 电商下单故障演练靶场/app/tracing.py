from typing import Callable, Dict, Tuple

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanProcessor
from opentelemetry.trace import NoOpTracerProvider, Tracer

from app.config import settings


SERVICE_NAMES = (
    "checkout-service",
    "inventory-service",
    "payment-service",
    "notification-service",
)

SpanExporterFactory = Callable[[str], SpanExporter]
SpanProcessorFactory = Callable[[SpanExporter], SpanProcessor]


class ServiceTracing:
    def __init__(
        self,
        tracers: Dict[str, Tracer],
        providers: Tuple[SDKTracerProvider, ...] = (),
    ) -> None:
        self._tracers = tracers
        self._providers = providers
        self._shutdown = False

    @property
    def enabled(self) -> bool:
        return bool(self._providers)

    def tracer(self, service_name: str) -> Tracer:
        return self._tracers[service_name]

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        for provider in self._providers:
            provider.shutdown()


def _create_otlp_exporter(endpoint: str) -> SpanExporter:
    return OTLPSpanExporter(endpoint=endpoint)


def build_service_tracing(
    endpoint: str,
    *,
    instrumentation_version: str = "0.1.0",
    exporter_factory: SpanExporterFactory = _create_otlp_exporter,
    processor_factory: SpanProcessorFactory = BatchSpanProcessor,
) -> ServiceTracing:
    resolved_endpoint = endpoint.strip()
    if not resolved_endpoint:
        provider = NoOpTracerProvider()
        return ServiceTracing(
            {
                service_name: provider.get_tracer(
                    f"minishop.{service_name}",
                    instrumentation_version,
                )
                for service_name in SERVICE_NAMES
            }
        )

    tracers: Dict[str, Tracer] = {}
    providers = []
    for service_name in SERVICE_NAMES:
        provider = SDKTracerProvider(
            resource=Resource.create(
                {
                    "service.name": service_name,
                    "service.namespace": "minishop",
                }
            ),
            shutdown_on_exit=False,
        )
        exporter = exporter_factory(resolved_endpoint)
        provider.add_span_processor(processor_factory(exporter))
        providers.append(provider)
        tracers[service_name] = provider.get_tracer(
            f"minishop.{service_name}",
            instrumentation_version,
        )
    return ServiceTracing(tracers, tuple(providers))


service_tracing = build_service_tracing(
    settings.otlp_traces_endpoint,
    instrumentation_version=settings.app_version,
)
