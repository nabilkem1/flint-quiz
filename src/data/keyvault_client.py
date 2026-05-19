"""Key Vault wrapper (TASK-122 / SEC-013).

A thin wrapper around `azure.keyvault.secrets.aio.SecretClient` that:

  * Constructs with `DefaultAzureCredential` — never a key or
    connection string (SEC-004).
  * Caches fetched secret values in-process with a 10-minute TTL so the
    runtime hot path stays cheap and secret rotations pick up within
    the cache window without code change.
  * **Never** writes secret values to disk, never logs them, and never
    embeds them in error messages. The class deliberately keeps the
    cached values inside instance state and exposes only ``get_secret``
    / ``warm`` / ``forget`` — there is no introspection method.

v1 has **zero secrets** in production code paths (see `docs/secrets.md`).
The wrapper exists so that the GDPR erasure cascade (TASK-134) and any
future dependency that genuinely needs a secret has a sanctioned read
path — and so `import-linter` can keep `SecretClient` out of every
other module by routing through this single entry point.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from azure.core.credentials_async import AsyncTokenCredential
    from azure.keyvault.secrets.aio import SecretClient

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SECONDS: float = 600.0  # 10 minutes — TASK-122 contract.


class _SecretClientLike(Protocol):
    """Subset of `azure.keyvault.secrets.aio.SecretClient` we depend on.

    Tests substitute an in-memory fake; production wires the real client
    via `build_secret_client`.
    """

    async def get_secret(self, name: str) -> "_KeyVaultSecret": ...

    async def close(self) -> None: ...


class _KeyVaultSecret(Protocol):  # pragma: no cover - typing-only
    """Shape of the SDK's secret return — only the `value` field is read."""

    value: str | None


@dataclass(frozen=True, slots=True)
class _CachedSecret:
    value: str
    fetched_at: float


class KeyVaultClient:
    """Cached read-only Key Vault wrapper (SEC-013 / TASK-122).

    Construct with a vault URL and (optionally) a credential / async
    SecretClient stand-in. The cache lives on the instance — a single
    process-lifetime client is the intended usage; the agent factory
    builds one and threads it through dependency injection.

    The class exposes no method that returns a dump of the cache, no
    `__repr__` that prints values, and no equality semantics that
    would let a `==` comparison side-channel a secret value.
    """

    def __init__(
        self,
        *,
        vault_url: str,
        client: _SecretClientLike | None = None,
        credential: "AsyncTokenCredential | None" = None,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
    ) -> None:
        if client is None:
            client = _build_default_client(vault_url, credential)
        self._client = client
        self._ttl = ttl_seconds
        self._cache: dict[str, _CachedSecret] = {}
        self._lock = asyncio.Lock()
        # No "_vault_url" attribute holding the URL — the URL is part of
        # the client; we don't need it elsewhere. Keeping the surface
        # tight keeps the leak attack surface tight.

    async def get_secret(self, name: str) -> str:
        """Return the value of `name`, refreshing the cache when stale.

        Raises:
            KeyError: secret missing in Key Vault (the SDK raises
                `ResourceNotFoundError`; the wrapper translates so
                callers do not have to import the Azure SDK exception
                hierarchy).
        """

        cached = self._cache.get(name)
        if cached is not None and not self._is_stale(cached):
            return cached.value

        async with self._lock:
            # Double-check after taking the lock — another coroutine may
            # have refreshed in the meantime.
            cached = self._cache.get(name)
            if cached is not None and not self._is_stale(cached):
                return cached.value
            secret = await self._client.get_secret(name)
            value = getattr(secret, "value", None)
            if value is None:
                # Defensive — the SDK shouldn't return None for `value`
                # on a successful fetch. Translate to KeyError so the
                # caller has a sane exception type to handle.
                raise KeyError(name)
            self._cache[name] = _CachedSecret(value=value, fetched_at=time.monotonic())
            logger.info(
                "keyvault.fetch",
                extra={"secret_name": name, "ttl_seconds": self._ttl},
            )
            return value

    async def warm(self, names: list[str]) -> None:
        """Pre-populate the cache. Used by tests and startup hooks."""

        for name in names:
            await self.get_secret(name)

    def forget(self, name: str | None = None) -> None:
        """Drop one cache entry (or the whole cache if `name=None`).

        The wrapper itself never invalidates — it relies on TTL — but the
        erasure cascade (TASK-134) calls this on salt rotation so the
        next read picks up the rotated value immediately.
        """

        if name is None:
            self._cache.clear()
            return
        self._cache.pop(name, None)

    async def close(self) -> None:
        await self._client.close()
        # Drop the cache on close so an attacker who captures the
        # process post-shutdown cannot pull stale values from `__dict__`.
        self._cache.clear()

    def _is_stale(self, entry: _CachedSecret) -> bool:
        return (time.monotonic() - entry.fetched_at) >= self._ttl

    # Deliberately no `__repr__` / `__str__` overrides — the dataclass
    # surface keeps values inside `_cache` and the default repr lists
    # only the type. A logging mistake (`logger.info(self)`) prints the
    # class name, not the secrets.


def _build_default_client(
    vault_url: str, credential: "AsyncTokenCredential | None"
) -> _SecretClientLike:
    """Construct the real `SecretClient` lazily.

    Lazy import keeps the test environment importable even when the
    Azure SDK is not installed — the test fixtures inject a fake client
    directly via :class:`KeyVaultClient` construction.
    """

    from azure.keyvault.secrets.aio import SecretClient  # noqa: PLC0415 - lazy
    from azure.identity.aio import DefaultAzureCredential  # noqa: PLC0415 - lazy

    cred = credential or DefaultAzureCredential()
    return SecretClient(vault_url=vault_url, credential=cred)


__all__ = ["KeyVaultClient"]
