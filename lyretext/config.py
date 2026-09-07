from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Literal, Optional, Any
from langchain_core.runnables import RunnableConfig

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from google import genai
import anthropic


# ============================================================================
# Runtime Configuration Schema (Phase 1)
# ============================================================================

class GlobalRuntimeOptions(BaseModel):
    """Global runtime options applied to the entire workflow."""
    execution_mode: Literal["upload", "direct"] = "direct"
    apply_mode: Literal["auto_apply", "dry_run"] = "auto_apply"
    create_backup: bool = False
    provider: Literal["gemini", "anthropic"] = "gemini"
    llm_model: Optional[str] = None  # None means use default from env
    verbosity: Literal["silent", "normal", "debug"] = "normal"
    pipeline: Literal["rmd", "qmd", "tex"] = "rmd"

    # Markdown → PreTeXt conversion (deterministic, via pandoc + pretext.lua).
    # None means auto-discover; see lyretext.convert.pandoc.find_pandoc.
    pandoc_exe: Optional[str] = None
    pandoc_writer: str = "pretext.lua"
    # Extra pandoc CLI args, comma-separated or a JSON list (e.g. for
    # --lua-filter, -M metadata, etc). None means no extra args.
    pandoc_extra_args: Optional[str] = None

    # Editing agent. It always runs when the user asks for a fix at the review
    # gate; auto_edit additionally lets the review loop invoke it *unasked* for
    # findings from checks marked auto_fixable. Off by default: deciding what
    # gets rewritten is the point of the human gate, and silently repairing
    # findings before the user sees them undercuts it.
    auto_edit: bool = False
    max_edit_iterations: int = 2

    # Normalise .ptx whitespace on every write, so pandoc's layout and the
    # editing agent's converge — see lyretext.format.pretext_fmt.
    format_output: bool = True

    model_config = {"use_enum_values": False}


class NodeOverrides(BaseModel):
    """Per-node configuration overrides. Any None field inherits from global."""
    execution_mode: Optional[Literal["upload", "direct"]] = None
    apply_mode: Optional[Literal["auto_apply", "dry_run"]] = None
    create_backup: Optional[bool] = None
    provider: Optional[Literal["gemini", "anthropic"]] = None
    llm_model: Optional[str] = None
    verbosity: Optional[Literal["silent", "normal", "debug"]] = None
    pandoc_exe: Optional[str] = None
    pandoc_writer: Optional[str] = None
    pandoc_extra_args: Optional[str] = None
    auto_edit: Optional[bool] = None
    max_edit_iterations: Optional[int] = None
    format_output: Optional[bool] = None

    model_config = {"use_enum_values": False, "extra": "forbid"}


class RuntimeConfig(BaseModel):
    """Complete runtime configuration with global and per-node settings."""
    global_options: GlobalRuntimeOptions = Field(default_factory=GlobalRuntimeOptions)
    node_overrides: dict[str, NodeOverrides] = Field(default_factory=dict)
    pipeline_options: dict[str, Any] = Field(default_factory=dict)

    model_config = {"use_enum_values": False}

    @field_validator("node_overrides", mode="before")
    @classmethod
    def validate_node_overrides(cls, v):
        """Ensure node_overrides dict contains valid NodeOverrides."""
        if not isinstance(v, dict):
            raise ValueError("node_overrides must be a dict")
        for node_id, overrides in v.items():
            if not isinstance(node_id, str):
                raise ValueError(f"node_overrides keys must be strings, got {type(node_id)}")
            if isinstance(overrides, dict):
                v[node_id] = NodeOverrides(**overrides)
            elif not isinstance(overrides, NodeOverrides):
                raise ValueError(f"node_overrides[{node_id}] must be dict or NodeOverrides")
        return v

    def get_node_config(self, node_id: str) -> dict[str, Any]:
        """
        Resolve final configuration for a specific node.
        Returns dict with merged global + node-specific overrides.
        Raises KeyError if node_id has overrides for a nonexistent field.
        """
        overrides = self.node_overrides.get(node_id)
        config = self.global_options.model_dump()
        
        if overrides:
            override_dict = overrides.model_dump(exclude_none=True)
            # Strict validation: only allow keys that exist in global config
            for key in override_dict:
                if key not in config:
                    raise KeyError(
                        f"Unknown config key '{key}' in node overrides for '{node_id}'. "
                        f"Allowed keys: {list(config.keys())}"
                    )
            config.update(override_dict)
        
        return config


