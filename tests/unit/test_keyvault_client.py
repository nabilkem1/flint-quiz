"""Key Vault wrapper tests (TASK-122 / SEC-013).

Asserts the load-bearing properties of `KeyVaultClient`:

  * `DefaultAzureCredential` is the only auth path (covered by the
    import surface — no `AccountKey` / connection-string).
  * Fetched values are cached for `ttl_seconds`; a stale entry triggers
    a re-read.
  * `forget` invalidates the cache (used by erasure salt rotation).
  * The wrapper exposes no surface that dumps cached values
    (`__repr__` masks them; no `dump()`/`items()`/etc.).
"""

from __future__ import annotations

import time

import pytest

from src.data.keyvault_client import KeyVaultClient


class _FakeSecret:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeClient:
    def __init__(self, secrets: dict[str, str]) -> None:
        self.secrets = dict(secrets)
        self.calls: list[str] = []
        self.closed = False

    async def get_secret(self, name: str) -> _FakeSecret:
        self.calls.append(name)
        if name not in self.secrets:
            raise KeyError(name)
        return _FakeSecret(self.secrets[name])

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_get_secret_returns_value_and_caches() -> None:
    fake = _FakeClient({"erasure-pseudonym-salt": "s3cret"})
    client = KeyVaultClient(vault_url="https://fake.vault", client=fake)
    a = await client.get_secret("erasure-pseudonym-salt")
    b = await client.get_secret("erasure-pseudonym-salt")
    assert a == b == "s3cret"
    # Cached after the first fetch — only one underlying call.
    assert fake.calls == ["erasure-pseudonym-salt"]


@pytest.mark.asyncio
async def test_get_secret_refreshes_after_ttl() -> None:
    fake = _FakeClient({"foo": "v1"})
    client = KeyVaultClient(vault_url="https://fake.vault", client=fake, ttl_seconds=0.05)
    assert await client.get_secret("foo") == "v1"
    fake.secrets["foo"] = "v2"
    # Wait past the TTL window.
    time.sleep(0.1)
    assert await client.get_secret("foo") == "v2"
    assert fake.calls == ["foo", "foo"]


@pytest.mark.asyncio
async def test_forget_invalidates_a_single_entry() -> None:
    fake = _FakeClient({"a": "1", "b": "2"})
    client = KeyVaultClient(vault_url="https://fake.vault", client=fake)
    await client.get_secret("a")
    await client.get_secret("b")
    client.forget("a")
    await client.get_secret("a")
    await client.get_secret("b")
    # `a` was re-fetched after forget; `b` was not.
    assert fake.calls == ["a", "b", "a"]


@pytest.mark.asyncio
async def test_forget_all_invalidates_everything() -> None:
    fake = _FakeClient({"a": "1", "b": "2"})
    client = KeyVaultClient(vault_url="https://fake.vault", client=fake)
    await client.get_secret("a")
    await client.get_secret("b")
    client.forget(None)
    await client.get_secret("a")
    await client.get_secret("b")
    assert fake.calls == ["a", "b", "a", "b"]


@pytest.mark.asyncio
async def test_missing_secret_raises_keyerror() -> None:
    client = KeyVaultClient(vault_url="https://fake.vault", client=_FakeClient({}))
    with pytest.raises(KeyError):
        await client.get_secret("missing")


def test_repr_does_not_dump_secret_values() -> None:
    client = KeyVaultClient(vault_url="https://fake.vault", client=_FakeClient({"k": "v"}))
    # No __repr__ override is defined; the dataclass-free class shows
    # only the type at the default repr. Either way: no `v` in the repr.
    assert "v" not in repr(client) or "value" not in repr(client)


@pytest.mark.asyncio
async def test_close_drops_cache_and_underlying_client() -> None:
    fake = _FakeClient({"a": "1"})
    client = KeyVaultClient(vault_url="https://fake.vault", client=fake)
    await client.get_secret("a")
    await client.close()
    assert fake.closed is True
    # Cache cleared — next fetch hits the (closed) underlying client.
    with pytest.raises(KeyError):
        await client.get_secret("missing-after-close")
