"""LoRA tag parsing and LoraLoader node injection.

Parsing and graph rewriting come from ``comfyui_reflex``; matching the
requested names against a backend's LoRA list stays here.
"""
from __future__ import annotations

import logging

from comfyui_reflex.lora import (
    LORA_NODE_TEMPLATE,
    apply_loras_to_api_json,
    extract_loras_from_prompt,
)

logger = logging.getLogger(__name__)

__all__ = ["LORA_NODE_TEMPLATE", "apply_loras_to_api_json", "extract_loras_from_prompt", "match_loras"]


def match_loras(
    parsed_loras: list[tuple[str, float]],
    available: list[str],
) -> list[tuple[str, float]]:
    """Fuzzy-match requested LoRA names against the backend's file list."""
    matched: list[tuple[str, float]] = []
    for lora_name, weight in parsed_loras:
        for real_lora in available:
            if lora_name in real_lora:
                logger.info("LoRA matched: %r -> %r (weight %s)", lora_name, real_lora, weight)
                matched.append((real_lora, weight))
                break
        else:
            logger.warning("No LoRA on the backend matches %r", lora_name)
    return matched
