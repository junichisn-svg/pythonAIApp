"""AIライティングツール（Streamlit + Gemini API）。

起動: streamlit run app.py
"""

import os

import streamlit as st
from dotenv import load_dotenv
from google.genai import errors

import gemini_client
from gemini_client import Message
from writing_tools import (
    CHAT_SYSTEM_INSTRUCTION,
    CHAT_TOOL,
    REFINE_TEMPLATE,
    SYSTEM_INSTRUCTION,
    TOOLS,
    Field,
    WritingTool,
    build_prompt,
)

load_dotenv()

st.set_page_config(page_title="AIライティングツール", page_icon="✍️", layout="wide")

ALL_TOOLS = {tool.id: tool for tool in [*TOOLS, CHAT_TOOL]}


@st.cache_data(ttl=3600, show_spinner=False)
def available_models(api_key: str) -> list[str]:
    try:
        return gemini_client.list_models(api_key) or gemini_client.FALLBACK_MODELS
    except Exception:
        return gemini_client.FALLBACK_MODELS


def history_for(tool: WritingTool) -> list[Message]:
    return st.session_state.setdefault(f"history_{tool.id}", [])


def render_sidebar() -> tuple[WritingTool, str, str]:
    with st.sidebar:
        st.title("✍️ AIライティング")
        tool_id = st.radio(
            "ツール",
            list(ALL_TOOLS),
            format_func=lambda i: f"{ALL_TOOLS[i].icon} {ALL_TOOLS[i].name}",
            key="tool_id",
        )
        tool = ALL_TOOLS[tool_id]

        st.divider()
        st.subheader("設定")
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
        if not api_key:
            api_key = st.text_input(
                "Gemini APIキー",
                type="password",
                help=".env に GEMINI_API_KEY を書いておくと、毎回の入力が不要になります。",
            )

        models = available_models(api_key) if api_key else gemini_client.FALLBACK_MODELS
        default_model = gemini_client.pick_default_model(models, os.getenv("GEMINI_MODEL"))
        if default_model not in models:
            models = [default_model, *models]
        model = st.selectbox("モデル", models, index=models.index(default_model))

        if st.button("この画面の結果をクリア", width="stretch"):
            history_for(tool).clear()
            st.rerun()

    return tool, api_key, model


def render_field(tool: WritingTool, f: Field) -> str:
    label = f"{f.label} *" if f.required else f.label
    key = f"field_{tool.id}_{f.key}"
    if f.kind == "select":
        return st.selectbox(label, f.options, key=key, persist_state="session")
    if f.kind == "textarea":
        return st.text_area(
            label, placeholder=f.placeholder, height=f.height, key=key, persist_state="session"
        )
    return st.text_input(label, placeholder=f.placeholder, key=key, persist_state="session")


def generate(api_key: str, model: str, system_instruction: str, history: list[Message]) -> bool:
    """履歴の最後のユーザー発言に対する返答を表示しながら生成し、履歴に追加する。

    失敗した場合は、最後のユーザー発言を履歴から取り除く。
    """
    if not api_key:
        st.error("Gemini APIキーが設定されていません。サイドバーで入力するか、.env に設定してください。")
        history.pop()
        return False
    try:
        text = st.write_stream(
            gemini_client.stream_reply(api_key, model, system_instruction, history)
        )
    except errors.APIError as e:
        st.error(gemini_client.describe_error(e))
        history.pop()
        return False
    except Exception as e:
        st.error(f"通信中にエラーが発生しました: {e}")
        history.pop()
        return False
    if not isinstance(text, str) or not text.strip():
        st.error("応答が空でした。入力内容を見直すか、別のモデルでお試しください。")
        history.pop()
        return False
    history.append(Message("model", text))
    return True


def render_result(tool: WritingTool, outputs: list[str]) -> None:
    latest = outputs[-1]
    with st.container(border=True):
        st.markdown(latest)
    st.caption(f"{len(latest):,} 文字")

    with st.expander("コピー用テキスト"):
        st.code(latest, language=None, wrap_lines=True)
    st.download_button(
        "Markdown でダウンロード",
        latest,
        file_name=f"{tool.id}.md",
        mime="text/markdown",
        icon=":material/download:",
    )

    if len(outputs) > 1:
        with st.expander(f"以前のバージョン（{len(outputs) - 1}件）"):
            for number, text in reversed(list(enumerate(outputs[:-1], start=1))):
                st.markdown(f"**バージョン {number}**")
                st.code(text, language=None, wrap_lines=True)


def render_form_tool(tool: WritingTool, api_key: str, model: str) -> None:
    st.header(f"{tool.icon} {tool.name}")
    st.caption(tool.description)

    history = history_for(tool)
    left, right = st.columns([2, 3], gap="large")

    with left:
        with st.form(f"form_{tool.id}"):
            values = {f.key: render_field(tool, f) for f in tool.fields}
            submitted = st.form_submit_button("生成する", type="primary", width="stretch")

    with right:
        st.subheader("結果")
        if submitted:
            missing = [f.label for f in tool.fields if f.required and not (values[f.key] or "").strip()]
            if missing:
                st.warning(f"必須項目を入力してください: {'、'.join(missing)}")
            else:
                history.clear()
                history.append(Message("user", build_prompt(tool, values)))
                with st.container(border=True):
                    if generate(api_key, model, SYSTEM_INSTRUCTION, history):
                        st.rerun()

        outputs = [m.text for m in history if m.role == "model"]
        if not outputs:
            if not submitted:
                st.info("左のフォームに入力して「生成する」を押してください。")
            return

        render_result(tool, outputs)

        st.divider()
        with st.form(f"refine_{tool.id}", clear_on_submit=True):
            instruction = st.text_area(
                "修正の指示",
                placeholder="例: もっと短く / 結論を先に / もう少しカジュアルに",
                height=80,
            )
            refine = st.form_submit_button("この指示で修正する")
        if refine and instruction.strip():
            history.append(Message("user", REFINE_TEMPLATE.format(instruction=instruction.strip())))
            with st.container(border=True):
                if generate(api_key, model, SYSTEM_INSTRUCTION, history):
                    st.rerun()


def render_chat(api_key: str, model: str) -> None:
    st.header(f"{CHAT_TOOL.icon} {CHAT_TOOL.name}")
    st.caption(CHAT_TOOL.description)

    history = history_for(CHAT_TOOL)
    for message in history:
        with st.chat_message("user" if message.role == "user" else "assistant"):
            st.markdown(message.text)

    if prompt := st.chat_input("メッセージを入力"):
        history.append(Message("user", prompt))
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            generate(api_key, model, CHAT_SYSTEM_INSTRUCTION, history)


def main() -> None:
    tool, api_key, model = render_sidebar()
    if tool is CHAT_TOOL:
        render_chat(api_key, model)
    else:
        render_form_tool(tool, api_key, model)


main()
