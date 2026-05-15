from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class ProviderHealth:
    failures: int = 0
    last_failure_at: float = 0.0
    cooldown_until: float = 0.0
    successes: int = 0


_STATE: dict[str, ProviderHealth] = {}
FAILURE_THRESHOLD = 2
COOLDOWN_SECONDS = 180.0


def _entry(key: str) -> ProviderHealth:
    health = _STATE.get(key)
    if health is None:
        health = ProviderHealth()
        _STATE[key] = health
    return health


def provider_is_available(key: str) -> bool:
    return time.time() >= _entry(key).cooldown_until


def record_provider_success(key: str) -> None:
    health = _entry(key)
    health.failures = 0
    health.cooldown_until = 0.0
    health.successes += 1


def record_provider_failure(key: str) -> None:
    health = _entry(key)
    health.failures += 1
    health.last_failure_at = time.time()
    if health.failures >= FAILURE_THRESHOLD:
        health.cooldown_until = time.time() + COOLDOWN_SECONDS


def provider_health_snapshot() -> dict[str, dict]:
    now = time.time()
    return {
        key: {
            "failures": value.failures,
            "successes": value.successes,
            "last_failure_at": value.last_failure_at,
            "cooldown_until": value.cooldown_until,
            "available": now >= value.cooldown_until,
        }
        for key, value in sorted(_STATE.items())
    }
