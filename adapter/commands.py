"""Routing of chat messages to the generation handler.

AstrBot's ``@filter.command`` collapses whitespace and splits on spaces,
which would mangle quoted prompts, and it cannot register commands that
only exist at runtime (each workflow's ``command`` key). So the plugin keeps
its own :class:`CommandTable` and matches the first word of the message
with a :class:`CustomFilter`; the handler then parses ``event.message_str``
itself with the argparse CLI from ``comfyui_core``.
"""
from __future__ import annotations

from typing import Any, Iterable

from astrbot.api.event import AstrMessageEvent
from astrbot.api.event.filter import CustomFilter

EXTRA_KEY = "yac_command"
"""``event.get_extra(EXTRA_KEY)`` holds the matched command word."""


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
                if isinstance(word, str) and word and word not in table:
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
