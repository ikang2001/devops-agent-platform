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
DynamicTracerFactory = Callable[[str], Tracer]


class ServiceTracing:
    def __init__(
        self,
        tracers: Dict[str, Tracer],
        providers: Tuple[SDKTracerProvider, ...] = (),
        dynamic_tracer_factory: DynamicTracerFactory | None = None,
    ) -> None:
        self._tracers = tracers
        self._providers = list(providers)
        self._dynamic_tracer_factory = dynamic_tracer_factory
        self._shutdown = False

    @property
    def enabled(self) -> bool:
        return bool(self._providers)

    def tracer(self, service_name: str) -> Tracer:
        tracer = self._tracers.get(service_name)
        if tracer is not None:
            return tracer
        if self._dynamic_tracer_factory is None:
            return next(iter(self._tracers.values()))
        tracer = self._dynamic_tracer_factory(service_name)
        self._tracers[service_name] = tracer
        return tracer

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
    providers: list[SDKTracerProvider] = []

    def build_tracer(service_name: str) -> Tracer:
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
        return provider.get_tracer(
            f"minishop.{service_name}",
            instrumentation_version,
        )

    for service_name in SERVICE_NAMES:
        tracers[service_name] = build_tracer(service_name)
    return ServiceTracing(tracers, tuple(providers), build_tracer)


service_tracing = build_service_tracing(
    settings.otlp_traces_endpoint,
    instrumentation_version=settings.app_version,
)