class ConfigResolver:
    """Handles configuration precedence: env < config file < runtime payload."""

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """Deep merge override dict into base dict."""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ConfigResolver._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    @staticmethod
    def resolve_from_layers(
        config_file: Optional[str | Path] = None,
        runtime_payload: Optional[dict] = None,
        env_prefix: str = "LYRETEXT_",
    ) -> RuntimeConfig:
        """
        Resolve configuration from multiple layers with strict precedence:
        1. Environment variables (lowest priority)
        2. Config file (if provided)
        3. Runtime payload (highest priority)
        
        Raises:
            FileNotFoundError: if config_file does not exist
            ValueError: if config values are invalid
        """
        # Start with env defaults
        env_config = ConfigResolver._load_from_env(env_prefix)
        
        # Load config file if provided
        file_config = {}
        if config_file:
            file_config = ConfigResolver._load_from_file(config_file)
        
        # Merge layers with deep merge (runtime > file > env)
        merged = ConfigResolver._deep_merge(env_config, file_config)
        if runtime_payload:
            merged = ConfigResolver._deep_merge(merged, runtime_payload)
        
        # Validate and create config object
        try:
            return RuntimeConfig(**merged)
        except Exception as e:
            raise ValueError(f"Invalid configuration: {e}")

    @staticmethod
    def _load_from_env(prefix: str = "LYRETEXT_") -> dict:
        """Load configuration from environment variables with strict parsing."""
        config_dict = {}
        
        # Map env vars to config keys under global_options
        env_mapping = {
            f"{prefix}EXECUTION_MODE": "execution_mode",
            f"{prefix}APPLY_MODE": "apply_mode",
            f"{prefix}CREATE_BACKUP": "create_backup",
            f"{prefix}PROVIDER": "provider",
            f"{prefix}LLM_MODEL": "llm_model",
            f"{prefix}VERBOSITY": "verbosity",
            f"{prefix}PIPELINE": "pipeline",
            f"{prefix}PANDOC_EXE": "pandoc_exe",
            f"{prefix}PANDOC_WRITER": "pandoc_writer",
            f"{prefix}PANDOC_EXTRA_ARGS": "pandoc_extra_args",
            f"{prefix}AUTO_EDIT": "auto_edit",
            f"{prefix}MAX_EDIT_ITERATIONS": "max_edit_iterations",
            f"{prefix}FORMAT_OUTPUT": "format_output",
        }

        # Env vars are always strings; these keys need coercing before pydantic
        # sees them.
        bool_keys = {"create_backup", "auto_edit", "format_output"}
        int_keys = {"max_edit_iterations"}

        for env_key, config_key in env_mapping.items():
            if env_key in os.environ:
                value: Any = os.environ[env_key]
                if config_key in bool_keys:
                    value = value.lower() in ("true", "1", "yes")
                elif config_key in int_keys:
                    try:
                        value = int(value)
                    except ValueError:
                        raise ValueError(
                            f"{env_key} must be an integer, got {value!r}"
                        )
                config_dict[config_key] = value

        return {"global_options": config_dict} if config_dict else {}

    @staticmethod
    def _load_from_file(config_file: str | Path) -> dict:
        """Load configuration from YAML or JSON file."""
        path = Path(config_file)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {config_file}")
        
        if path.suffix in (".yaml", ".yml"):
            try:
                import yaml
                with open(path) as f:
                    data = yaml.safe_load(f)
                    return data or {}
            except ImportError:
                raise ImportError(
                    "PyYAML required for .yaml config files. "
                    "Install with: pip install pyyaml"
                )
        elif path.suffix == ".json":
            with open(path) as f:
                return json.load(f)
        else:
            raise ValueError(f"Unsupported config file format: {path.suffix}")


