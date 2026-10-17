"""Provider TLS verifies against the OS certificate store (#90)."""
from __future__ import annotations

import ssl

import truststore

from lyretext.tls import os_trust_store_active, use_os_trust_store


def test_api_import_switches_to_os_trust_store():
    import lyretext.api  # noqa: F401  (every server mode imports it)

    assert os_trust_store_active()
    assert ssl.SSLContext is truststore.SSLContext


def test_idempotent():
    assert use_os_trust_store() is True
    assert use_os_trust_store() is True
    assert ssl.SSLContext is truststore.SSLContext


def test_httpx_contexts_use_os_store():
    # httpx builds its context with ssl.create_default_context(cafile=certifi);
    # with truststore injected that context verifies via the OS, with certifi's
    # CAs as a fallback.
    import httpx

    use_os_trust_store()
    ctx = httpx.create_ssl_context()
    assert isinstance(ctx, truststore.SSLContext)
