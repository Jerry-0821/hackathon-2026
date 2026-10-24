"""All Claude API logic lives here. The UI is in app.py."""
import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-opus-5-5"

# Change this to turn the chatbot into your idea (e.g. "You help students plan their revision.")
SYSTEM_PROMPT = "You are a helpful assistant. Keep answers short and clear."

_client = None


def get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from .env
    return _client


def has_api_key():
    key = os.getenv("ANTHROPIC_API_KEY", "")
    return key.startswith("sk-ant-") and len(key) > 20  # rejects the empty placeholder


def stream_reply(messages, system=SYSTEM_PROMPT):
    """messages format: [{"role": "user" or "assistant", "content": "..."}, ...]
    Yields the reply chunk by chunk, so the UI shows a typing effect."""
    with get_client().beta.messages.stream(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=messages,
        # If Claude's safety check wrongly refuses a request, retry automatically on another model
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        for text in stream.text_stream:
            yield text
        final = stream.get_final_message()

    if final.stop_reason == "refusal":
        yield "\n\n(Claude declined to answer this.)"
    elif final.stop_reason == "max_tokens":
        yield "\n\n(The reply was too long and got cut off.)"


def ask(prompt, system=SYSTEM_PROMPT):
    """One question, one full answer. Good for features without a chat UI, like "summarize this text"."""
    return "".join(stream_reply([{"role": "user", "content": prompt}], system))
