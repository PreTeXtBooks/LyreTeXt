"""Unit tests for the inferenceswitch-backed LLM adapter in lyretext.config.

The adapter must preserve the call sites' contract —
``create_llm(provider).with_structured_output(schema).invoke(messages) -> dict``
with a ``.model`` attribute — while routing text prompts through
inferenceswitch and keeping the LangChain path for the file-upload case
(decision #61).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lyretext import config


def _msg(content):
    """A minimal stand-in for a LangChain message (only `.content` is read)."""
    return SimpleNamespace(content=content)


_SCHEMA = {"type": "object", "properties": {"manifest": {"type": "array"}}}


def test_create_llm_exposes_provider_and_model():
    llm = config.create_llm("anthropic")
    assert llm.provider == "anthropic"
    # Default model for anthropic, used by the eval harness's live-mode capture.
    assert llm.model == "claude-haiku-4-5-20251001"


def test_default_model_reads_env(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-custom-x")
    assert config.create_llm("gemini").model == "gemini-custom-x"


def test_unsupported_provider_rejected():
    with pytest.raises(ValueError):
        config.create_llm("nope")


def test_invoke_before_schema_raises():
    with pytest.raises(ValueError):
        config.create_llm("gemini").invoke([_msg("hi")])


def test_with_structured_output_is_bound_copy():
    base = config.create_llm("gemini")
    bound = base.with_structured_output(_SCHEMA)
    assert bound is not base
    assert bound._schema == _SCHEMA
    assert base._schema is None  # original stays unbound


def test_text_invoke_routes_through_inferenceswitch():
    client = MagicMock()
    client.generate_structured_json.return_value = {"manifest": [1, 2]}
    with patch.object(config, "_get_client", return_value=client):
        llm = config.create_llm("gemini").with_structured_output(_SCHEMA)
        out = llm.invoke([_msg([
            {"type": "text", "text": "PROMPT"},
            {"type": "text", "text": "BODY"},
        ])])
    assert out == {"manifest": [1, 2]}
    kwargs = client.generate_structured_json.call_args.kwargs
    assert kwargs["provider"] == "gemini"
    assert kwargs["model"] == "gemini-3.1-flash-lite"
    assert kwargs["schema"] == _SCHEMA
    assert kwargs["prompt"] == "PROMPT\n\nBODY"
    assert kwargs["temperature"] == 0.1


def test_file_parts_fall_back_to_langchain():
    fallback_llm = MagicMock()
    fallback_llm.with_structured_output.return_value.invoke.return_value = {"x": 1}
    with patch.object(config, "_create_langchain_llm", return_value=fallback_llm) as mk, \
         patch.object(config, "_get_client") as get_client:
        llm = config.create_llm("gemini").with_structured_output(_SCHEMA)
        msgs = [_msg([
            {"type": "text", "text": "PROMPT"},
            {"type": "file", "file_id": "uri://f", "mime_type": "text/markdown"},
        ])]
        out = llm.invoke(msgs)
    assert out == {"x": 1}
    mk.assert_called_once_with("gemini")
    fallback_llm.with_structured_output.assert_called_once_with(_SCHEMA)
    fallback_llm.with_structured_output.return_value.invoke.assert_called_once_with(msgs)
    get_client.assert_not_called()  # never hit inferenceswitch for file parts


def test_string_content_is_flattened():
    client = MagicMock()
    client.generate_structured_json.return_value = {}
    with patch.object(config, "_get_client", return_value=client):
        config.create_llm("gemini").with_structured_output(_SCHEMA).invoke(
            [_msg("just a string")]
        )
    assert client.generate_structured_json.call_args.kwargs["prompt"] == "just a string"
