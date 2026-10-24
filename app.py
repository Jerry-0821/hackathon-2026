import anthropic
import streamlit as st

import ai

st.set_page_config(page_title="Hackathon Demo", page_icon="🚀")
st.title("🚀 Hackathon Demo")
st.caption("Change SYSTEM_PROMPT in ai.py to turn this into your idea")

if not ai.has_api_key():
    st.error("No API key found: copy .env.example to .env, put your key in it, then restart.")
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    if st.button("Clear chat"):
        st.session_state.messages = []
        st.rerun()

# Show the conversation so far
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Ask something..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            reply = st.write_stream(ai.stream_reply(st.session_state.messages))
        except anthropic.AuthenticationError:
            st.error("Invalid API key. Check your .env file.")
            st.stop()
        except anthropic.RateLimitError:
            st.error("Too many requests or out of credits. Wait a bit and try again.")
            st.stop()
        except anthropic.APIConnectionError:
            st.error("Can't reach Claude. Check your internet connection.")
            st.stop()
        except anthropic.APIStatusError as e:
            st.error(f"API error: {e.message}")
            st.stop()

    st.session_state.messages.append({"role": "assistant", "content": reply})