def resolve_node_opts(
    state: dict,
    node_id: str,
    run_config: RunnableConfig | None = None,
) -> dict:
    """
    Extract resolved runtime options for a specific node.
    Prefers runtime options passed via LangGraph configurable runtime context,
    then falls back to legacy state fields for compatibility.
    
    Args:
        state: LangGraph node state dict
        node_id: Stable logical node identifier (used for per-node override lookup)
        
    Returns:
        dict with keys: execution_mode, apply_mode, create_backup, provider,
                        llm_model, verbosity, pipeline, pandoc_exe,
                        pandoc_writer, auto_edit, max_edit_iterations,
                        format_output
    """
    if run_config is not None:
        configurable = run_config.get("configurable", {})
        runtime_options = configurable.get("runtime_options")
        if isinstance(runtime_options, dict):
            return RuntimeConfig(**runtime_options).get_node_config(node_id)

    # Fallback: read flat fields from state for legacy callers.
    return {
        "execution_mode": state.get("execution_mode", "direct"),
        "apply_mode": state.get("apply_mode", "auto_apply"),
        "create_backup": state.get("create_backup", False),
        "provider": state.get("provider", "gemini"),
        "llm_model": state.get("llm_model"),
        "verbosity": state.get("verbosity", "normal"),
        "pipeline": state.get("pipeline", "rmd"),
        "pandoc_exe": state.get("pandoc_exe"),
        "pandoc_writer": state.get("pandoc_writer", "pretext.lua"),
        "pandoc_extra_args": state.get("pandoc_extra_args"),
        "auto_edit": state.get("auto_edit", False),
        "max_edit_iterations": state.get("max_edit_iterations", 2),
        "format_output": state.get("format_output", True),
    }


def get_resolved_config(
    config_file: Optional[str | Path] = None,
    runtime_payload: Optional[dict] = None,
) -> RuntimeConfig:
    """
    Public API to get fully resolved runtime configuration.
    Precedence: environment < config_file < runtime_payload
    
    Args:
        config_file: Optional path to YAML or JSON config file
        runtime_payload: Optional dict to override specific values
        
    Returns:
        RuntimeConfig: Fully resolved configuration object
        
    Raises:
        FileNotFoundError: if config_file does not exist
        ValueError: if configuration is invalid
    """
    load_dotenv()
    return ConfigResolver.resolve_from_layers(
        config_file=config_file,
        runtime_payload=runtime_payload,
    )


# ============================================================================
# LLM Configuration (existing)
# ============================================================================

def create_llm(model_type: str = "gemini", **options):
    if model_type == "gemini":
        return create_gemini_llm(**options)
    elif model_type == "anthropic":
        return create_anthropic_llm(**options)
    else:
        raise ValueError(f"Unsupported model type: {model_type}")

def create_gemini_llm(**options) -> ChatGoogleGenerativeAI:
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    model_name = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

    if not api_key:
        raise ValueError("GEMINI_API_KEY is required to initialize Gemini.")

    return ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key, **options)

def create_anthropic_llm(**options) -> ChatAnthropic:
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    model_name = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is required to initialize Anthropic.")
    
    return ChatAnthropic(model=model_name, anthropic_api_key=api_key, **options)

def initialise_client(model_type: str = "gemini", **options):
    if model_type == "gemini":
        return initialise_gemini_client(**options)
    elif model_type == "anthropic":
        return initialise_anthropic_client(**options)
    else:
        raise ValueError(f"Unsupported model type: {model_type}")

def initialise_gemini_client(**options):
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise ValueError("GEMINI_API_KEY is required to initialize Gemini client.")

    return genai.Client(**options)

def initialise_anthropic_client(**options):
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is required to initialize Anthropic client.")

    return anthropic.Client(api_key=api_key, **options).beta