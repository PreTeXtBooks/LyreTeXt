"""Verify TLS against the operating system's certificate store (#90).

The provider SDKs (google-genai, anthropic, via httpx) trust only certifi's CA
bundle by default. On a machine whose TLS is intercepted by a locally trusted
root — a corporate proxy, TLS-inspecting antivirus — that root is in the OS
store but not in certifi, so every LLM call fails with CERTIFICATE_VERIFY_FAILED
while browsers and curl on the same machine work.

``truststore.inject_into_ssl()`` makes ``ssl.SSLContext`` verify with the OS
(Windows CryptoAPI, macOS Security framework, OpenSSL's system paths on Linux).
CAs a library loads explicitly — httpx loads certifi's bundle — are still
honoured as a fallback, so the effective trust is the OS store plus certifi:
nothing that verified before stops verifying.

Called once from each entry point that can make provider calls (the API module,
which every server mode imports, and the CLI), before any SDK client exists.
"""
from __future__ import annotations

import logging
import ssl

logger = logging.getLogger("lyretext.tls")


def use_os_trust_store() -> bool:
    """Route TLS verification through the OS certificate store. Idempotent.

    Returns True when active. A missing ``truststore`` (an old dev venv) logs a
    warning and leaves certifi-only verification in place rather than failing;
    the frozen bundle's ``--self-check`` treats that case as an error.
    """
    if os_trust_store_active():
        return True
    try:
        import truststore
    except ImportError:
        logger.warning(
            "truststore is not installed; TLS falls back to certifi's CA bundle "
            "and fails behind proxies that use a locally trusted root "
            "(pip install -r requirements.txt)"
        )
        return False
    truststore.inject_into_ssl()
    return True


def os_trust_store_active() -> bool:
    try:
        import truststore
    except ImportError:
        return False
    return ssl.SSLContext is truststore.SSLContext
