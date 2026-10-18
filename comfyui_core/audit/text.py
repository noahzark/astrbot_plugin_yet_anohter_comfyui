"""Prompt text audit through the adapter's LLM or an OpenAI-compatible endpoint."""
from __future__ import annotations

import logging
import re
import string
from typing import Awaitable, Callable

from ..client import ComfyClient
from ..exceptions import AuditError
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


LLMCall = Callable[[str, str], Awaitable[str]]
"""``(system_prompt, user_text) -> reply``: how an adapter lends its own LLM."""

REWRITE_PROMPT = (
    "接下来请你对一些聊天内容进行审核,如果内容出现色情,血腥内容,请你将其去掉,只需要回复调整过的内容,不要回复其他内容"
)


class TextAuditor:
    """Checks or rewrites a prompt with an LLM.

    ``llm`` is the adapter's model (the AstrBot provider). Without one, or
    when the settings name a custom endpoint, the OpenAI-compatible endpoint
    in ``text_audit.openai`` is called instead.
    """

    def __init__(self, settings: CoreSettings, client: ComfyClient, llm: LLMCall | None = None) -> None:
        self.settings = settings
        self.client = client
        self.llm = llm

    def default_prompt(self) -> str:
        ban_words_str = ",".join(self.settings.text_audit.ban_words)
        return f"""接下来请你对一些聊天内容进行审核,
如果内容出现中国相关的任何内容/政治/暴力/恐怖袭击/血腥以及{ban_words_str}相关内容（特别是中国的政治人物/或者和中国相关的政治）则请你输出yes,
如果没有则输出no,最后， 只输出yes或者no即可，不需要你输出其他内容"""

    def mode_for(self, group_id: str) -> int:
        """0 off, 1 block unsafe prompts, 2 rewrite them; groups may override."""
        groups = self.settings.group_config.get("reject_nsfw_prompts", {})
        if group_id and group_id in groups:
            try:
                return int(groups[group_id])
            except (TypeError, ValueError):
                pass
        return 1 if self.settings.text_audit.enabled else 0

    async def ask(self, system_prompt: str, msg: str) -> str:
        """One LLM round trip; raises :class:`AuditError` when it fails."""
        try:
            if self.llm is not None and not self.settings.text_audit.use_endpoint:
                content = await self.llm(system_prompt, msg)
            else:
                content = await self._ask_endpoint(system_prompt, msg)
        except AuditError:
            raise
        except Exception as e:
            raise AuditError(f"文本审核请求失败: {e}")
        cleaned = clean_llm_response(str(content or "").strip())
        if not cleaned:
            raise AuditError("文本审核没有返回内容")
        return cleaned

    async def _ask_endpoint(self, system_prompt: str, msg: str) -> str:
        openai_cfg = self.settings.text_audit.openai
        payload = {
            "model": openai_cfg.get("params", {}).get("model", "gpt-3.5-turbo"),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": msg},
            ],
            "max_tokens": 4000,
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {openai_cfg.get('token', '')}"}
        resp = await self.client.request(
            "POST", f"{str(openai_cfg['endpoint']).rstrip('/')}/chat/completions",
            headers=headers, json_data=payload, proxy=True, timeout=30)
        return resp["choices"][0]["message"]["content"]

    async def is_unsafe(self, msg: str) -> bool:
        """``True`` when the model answers yes; a failed check counts as unsafe."""
        try:
            verdict = remove_punctuation(await self.ask(self.default_prompt(), msg)).lower()
        except AuditError as e:
            logger.error("Text audit failed, blocking the prompt: %s", e)
            return True
        logger.info("Text audit: input=%r verdict=%r", msg[:50], verdict[:20])
        return "yes" in verdict

    async def rewrite(self, msg: str) -> str:
        """The prompt with unsafe parts removed. Raises :class:`AuditError`."""
        return await self.ask(REWRITE_PROMPT, msg)
