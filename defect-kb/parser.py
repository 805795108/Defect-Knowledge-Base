"""Robust LLM output parsing — handles markdown fences, loose JSON, etc."""

from __future__ import annotations

import json
import re
from typing import Any


class LLMParseError(Exception):
    """Raised when LLM output cannot be parsed as valid JSON."""


def parse_llm_json(raw: str) -> dict[str, Any]:
    """Extract a JSON object from raw LLM text output.

    Tries three strategies in order:
    1. Direct ``json.loads`` on trimmed input
    2. Extract content inside markdown ```json ... ``` fences
    3. Greedy brace-matching to find the outermost ``{ ... }``
    """
    text = raw.strip()

    # Strategy 1: direct parse
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Strategy 2: markdown fence extraction
    fence_match = re.search(
        r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL
    )
    if fence_match:
        try:
            obj = json.loads(fence_match.group(1).strip())
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # Strategy 3: outermost brace matching
    start = text.find("{")
    if start != -1:
        depth = 0
        end = start
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        candidate = text[start : end + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    raise LLMParseError(
        f"Failed to extract JSON object from LLM output. Raw text:\n{text[:500]}"
    )


def parse_llm_json_array(raw: str) -> list[dict[str, Any]]:
    """Extract a JSON array from raw LLM text output (used by brainstorm prompts)."""
    text = raw.strip()

    try:
        obj = json.loads(text)
        if isinstance(obj, list):
            return obj
    except json.JSONDecodeError:
        pass

    fence_match = re.search(
        r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL
    )
    if fence_match:
        try:
            obj = json.loads(fence_match.group(1).strip())
            if isinstance(obj, list):
                return obj
        except json.JSONDecodeError:
            pass

    start = text.find("[")
    if start != -1:
        depth = 0
        end = start
        for i in range(start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        candidate = text[start : end + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, list):
                return obj
        except json.JSONDecodeError:
            pass

    raise LLMParseError(
        f"Failed to extract JSON array from LLM output. Raw text:\n{text[:500]}"
    )
