from __future__ import annotations

from dataclasses import dataclass

from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker, CircuitOpenError
from reliability_lab.providers import FakeLLMProvider, ProviderError, ProviderResponse


@dataclass(slots=True)
class GatewayResponse:
    text: str
    route: str
    provider: str | None
    cache_hit: bool
    latency_ms: float
    estimated_cost: float
    error: str | None = None


class ReliabilityGateway:
    """Routes requests through cache, circuit breakers, and fallback providers."""

    def __init__(
        self,
        providers: list[FakeLLMProvider],
        breakers: dict[str, CircuitBreaker],
        cache: ResponseCache | SharedRedisCache | None = None,
    ):
        self.providers = providers
        self.breakers = breakers
        self.cache = cache

    def complete(self, prompt: str) -> GatewayResponse:
        """Return a reliable response or a static fallback.

        Pipeline: Cache → Circuit Breaker → Provider Chain → Static Fallback
        """
        # 1. CACHE CHECK
        if self.cache is not None:
            cached_text, score = self.cache.get(prompt)
            if cached_text is not None:
                return GatewayResponse(
                    text=cached_text,
                    route=f"cache_hit:{score:.2f}",
                    provider=None,
                    cache_hit=True,
                    latency_ms=0,
                    estimated_cost=0,
                    error=None
                )

        # 2. PROVIDER FALLBACK CHAIN
        last_error: str | None = None
        for i, provider in enumerate(self.providers):
            breaker = self.breakers[provider.name]
            try:
                response = breaker.call(provider.complete, prompt)

                # Store in cache if available
                if self.cache is not None:
                    self.cache.set(prompt, response.text, {"provider": provider.name})

                # Determine route
                route = "primary" if i == 0 else "fallback"

                return GatewayResponse(
                    text=response.text,
                    route=route,
                    provider=response.provider,
                    cache_hit=False,
                    latency_ms=response.latency_ms,
                    estimated_cost=response.estimated_cost,
                    error=None
                )
            except (ProviderError, CircuitOpenError) as e:
                last_error = str(e)
                continue

        # 3. STATIC FALLBACK (all providers failed)
        return GatewayResponse(
            text="The service is temporarily degraded. Please try again soon.",
            route="static_fallback",
            provider=None,
            cache_hit=False,
            latency_ms=0,
            estimated_cost=0,
            error=last_error
        )
