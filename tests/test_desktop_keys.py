"""Desktop key handling and local-API protection (#67, decision #84).

* With LYRETEXT_AUTH_TOKEN set, every request but GET /api/health needs the
  token (cookie or X-LyreTeXt-Token header) and a loopback Host; without it
  (dev mode) nothing changes.
* POST /api/internal/keys updates the process env and resets the cached
  inferenceswitch client; POST /api/keys/verify never echoes a key.
* In data-dir mode, run directories must stay inside the data dir.
* Key values and the token are redacted from log records.
"""
from __future__ import annotations

import logging
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from lyretext import config
from lyretext.config import AUTH_TOKEN_ENV, DATA_DIR_ENV, PROVIDER_KEY_ENV
from lyretext.security import (
    AUTH_COOKIE,
    AUTH_HEADER,
    REDACTED,
    KeyRedactionFilter,
    redact,
)

TOKEN = "t0ken-" + "a" * 58
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"
DUMMY_KEY = "dummy-gemini-key-0123456789"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    # Record every variable these tests may touch, so monkeypatch restores it
    # even when the endpoint under test sets or pops it directly.
    for name in [AUTH_TOKEN_ENV, DATA_DIR_ENV, *PROVIDER_KEY_ENV.values()]:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    yield
    config.reset_client()


@pytest.fixture
def desktop(monkeypatch):
    monkeypatch.setenv(AUTH_TOKEN_ENV, TOKEN)


@pytest.fixture
def client():
    from lyretext.api import app

    with TestClient(app, base_url=BASE) as c:
        yield c


def _cookie(value=TOKEN):
    return {"Cookie": f"{AUTH_COOKIE}={value}"}


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

