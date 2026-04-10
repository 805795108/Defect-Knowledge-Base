"""LLM abstraction layer — isolates provider-specific calling logic.

Supports OpenAI-compatible providers (OpenAI, DeepSeek, Qwen, Doubao)
via the ``openai`` SDK, and Anthropic Claude via the ``anthropic`` SDK.
"""

from __future__ import annotations

import logging
import os
from typing import Any

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# LLM chat completion
# ---------------------------------------------------------------------------

def call_llm(cfg: dict, prompt: str) -> str:
    """Call an LLM with the given prompt, routing to the correct provider."""
    from config import get_provider_config

    pc = get_provider_config(cfg, "llm")

    if pc["provider"] == "claude":
        return _call_claude(pc, prompt)
    return _call_openai_compat(pc, prompt)


def _call_openai_compat(pc: dict, prompt: str) -> str:
    """OpenAI / DeepSeek / Qwen / Doubao — all use the openai SDK."""
    from openai import OpenAI

    kwargs: dict[str, Any] = {"api_key": pc["api_key"]}
    if pc.get("base_url"):
        kwargs["base_url"] = pc["base_url"]

    client = OpenAI(**kwargs)
    resp = client.chat.completions.create(
        model=pc.get("model") or "gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return resp.choices[0].message.content or ""


def _call_claude(pc: dict, prompt: str) -> str:
    """Anthropic Claude — uses the anthropic SDK."""
    try:
        from anthropic import Anthropic
    except ImportError:
        raise ImportError(
            "Claude provider requires the anthropic SDK. "
            "Install with: pip install anthropic"
        )

    client = Anthropic(api_key=pc["api_key"])
    message = client.messages.create(
        model=pc.get("model") or "claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def get_embedding(cfg: dict, text: str) -> list[float]:
    """Generate an embedding vector for the given text."""
    from config import get_provider_config

    pc = get_provider_config(cfg, "embedding")

    if pc["provider"] == "local":
        return _local_embedding(text, pc.get("embedding_model", "all-MiniLM-L6-v2"))

    if pc["provider"] == "claude":
        raise ValueError(
            "Claude does not provide an Embedding API. "
            "Set llm.embedding_provider to 'openai', 'deepseek', 'qwen', 'doubao', or 'local'."
        )

    return _openai_compat_embedding(pc, text)


def _openai_compat_embedding(pc: dict, text: str) -> list[float]:
    """Embedding via any OpenAI-compatible provider."""
    from openai import OpenAI

    kwargs: dict[str, Any] = {"api_key": pc["api_key"]}
    if pc.get("base_url"):
        kwargs["base_url"] = pc["base_url"]

    client = OpenAI(**kwargs)
    model = pc.get("embedding_model") or "text-embedding-3-small"
    resp = client.embeddings.create(model=model, input=text)
    return resp.data[0].embedding


_rerank_model_cache: dict[str, Any] = {}


def rerank(query: str, documents: list[str], top_k: int = 5,
           model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> list[int]:
    """Rerank documents using a cross-encoder model. Returns indices sorted by relevance."""
    import warnings

    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        raise ImportError(
            "Reranking requires sentence-transformers with CrossEncoder support. "
            "Install with: pip install sentence-transformers"
        )

    if model_name not in _rerank_model_cache:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _rerank_model_cache[model_name] = CrossEncoder(model_name)

    model = _rerank_model_cache[model_name]
    pairs = [(query, doc) for doc in documents]
    scores = model.predict(pairs)
    ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return ranked_indices[:top_k]


_local_model_cache: dict[str, Any] = {}


def _local_embedding(text: str, model_name: str = "all-MiniLM-L6-v2") -> list[float]:
    """Use sentence-transformers for local embedding (no API key required)."""
    import warnings

    if model_name not in _local_model_cache:
        import io
        import sys
        _stderr_backup = sys.stderr
        sys.stderr = io.StringIO()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError:
                    sys.stderr = _stderr_backup
                    raise ImportError(
                        "Local embedding requires sentence-transformers. "
                        "Install with: pip install sentence-transformers"
                    )
                _local_model_cache[model_name] = SentenceTransformer(
                    model_name, device="cpu"
                )
        finally:
            sys.stderr = _stderr_backup

    model = _local_model_cache[model_name]
    embedding = model.encode(text, convert_to_numpy=True, show_progress_bar=False)
    return embedding.tolist()
