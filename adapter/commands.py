"""Routing of chat messages to the generation handler.

AstrBot's ``@filter.command`` collapses whitespace and splits on spaces,
which would mangle quoted prompts, and it cannot register commands that
only exist at runtime (each workflow's ``command`` key). So the plugin keeps
its own :class:`CommandTable` and matches the first word of the message
with a :class:`CustomFilter`; the handler then parses ``event.message_str``
itself with the argparse CLI from ``comfyui_core``.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from astrbot.api.event import AstrMessageEvent
from astrbot.api.event.filter import CustomFilter

EXTRA_KEY = "yac_command"
"""``event.get_extra(EXTRA_KEY)`` holds the matched command word."""

# Words the plugin's own fixed commands use; a workflow cannot take them over.
RESERVED = {
    "comfyui帮助", "comfy帮助", "comfyhelp", "查看工作流", "后端", "comfyui后端", "queue", "capi",
    "二次元的", "二次元的我", "dan", "查tag", "llm-tag", "get-ckpt", "get-loras", "get-lora", "get-task", "comfy",
}

logger = logging.getLogger("astrbot")


class CommandTable:
    """Maps a command word to the workflow it runs (``None`` = default)."""

    def __init__(self) -> None:
        self._table: dict[str, str | None] = {}

    def refresh(self, trigger_words: Iterable[str], workflows: dict[str, dict[str, Any]]) -> None:
        table: dict[str, str | None] = {"prompt": None}
        for word in trigger_words:
            if word:
                table[word] = None
        for name, reflex in workflows.items():
            cmd = reflex.get("command")
            words = cmd if isinstance(cmd, list) else [cmd]
            for word in words:
                if not isinstance(word, str) or not word or word in table:
                    continue
                if word in RESERVED:
                    logger.warning("Workflow %s: command %s is taken by a built-in command, skipped", name, word)
                    continue
                table[word] = name
        self._table = table

    def words(self) -> list[str]:
        return list(self._table)

    def match(self, message_str: str) -> str | None:
        """Return the command word the message starts with, if any."""
        text = message_str.strip()
        if not text:
            return None
        head = text.split(maxsplit=1)[0]
        return head if head in self._table else None

    def workflow_commands(self) -> list[tuple[str, str]]:
        """``("超分 / upscale", "超分")`` for each workflow that registered commands."""
        grouped: dict[str, list[str]] = {}
        for word, wf in self._table.items():
            if wf:
                grouped.setdefault(wf, []).append(word)
        return [(" / ".join(words), wf) for wf, words in grouped.items()]

    def workflow_for(self, word: str) -> str | None:
        return self._table.get(word)

    def strip(self, word: str, message_str: str) -> str:
        text = message_str.strip()
        return text[len(word):].strip() if text.startswith(word) else text


COMMANDS = CommandTable()


class GenerateCommandFilter(CustomFilter):
    """Passes when the message starts with a known generation command word."""

    def filter(self, event: AstrMessageEvent, cfg: Any) -> bool:
        if not getattr(event, "is_at_or_wake_command", False):
            return False
        word = COMMANDS.match(event.message_str)
        if word is None:
            return False
        event.set_extra(EXTRA_KEY, word)
        return True
