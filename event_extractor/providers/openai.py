# event_extractor/providers/openai.py
"""OpenAI LLM provider."""

import json
import logging
from typing import Any, Dict, List

from event_extractor.providers.base import SYSTEM_PROMPT, BaseLLMProvider

log = logging.getLogger(__name__)


class OpenAIProvider(BaseLLMProvider):
    """LLM provider that calls the OpenAI API."""

    def extract_events(self, emails: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not emails:
            return []

        prompt = self._build_prompt(emails)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        content = response.choices[0].message.content or ""
        return _parse_openai_response(content)


def _parse_openai_response(content: str) -> List[Dict[str, Any]]:
    """Parse the LLM JSON response, handling markdown fences."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:]) if len(lines) > 1 else content
        if content.endswith("```"):
            content = content[:-3].strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        log.warning("Failed to parse LLM JSON response")
        return []
