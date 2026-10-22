# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概要

個人用のAIライティングツール（Python + Streamlit + Gemini API）。DB・認証は意図的に持たない。生成結果は `st.session_state` にだけ保持され、ブラウザを閉じると消える。UI 文言・プロンプトはすべて日本語。

## コマンド

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py                     # http://localhost:8501
streamlit run app.py --server.headless true   # バックグラウンド起動時はこちら（初回のメール入力プロンプトで止まるのを防ぐ）
```

APIキーは `.env` の `GEMINI_API_KEY`（`GOOGLE_API_KEY` も可）か、未設定ならサイドバーで入力。`GEMINI_MODEL` で既定モデルを固定できる。

テストスイートやリンターは未導入。動作確認は `streamlit.testing.v1.AppTest` で `gemini_client.stream_reply` / `list_models` をモンキーパッチして行う（APIキー不要）。その際の注意:
- フォーム内のウィジェットは `.input(...)` のあと `.run()` を挟まず、送信ボタンの `.click().run()` まで一気に行う（間に `run()` を挟むと入力値が破棄される）
- メイン領域の要素が先に並ぶため、`at.button[0]` はサイドバーではなくフォーム送信ボタン。ラベルで探すほうが確実
- Windows のコンソールは cp932 なので、絵文字を print するなら `sys.stdout.reconfigure(encoding="utf-8")`

## アーキテクチャ

3ファイル構成で、ツールはデータ駆動。

- `writing_tools.py` — 各ツールを `WritingTool`（id・名前・アイコン・`instruction`・`fields`）として `TOOLS` に定義。`Field.kind` は `text` / `textarea` / `select`。`build_prompt()` が `instruction` と入力値（空欄は省略、`## ラベル` 見出し付き）を連結してユーザープロンプトにする。共通のシステムプロンプト `SYSTEM_INSTRUCTION` と、チャット用の `CHAT_SYSTEM_INSTRUCTION`、修正指示用の `REFINE_TEMPLATE` もここ。**ツールの追加は `TOOLS` に1件足すだけで、`app.py` の変更は不要。**
- `app.py` — 画面。`TOOLS` のツールはすべて `render_form_tool()`（左: フォーム、右: 結果）で、`CHAT_TOOL` だけ `render_chat()` で描画する。
- `gemini_client.py` — google-genai SDK のラッパー（Streamlit 非依存）。`stream_reply()` は `Message(role="user"|"model")` のリストを `contents` に変換してストリーミングする。

### 会話履歴と「修正指示」の仕組み

ツールごとに `st.session_state[f"history_{tool.id}"]` に `Message` のリストを持つ。フォーム送信で履歴をリセットして最初のプロンプトを入れ、「修正指示」は `REFINE_TEMPLATE` を user 発言として追記し、履歴全体を再送して多ターン会話として作り直す。表示される「以前のバージョン」は履歴中の model 発言。

`generate()` は失敗時に最後の user 発言を `pop()` して履歴を一貫した状態に戻す。成功時は `st.rerun()` で再描画し、結果表示を `render_result()` に一本化している（ストリーミング表示との二重表示を避けるため）。チャットだけは rerun しない。

### その他の前提

- フォームの各ウィジェットは `persist_state="session"`（Streamlit 1.66 の機能）付きで、キーは `field_{tool.id}_{field.key}`。ツールを切り替えても入力が残るのはこのため。
- モデル一覧は `client.models.list()` から取得し（`st.cache_data` で1時間キャッシュ）、tts・image・embedding などの派生モデルは除外する。既定モデルは `PREFERRED_MODELS` の順で選ぶ。取得できないときは `FALLBACK_MODELS` を使う。
- `temperature` はあえて指定していない（モデル既定に任せる）。
