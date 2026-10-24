import anthropic
import streamlit as st

import ai

st.set_page_config(page_title="Hackathon Demo", page_icon="🚀")
st.title("🚀 Hackathon Demo")
st.caption("改 ai.py 里的 SYSTEM_PROMPT，就能把它变成你们的点子")

if not ai.has_api_key():
    st.error("找不到 API key：把 .env.example 复制成 .env，填入你的 key，然后重新运行。")
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    if st.button("清空对话"):
        st.session_state.messages = []
        st.rerun()

# 显示之前的对话
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("输入你的问题..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            reply = st.write_stream(ai.stream_reply(st.session_state.messages))
        except anthropic.AuthenticationError:
            st.error("API key 无效，检查一下 .env。")
            st.stop()
        except anthropic.RateLimitError:
            st.error("请求太频繁或额度用完了，等一下再试。")
            st.stop()
        except anthropic.APIConnectionError:
            st.error("连不上 Claude，检查网络。")
            st.stop()
        except anthropic.APIStatusError as e:
            st.error(f"API 出错：{e.message}")
            st.stop()

    st.session_state.messages.append({"role": "assistant", "content": reply})
