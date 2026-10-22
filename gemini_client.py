"""Gemini API の呼び出しをまとめたモジュール（Streamlit には依存しない）。"""

from collections.abc import Iterator
from dataclasses import dataclass

from google import genai
from google.genai import errors, types

# モデル一覧を取得できなかったときに使う候補
FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"]
# 一覧に含まれていれば、この順で既定モデルに選ぶ
PREFERRED_MODELS = ["gemini-3.5-flash", "gemini-3-flash-preview", "gemini-2.5-flash"]
# 文章生成に向かない派生モデルは一覧から除外する
_EXCLUDED_KEYWORDS = ("tts", "image", "embedding", "live", "audio", "robotics", "computer-use")


@dataclass
class Message:
    role: str  # "user" または "model"
    text: str


def list_models(api_key: str) -> list[str]:
    """APIキーで利用できる、文章生成向けの Gemini モデル名を返す。"""
    client = genai.Client(api_key=api_key)
    names = set()
    for model in client.models.list():
        name = (model.name or "").removeprefix("models/")
        if not name.startswith("gemini"):
            continue
        if "generateContent" not in (model.supported_actions or []):
            continue
        if any(keyword in name for keyword in _EXCLUDED_KEYWORDS):
            continue
        names.add(name)
    return sorted(names, reverse=True)


def pick_default_model(models: list[str], preferred: str | None = None) -> str:
    if preferred:
        return preferred
    for name in PREFERRED_MODELS:
        if name in models:
            return name
    return models[0]


def stream_reply(
    api_key: str, model: str, system_instruction: str, messages: list[Message]
) -> Iterator[str]:
    """会話履歴を渡して、モデルの返答をテキスト片ごとに返す。"""
    client = genai.Client(api_key=api_key)
    contents = [
        types.Content(role=m.role, parts=[types.Part(text=m.text)]) for m in messages
    ]
    config = types.GenerateContentConfig(system_instruction=system_instruction)
    for chunk in client.models.generate_content_stream(
        model=model, contents=contents, config=config
    ):
        if chunk.text:
            yield chunk.text


def describe_error(error: errors.APIError) -> str:
    """API エラーを、画面に表示するための日本語メッセージにする。"""
    detail = error.message or str(error)
    if error.code in (400, 401, 403) and "key" in detail.lower():
        return f"APIキーが無効か、権限がありません。設定を確認してください。（{detail}）"
    if error.code == 404:
        return f"モデルが見つかりません。サイドバーで別のモデルを選んでください。（{detail}）"
    if error.code == 429:
        return f"利用上限（レート制限）に達しました。少し待ってから再度お試しください。（{detail}）"
    if isinstance(error, errors.ServerError):
        return f"Gemini 側で一時的なエラーが発生しました。再度お試しください。（{detail}）"
    return f"エラーが発生しました（{error.code}）: {detail}"
