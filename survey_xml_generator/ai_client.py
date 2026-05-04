"""Shared OpenAI client wrapper with retry logic."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

from openai import OpenAI

from .config import OPENAI_MODEL, OPENAI_MODEL_MINI, AI_TEMPERATURE

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None
_client_lock = threading.Lock()


def get_client() -> OpenAI:
    """Lazy-init and return the OpenAI client (thread-safe).

    Reads OPENAI_API_KEY from os.environ at call time so that keys
    set after import (e.g. via Streamlit sidebar or st.secrets) work.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                api_key = os.environ.get("OPENAI_API_KEY", "")
                if not api_key:
                    raise ValueError(
                        "OPENAI_API_KEY not set. Add it to .env or enter it in the sidebar."
                    )
                _client = OpenAI(api_key=api_key)
    return _client


def reset_client() -> None:
    """Invalidate the cached client so the next call to get_client() rebuilds it."""
    global _client
    with _client_lock:
        _client = None


def call_ai(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_retries: int = 3,
    expect_json: bool = True,
) -> str | dict | list:
    """Call the OpenAI API and return the response.

    If expect_json is True, parses the response as JSON and retries on
    parse failure.
    """
    client = get_client()
    model = model or OPENAI_MODEL
    temperature = temperature if temperature is not None else AI_TEMPERATURE

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"AI call attempt {attempt}/{max_retries} (model={model})")

            kwargs = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 16000,
            }

            # Use JSON response format for models that support it
            if expect_json:
                kwargs["response_format"] = {"type": "json_object"}

            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content.strip()

            if expect_json:
                # Clean up potential markdown fences
                if content.startswith("```"):
                    # Remove ```json ... ``` wrapping
                    lines = content.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    content = "\n".join(lines)

                parsed = json.loads(content)
                logger.info(
                    f"AI response parsed successfully "
                    f"(tokens: {response.usage.total_tokens if response.usage else '?'})"
                )
                return parsed

            return content

        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error on attempt {attempt}: {e}")
            if attempt == max_retries:
                logger.error(f"Failed to parse JSON after {max_retries} attempts")
                raise
            time.sleep(1)

        except Exception as e:
            logger.warning(f"API error on attempt {attempt}: {e}")
            if attempt == max_retries:
                raise
            time.sleep(2 * attempt)  # Exponential backoff

    raise RuntimeError("Unreachable")
