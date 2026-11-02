"""Info cards: render a template through AstrBot's html_render, else send text."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable

import astrbot.api.message_components as Comp
from astrbot.api import logger

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

Render = Callable[[str, dict], Awaitable[str]]


@lru_cache(maxsize=None)
def template(name: str) -> str:
    return (TEMPLATE_DIR / f"{name}.html").read_text(encoding="utf-8")


def rendered_image(ref: str) -> Comp.Image:
    """A rendered image: a URL from the t2i service, or a local file path
    when AstrBot fell back to rendering it locally."""
    return Comp.Image.fromURL(ref) if ref.startswith("http") else Comp.Image.fromFileSystem(ref)


async def card(render: Render | None, name: str, data: dict[str, Any], fallback: str) -> list[Comp.BaseMessageComponent]:
    """The card image for ``data``, or ``fallback`` text when rendering is off or fails."""
    if render is not None:
        try:
            url = await render(template(name), data)
            if url:
                return [rendered_image(url)]
        except Exception as e:
            logger.warning("Rendering the %s card failed, sending text instead: %s", name, e)
    return [Comp.Plain(fallback)]