class TestToken:
    def test_no_token_is_401(self, desktop, client):
        assert client.get("/api/settings").status_code == 401

    def test_wrong_token_is_401_by_cookie_and_header(self, desktop, client):
        assert client.get("/api/settings", headers=_cookie("nope")).status_code == 401
        assert client.get("/api/settings", headers={AUTH_HEADER: "nope"}).status_code == 401

    def test_right_token_by_cookie(self, desktop, client):
        assert client.get("/api/settings", headers=_cookie()).status_code == 200

    def test_right_token_by_header(self, desktop, client):
        assert client.get("/api/settings", headers={AUTH_HEADER: TOKEN}).status_code == 200

    def test_static_ui_needs_token_too(self, desktop, client):
        assert client.get("/ui/").status_code == 401
        assert client.get("/ui/", headers=_cookie()).status_code == 200

    def test_health_is_exempt(self, desktop, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_only_get_health_is_exempt(self, desktop, client):
        assert client.post("/api/health").status_code == 401


class TestHost:
    def test_forged_host_rejected_even_with_token(self, desktop, client):
        r = client.get("/api/settings", headers={AUTH_HEADER: TOKEN, "Host": "evil.example:8123"})
        assert r.status_code == 400

    def test_wrong_port_rejected(self, desktop, client):
        r = client.get("/api/health", headers={"Host": "127.0.0.1:9999"})
        assert r.status_code == 400

    def test_localhost_allowed(self, desktop, client):
        r = client.get("/api/settings", headers={AUTH_HEADER: TOKEN, "Host": f"localhost:{PORT}"})
        assert r.status_code == 200


class TestDevMode:
    def test_no_auth_needed(self, client):
        assert client.get("/api/settings").status_code == 200

    def test_any_host_accepted(self, client):
        assert client.get("/api/settings", headers={"Host": "anything:1"}).status_code == 200

    def test_permissive_cors_kept_in_dev(self, client):
        r = client.get("/api/health", headers={"Origin": "http://example.com"})
        assert r.headers.get("access-control-allow-origin") == "*"

    def test_no_cors_in_desktop_mode(self, desktop, client):
        r = client.get("/api/health", headers={"Origin": "http://example.com"})
        assert "access-control-allow-origin" not in r.headers

    def test_internal_keys_refused_without_desktop(self, client):
        r = client.post("/api/internal/keys", json={"keys": {"gemini": DUMMY_KEY}})
        assert r.status_code == 403
        assert "GEMINI_API_KEY" not in os.environ

    def test_dotenv_skipped_in_desktop_mode(self, desktop):
        with patch.object(config, "load_dotenv") as load:
            config._load_dotenv()
        load.assert_not_called()

    def test_dotenv_loaded_in_dev_mode(self):
        with patch.object(config, "load_dotenv") as load:
            config._load_dotenv()
        load.assert_called_once()


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

class TestInternalKeys:
    def test_sets_env_and_resets_client(self, desktop, client):
        config._client = MagicMock()
        r = client.post("/api/internal/keys", json={"keys": {"gemini": DUMMY_KEY}},
                        headers={AUTH_HEADER: TOKEN})
        assert r.status_code == 200
        assert os.environ["GEMINI_API_KEY"] == DUMMY_KEY
        assert config._client is None
        assert {"provider": "gemini", "set": True} in r.json()["keys"]
        assert DUMMY_KEY not in r.text

    def test_null_unsets(self, desktop, client, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY_KEY)
        r = client.post("/api/internal/keys", json={"keys": {"anthropic": None}},
                        headers={AUTH_HEADER: TOKEN})
        assert r.status_code == 200
        assert "ANTHROPIC_API_KEY" not in os.environ
        assert {"provider": "anthropic", "set": False} in r.json()["keys"]

    def test_cookie_is_not_enough(self, desktop, client):
        # The web layer holds only the cookie; key material may only come from
        # the main process, which sends the header.
        r = client.post("/api/internal/keys", json={"keys": {"gemini": DUMMY_KEY}}, headers=_cookie())
        assert r.status_code == 401
        assert "GEMINI_API_KEY" not in os.environ

    def test_unknown_provider_rejected_without_echo(self, desktop, client):
        r = client.post("/api/internal/keys", json={"keys": {"openai": DUMMY_KEY}},
                        headers={AUTH_HEADER: TOKEN})
        assert r.status_code == 400
        assert DUMMY_KEY not in r.text

    def test_malformed_body_not_echoed(self, desktop, client):
        r = client.post("/api/internal/keys", content=f"{{oops {DUMMY_KEY}",
                        headers={AUTH_HEADER: TOKEN, "Content-Type": "application/json"})
        assert r.status_code == 400
        assert DUMMY_KEY not in r.text


class TestVerify:
    def test_never_echoes_key(self, client, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", DUMMY_KEY)
        fake = MagicMock()
        fake.verify_key.return_value = (False, f"400 API key not valid: {DUMMY_KEY}")
        with patch.object(config, "_get_client", return_value=fake):
            r = client.post("/api/keys/verify", json={"provider": "gemini"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert DUMMY_KEY not in r.text
        assert REDACTED in body["detail"]
        fake.verify_key.assert_called_once_with("gemini")

    def test_ok(self, client, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY_KEY)
        fake = MagicMock()
        fake.verify_key.return_value = (True, "Key is valid.")
        with patch.object(config, "_get_client", return_value=fake):
            r = client.post("/api/keys/verify", json={"provider": "anthropic"})
        assert r.json() == {"provider": "anthropic", "ok": True, "detail": "Key is valid."}

    def test_missing_key(self, client):
        r = client.post("/api/keys/verify", json={"provider": "gemini"})
        assert r.json()["ok"] is False
        assert "No API key" in r.json()["detail"]

    def test_unknown_provider(self, client):
        assert client.post("/api/keys/verify", json={"provider": "x"}).status_code == 400

    def test_requires_token_in_desktop_mode(self, desktop, client):
        assert client.post("/api/keys/verify", json={"provider": "gemini"}).status_code == 401


# ---------------------------------------------------------------------------
# Run-dir hardening
# ---------------------------------------------------------------------------

class TestRunDirs:
    @pytest.fixture
    def data_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "data"
        monkeypatch.setenv(DATA_DIR_ENV, str(d))
        return d

    @pytest.mark.parametrize("field,value", [
        ("output_dir", "../escape"),
        ("temp_dir", "../../escape"),
        ("output_dir", "C:/Windows/Temp/lyretext" if os.name == "nt" else "/tmp/lyretext"),
    ])
    def test_escape_rejected(self, data_dir, client, field, value):
        body = {"source": "somewhere", field: value}
        with patch("lyretext.api.start_run") as start:
            r = client.post("/api/runs", json=body)
        assert r.status_code == 400
        assert field in r.json()["detail"]
        start.assert_not_called()

    def test_upload_escape_rejected(self, data_dir, client):
        r = client.post(
            "/api/runs/upload",
            files=[("files", ("a.Rmd", b"x"))],
            data={"paths": ["proj/a.Rmd"], "output_dir": "../out"},
        )
        assert r.status_code == 400

    def test_inside_data_dir_accepted(self, data_dir, client):
        with patch("lyretext.api.start_run"):
            r = client.post("/api/runs", json={
                "source": "somewhere", "output_dir": "output/book",
                "temp_dir": str(data_dir / "temp"),
            })
        assert r.status_code == 202

    def test_dev_mode_unrestricted(self, client, tmp_path):
        with patch("lyretext.api.start_run"):
            r = client.post("/api/runs", json={"source": "x", "output_dir": str(tmp_path / "anywhere")})
        assert r.status_code == 202


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

class TestRedaction:
    def _record(self, msg, args=(), exc_info=None):
        return logging.LogRecord("t", logging.ERROR, __file__, 1, msg, args, exc_info)

    def test_redact_helper(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", DUMMY_KEY)
        assert redact(f"bad key {DUMMY_KEY}!") == f"bad key {REDACTED}!"

    def test_filter_masks_message_args_and_traceback(self, monkeypatch, desktop):
        monkeypatch.setenv("GEMINI_API_KEY", DUMMY_KEY)
        try:
            raise RuntimeError(f"provider said {DUMMY_KEY}")
        except RuntimeError as exc:
            import sys
            record = self._record("call failed with %s token=%s", (exc, TOKEN), sys.exc_info())
        KeyRedactionFilter().filter(record)
        formatted = logging.Formatter().format(record)
        assert DUMMY_KEY not in formatted
        assert TOKEN not in formatted
        assert REDACTED in formatted

    def test_filter_keeps_arg_shape(self, monkeypatch):
        # uvicorn's access formatter unpacks record.args as a 5-tuple.
        monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY_KEY)
        record = self._record("%s %s %s %s %d", ("127.0.0.1", "GET", f"/x?k={DUMMY_KEY}", "1.1", 200))
        KeyRedactionFilter().filter(record)
        assert len(record.args) == 5 and record.args[4] == 200
        assert DUMMY_KEY not in record.getMessage()

    def test_installed_for_every_logger(self, monkeypatch, caplog):
        import lyretext.api  # noqa: F401  (installs the record factory)

        monkeypatch.setenv("GEMINI_API_KEY", DUMMY_KEY)
        with caplog.at_level(logging.WARNING):
            logging.getLogger("some.sdk").warning("key=%s", DUMMY_KEY)
        assert DUMMY_KEY not in caplog.text
        assert REDACTED in caplog.text

    def test_short_values_not_treated_as_secrets(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        assert redact("x marks the spot") == "x marks the spot"
