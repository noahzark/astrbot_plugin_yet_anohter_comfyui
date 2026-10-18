"""Prompt text audit through an OpenAI-compatible chat endpoint."""
from __future__ import annotations

import logging
import re
import string

from ..client import ComfyClient
from ..settings import CoreSettings

logger = logging.getLogger(__name__)


def clean_llm_response(text: str) -> str:
    """Strip ``<think>...</think>`` blocks from a reasoning model's reply."""
    return re.sub(r"<?think>.*?</think>", "", text, flags=re.DOTALL).strip()


def remove_punctuation(text: str) -> str:
    for i in range(len(text)):
        if text[i] not in string.punctuation:
            return text[i:]
    return ""


class TextAuditor:
    def __init__(self, settings: CoreSettings, client: ComfyClient) -> None:
        self.settings = settings
        self.client = client

    def default_prompt(self) -> str:
        ban_words_str = ",".join(self.settings.text_audit.ban_words)
        return f"""接下来请你对一些聊天内容进行审核,
如果内容出现中国相关的任何内容/政治/暴力/恐怖袭击/血腥以及{ban_words_str}相关内容（特别是中国的政治人物/或者和中国相关的政治）则请你输出yes,
如果没有则输出no,最后， 只输出yes或者no即可，不需要你输出其他内容"""

    async def audit(self, msg: str, custom_prompt: str | None = None) -> str:
        """Returns the model's lowercased verdict (``yes``/``no`` or a rewrite).

        Disabled audit answers ``no``; a failed request answers ``yes``.
        """
        if not self.settings.text_audit.enabled:
            return "no"

        openai_cfg = self.settings.text_audit.openai
        payload = {
            "model": openai_cfg.get("params", {}).get("model", "gpt-3.5-turbo"),
            "messages": [
                {"role": "system", "content": custom_prompt or self.default_prompt()},
                {"role": "user", "content": msg},
            ],
            "max_tokens": 4000,
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {openai_cfg.get('token', '')}"}
        try:
            resp = await self.client.request(
                "POST", f"{openai_cfg['endpoint']}/chat/completions",
                headers=headers, json_data=payload, proxy=True, timeout=10)
            content = resp["choices"][0]["message"]["content"].strip()
            cleaned = clean_llm_response(remove_punctuation(content)).lower()
            logger.info("Text audit: input=%r verdict=%r", msg[:50], cleaned)
            return cleaned
        except Exception as e:
            logger.error("Text audit request failed: %s", e)
            return "yes"
