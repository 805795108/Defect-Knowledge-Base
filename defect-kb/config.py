"""Load and validate defect-kb.yaml project configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


_DEFAULT_CONFIG_NAME = "defect-kb.yaml"


def find_project_root(start: str | None = None) -> Path:
    """Walk up from *start* (default cwd) looking for defect-kb.yaml."""
    current = Path(start) if start else Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / _DEFAULT_CONFIG_NAME).exists():
            return parent
    raise FileNotFoundError(
        f"Cannot find {_DEFAULT_CONFIG_NAME} in {current} or any parent directory. "
        "Run 'python defect-kb/cli.py init' first."
    )


def load_config(project_root: str | None = None) -> dict[str, Any]:
    root = find_project_root(project_root)
    cfg_path = root / _DEFAULT_CONFIG_NAME
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_root"] = str(root)
    return cfg


def resolve_path(cfg: dict, relative_key: str) -> Path:
    """Resolve a config value as a path relative to project root."""
    root = Path(cfg["_root"])
    value = cfg
    for part in relative_key.split("."):
        value = value[part]
    return root / value


_LEGACY_ENV_MAP: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "doubao": "ARK_API_KEY",
}


def get_provider_config(cfg: dict, usage: str = "llm") -> dict[str, Any]:
    """Get provider configuration for *usage* ('llm' or 'embedding').

    Resolution order:
      1. ``llm.providers.<name>`` dict (new multi-provider format)
      2. Legacy flat ``llm`` section (backward compatible)

    Returns a dict with keys: provider, api_key, base_url, model,
    embedding_model.
    """
    llm_cfg = cfg.get("llm", {})
    providers = llm_cfg.get("providers", {})

    if usage == "embedding":
        provider_name = llm_cfg.get(
            "embedding_provider", llm_cfg.get("provider", "openai")
        )
    else:
        provider_name = llm_cfg.get("provider", "openai")

    if provider_name == "local":
        local_p = providers.get("local", {})
        return {
            "provider": "local",
            "api_key": "",
            "base_url": "",
            "model": "",
            "embedding_model": local_p.get("embedding_model", "all-MiniLM-L6-v2"),
        }

    # --- New config path: providers dict ---
    if providers and provider_name in providers:
        p = providers[provider_name]
        env_var = p.get("env_key", _LEGACY_ENV_MAP.get(provider_name, "OPENAI_API_KEY"))
        api_key = os.environ.get(env_var, "")
        if not api_key:
            raise EnvironmentError(
                f"Environment variable {env_var} is not set (provider: {provider_name})."
            )
        return {
            "provider": provider_name,
            "api_key": api_key,
            "base_url": p.get("base_url", ""),
            "model": p.get("model", ""),
            "embedding_model": p.get("embedding_model", "text-embedding-3-small"),
        }

    # --- Legacy fallback: flat llm section ---
    env_var = _LEGACY_ENV_MAP.get(provider_name, "OPENAI_API_KEY")
    api_key = os.environ.get(env_var, "")
    if not api_key:
        raise EnvironmentError(f"Environment variable {env_var} is not set.")
    return {
        "provider": provider_name,
        "api_key": api_key,
        "base_url": llm_cfg.get("base_url", ""),
        "model": llm_cfg.get("model", "gpt-4o-mini"),
        "embedding_model": llm_cfg.get("embedding_model", "text-embedding-3-small"),
    }


def get_api_key(cfg: dict) -> str:
    """Read LLM API key from environment (backward-compatible wrapper)."""
    return get_provider_config(cfg, "llm")["api_key"]
