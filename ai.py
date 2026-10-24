"""所有和 Claude API 有关的逻辑都放在这里，界面在 app.py。"""
import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-opus-5-5"

# 改这里就能把聊天机器人变成你们的点子（比如"你是一个帮学生规划复习的助手"）
SYSTEM_PROMPT = "你是一个乐于助人的助手。回答简洁清楚。"

_client = None


def get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # 自动读取 .env 里的 ANTHROPIC_API_KEY
    return _client


def has_api_key():
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def stream_reply(messages, system=SYSTEM_PROMPT):
    """messages 格式: [{"role": "user" 或 "assistant", "content": "..."}, ...]
    回复会一段一段 yield 出来，界面上就能看到打字效果。"""
    with get_client().beta.messages.stream(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=messages,
        # 如果 Claude 的安全检查误拒了请求，会自动换一个模型重试
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        for text in stream.text_stream:
            yield text
        final = stream.get_final_message()

    if final.stop_reason == "refusal":
        yield "\n\n（Claude 拒绝回答这个问题。）"
    elif final.stop_reason == "max_tokens":
        yield "\n\n（回复太长，被截断了。）"


def ask(prompt, system=SYSTEM_PROMPT):
    """一问一答，直接返回完整文字。适合不需要聊天界面的功能，比如"总结这段文字"。"""
    return "".join(stream_reply([{"role": "user", "content": prompt}], system))
