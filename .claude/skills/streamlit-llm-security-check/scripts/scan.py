#!/usr/bin/env python3
"""Streamlit + LLM アプリで、セキュリティ上確認すべき箇所を洗い出す補助スキャナー。

正規表現で候補を拾うだけなので、結果は「確認すべき箇所」であって脆弱性の確定ではない。
シークレットらしき文字列は伏せ字で表示する。標準ライブラリだけで動く。

使い方: python scan.py [対象ディレクトリ]   （省略時はカレントディレクトリ）
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", ".env.d", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "site-packages", "dist", "build",
}
MAX_FILE_BYTES = 1_000_000
MAX_HITS_PER_RULE = 30
DEPLOY_SUFFIXES = {".toml", ".yaml", ".yml", ".sh", ".ps1", ".bat", ".cmd", ".cfg", ".ini", ".json"}
DEPLOY_NAMES = {"Dockerfile", "Procfile", "Makefile", "docker-compose.yml", "compose.yaml"}
# スキルを診断対象のプロジェクト内に置いたとき、スキャナー自身のパターン定義を拾わないように除外する
SKILL_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    pattern: re.Pattern[str]
    note: str
    scope: str  # "py" | "deploy" | "any"
    secret: bool = False


def rule(id: str, category: str, pattern: str, note: str, scope: str = "py", secret: bool = False) -> Rule:
    return Rule(id, category, re.compile(pattern), note, scope, secret)


RULES = [
    # 1. シークレット
    rule("secret-google", "1. シークレット管理", r"AIza[0-9A-Za-z_\-]{35}", "Google API キーらしき文字列", "any", True),
    rule("secret-anthropic", "1. シークレット管理", r"sk-ant-[A-Za-z0-9_\-]{20,}", "Anthropic API キーらしき文字列", "any", True),
    rule("secret-openai", "1. シークレット管理", r"sk-(?!ant-)(?:proj-)?[A-Za-z0-9_\-]{20,}", "OpenAI 形式の API キーらしき文字列", "any", True),
    rule(
        "secret-assign", "1. シークレット管理",
        r"(?i)\b\w*(?:api_?key|secret|token|passw(?:or)?d)\w*\b\s*[:=]\s*[\"'](?P<val>[A-Za-z0-9_\-./+]{16,})[\"']",
        "シークレットらしき値の直書き", "py", True,
    ),
    rule("key-input", "1. シークレット管理", r"type\s*=\s*[\"']password[\"']", "パスワード入力欄（APIキー入力？値の扱いを確認）"),
    # 3. 出力の扱い
    rule("unsafe-html", "3. LLM 出力の扱い", r"unsafe_allow_html\s*=\s*True", "HTML をそのまま描画。外部由来の文字列が届くか確認"),
    rule("st-html", "3. LLM 出力の扱い", r"\bst\.html\(", "st.html で HTML を挿入。外部由来の文字列が届くか確認"),
    rule("components-html", "3. LLM 出力の扱い", r"\bcomponents(?:\.v1)?\.(?:html|iframe)\(", "iframe 内で JS が動く。外部由来の文字列が届くか確認"),
    rule(
        "markdown-sink", "3. LLM 出力の扱い", r"\bst\.(?:markdown|write|write_stream)\(",
        "Markdown として描画（画像 Markdown による外部送信の確認用。LLM 出力が届くものだけ見る）",
    ),
    # 4. コード実行
    rule("eval-exec", "4. コード実行・危険な関数", r"(?<![\w.])(?:eval|exec)\(", "eval/exec。外部の値が届くか確認"),
    rule("shell", "4. コード実行・危険な関数", r"\bsubprocess\.|\bos\.(?:system|popen)\(|shell\s*=\s*True", "コマンド実行"),
    rule(
        "deserialize", "4. コード実行・危険な関数",
        r"\bpickle\.loads?\(|\bjoblib\.load\(|\btorch\.load\(|\bpd\.read_pickle\(|\byaml\.load\(",
        "安全でないデシリアライズの可能性（yaml.load は SafeLoader なら可）",
    ),
    rule(
        "code-agent", "4. コード実行・危険な関数",
        r"PythonREPL|PythonAstREPLTool|allow_dangerous_code|create_pandas_dataframe_agent|create_sql_agent",
        "コードや SQL を実行するエージェント",
    ),
    # 5. 入力・外部取得
    rule("file-upload", "5. ファイルアップロードと外部データ取得", r"\bst\.file_uploader\(", "アップロード（type 制限・保存時のファイル名を確認）"),
    rule("query-params", "5. ファイルアップロードと外部データ取得", r"\bst\.query_params\b|experimental_get_query_params", "URL パラメータ（攻撃者が操作できる入力）"),
    rule(
        "http-fetch", "5. ファイルアップロードと外部データ取得",
        r"\brequests\.(?:get|post|put|head|request)\(|\bhttpx\.|\burllib\.request\b|\burlopen\(|\baiohttp\.|WebBaseLoader|UnstructuredURLLoader",
        "外部への HTTP リクエスト（URL がユーザーや LLM 由来なら SSRF）",
    ),
    # 6. セッション分離
    rule(
        "cache", "6. セッション分離とキャッシュ",
        r"\bst\.cache_(?:resource|data)\b|@st\.cache\b|experimental_(?:singleton|memo)",
        "キャッシュは全セッションで共有。ユーザー固有データを入れていないか確認",
    ),
    rule("global-stmt", "6. セッション分離とキャッシュ", r"^\s+global\s+\w+", "global 変数の書き換え（セッション間で共有される可能性）"),
    # 7. 認証
    rule("auth", "7. 認証・アクセス制御と公開設定", r"\bst\.(?:login|logout)\(|\bst\.user\b|experimental_user|streamlit_authenticator", "認証の実装"),
    # 9. エラー・ログ
    rule("show-exception", "9. エラー表示とログ", r"\bst\.exception\(|traceback\.(?:format_exc|print_exc)", "例外の詳細を表示・出力"),
    rule(
        "error-message", "9. エラー表示とログ",
        r"\bst\.(?:error|warning|write|code|text)\(.*\{(?:e|err|error|exc|ex|exception)\}|\bst\.(?:error|warning|write)\(\s*(?:str\()?(?:e|err|exc|ex)\)?\s*\)",
        "例外メッセージを画面に表示（キーや URL が含まれ得る）",
    ),
    rule(
        "log-sensitive", "9. エラー表示とログ",
        r"(?i)\b(?:print|logging\.\w+|logger\.\w+)\(.*\b(?:api_?key|password|token|secret|prompt|messages)\b",
        "機密情報やプロンプトをログ出力している可能性",
    ),
    # 8 / 10 / 11. LLM の設定
    rule(
        "llm-sdk", "LLM の利用箇所（参考）",
        r"^\s*(?:from|import)\s+(?:openai|anthropic|google\.genai|google\.generativeai|google import genai|langchain\w*|litellm|ollama|mistralai|cohere|groq|llama_index)\b",
        "LLM SDK の import",
    ),
    rule("token-cap", "8. コストとリソース消費", r"\b(?:max_tokens|max_output_tokens|max_completion_tokens|maxOutputTokens)\b", "出力トークン上限の指定"),
    rule("max-chars", "8. コストとリソース消費", r"\bmax_chars\s*=", "入力文字数の上限"),
    rule(
        "system-prompt", "10. システムプロンプトの扱い",
        r"\bsystem_instruction\b|\bsystem_prompt\b|[\"']role[\"']\s*:\s*[\"']system[\"']|role\s*=\s*[\"']system[\"']|\bSystemMessage\(",
        "システムプロンプト（秘密情報・ユーザー入力の埋め込みがないか確認）",
    ),
    rule(
        "tools", "11. ツール実行・エージェントの権限",
        r"\btools\s*=|function_declarations|tool_choice|\.bind_tools\(|AgentExecutor|create_\w*agent\(|@tool\b",
        "ツール / Function calling（権限と確認の有無を確認）",
    ),
    rule("safety-off", "13. プライバシーと出力の信頼性", r"BLOCK_NONE|HarmBlockThreshold\.OFF|safety_settings", "安全フィルタの設定"),
    # 7. 公開設定（設定ファイル・デプロイ定義）
    rule("bind-all", "7. 認証・アクセス制御と公開設定", r"0\.0\.0\.0", "全インターフェースで待ち受け（外部からアクセス可能）", "deploy"),
    rule("xsrf-off", "7. 認証・アクセス制御と公開設定", r"(?i)enableXsrfProtection[\"'\s=:]*false", "XSRF 保護を無効化", "deploy"),
    rule("cors-off", "7. 認証・アクセス制御と公開設定", r"(?i)enableCORS[\"'\s=:]*false", "CORS 保護を無効化", "deploy"),
    rule("static-serving", "7. 認証・アクセス制御と公開設定", r"(?i)enableStaticServing[\"'\s=:]*true", "static/ を公開配信", "deploy"),
    rule("error-details", "9. エラー表示とログ", r"(?i)showErrorDetails", "エラー詳細の表示設定", "deploy"),
    rule("upload-size", "5. ファイルアップロードと外部データ取得", r"(?i)maxUploadSize", "アップロードサイズ上限の設定", "deploy"),
]


def mask(text: str) -> str:
    return text[:6] + "…(伏せ字)" if len(text) > 6 else "…(伏せ字)"


def mask_match(m: re.Match[str]) -> str:
    """値の部分（名前付きグループ val があればそこだけ）を伏せ字にする。"""
    if "val" not in m.re.groupindex:
        return mask(m.group(0))
    start, end = m.span("val")
    base = m.start()
    whole = m.group(0)
    return whole[: start - base] + mask(m.group("val")) + whole[end - base :]


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        if path.resolve().is_relative_to(SKILL_DIR):
            continue
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        yield path, text


def scope_matches(scope: str, path: Path) -> bool:
    if scope == "any":
        return True
    if scope == "py":
        return path.suffix == ".py"
    return path.name in DEPLOY_NAMES or path.suffix in DEPLOY_SUFFIXES


def scan(root: Path):
    hits: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    module_globals: list[tuple[str, int, str]] = []
    scanned = 0
    for path, text in iter_files(root):
        scanned += 1
        rel = path.relative_to(root).as_posix()
        # streamlit run されるスクリプト（トップレベル変数は再実行ごとに作り直される）以外のモジュールか
        is_shared_module = (
            path.suffix == ".py"
            and "pages" not in path.relative_to(root).parts
            and "set_page_config" not in text
            and path.name != "streamlit_app.py"
        )
        for lineno, line in enumerate(text.splitlines(), start=1):
            for r in RULES:
                if not scope_matches(r.scope, path):
                    continue
                m = r.pattern.search(line)
                if not m:
                    continue
                shown = line.strip()
                if r.secret:
                    shown = r.pattern.sub(mask_match, shown)
                hits[r.id].append((rel, lineno, shown[:160]))
            if is_shared_module and re.match(r"^[A-Za-z_]\w*\s*(?::[^=]+)?=\s*(?:\[\]|\{\}|list\(\)|dict\(\)|set\(\))\s*$", line):
                module_globals.append((rel, lineno, line.strip()))
    return scanned, hits, module_globals


def gitignore_status(root: Path) -> list[str]:
    notes = []
    gi = root / ".gitignore"
    lines = gi.read_text(encoding="utf-8", errors="ignore").splitlines() if gi.exists() else []
    patterns = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
    env_ignored = any(p.lstrip("/").startswith(".env") or p in {"*.env"} for p in patterns)
    secrets_ignored = any("secrets.toml" in p or p.rstrip("/") in {".streamlit", "/.streamlit"} for p in patterns)
    if not gi.exists():
        notes.append(".gitignore がありません")
    if (root / ".env").exists():
        notes.append(f".env があります（.gitignore 登録: {'あり' if env_ignored else 'なし ← 要確認'}）")
    elif not env_ignored:
        notes.append(".gitignore に .env が登録されていません（今後 .env を作る場合に注意）")
    if (root / ".streamlit" / "secrets.toml").exists():
        notes.append(f".streamlit/secrets.toml があります（.gitignore 登録: {'あり' if secrets_ignored else 'なし ← 要確認'}）")
    if not (root / ".streamlit" / "config.toml").exists():
        notes.append(".streamlit/config.toml はありません（Streamlit の既定設定で動作）")
    return notes


def git_status(root: Path) -> list[str]:
    if not (root / ".git").exists():
        return ["git リポジトリではありません（履歴の確認は不要）"]
    if not shutil.which("git"):
        return ["git リポジトリですが git コマンドが見つかりません"]
    notes = []
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files"], capture_output=True, text=True, encoding="utf-8", errors="ignore"
    ).stdout.splitlines()
    for name in tracked:
        base = name.rsplit("/", 1)[-1]
        if (base == ".env" or base.startswith(".env.")) and not base.endswith((".example", ".sample", ".template")):
            notes.append(f"git 管理下にあります: {name} ← 要確認")
        if base == "secrets.toml":
            notes.append(f"git 管理下にあります: {name} ← 要確認")
    history = subprocess.run(
        ["git", "-C", str(root), "log", "--all", "--oneline", "--", ".env", ".streamlit/secrets.toml"],
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
    ).stdout.strip()
    if history:
        notes.append(f".env / secrets.toml を含むコミットが履歴にあります（{len(history.splitlines())} 件）← 要確認")
    return notes or ["git 管理下に .env / secrets.toml はなく、履歴にも見つかりません"]


def requirement_status(root: Path) -> list[str]:
    req = root / "requirements.txt"
    if not req.exists():
        has_pyproject = (root / "pyproject.toml").exists()
        return ["requirements.txt はありません" + ("（pyproject.toml を確認）" if has_pyproject else "")]
    notes = []
    unpinned = []
    for line in req.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        if not re.search(r"[<>=~!]=?|@", line):
            unpinned.append(line)
        if re.match(r"(?i)streamlit\b", line):
            notes.append(f"streamlit の指定: {line}")
    if unpinned:
        notes.append(f"バージョン指定のないパッケージ: {', '.join(unpinned)}")
    notes.append(f"pip-audit: {'利用可能' if shutil.which('pip-audit') else '未インストール（依存の既知脆弱性は未確認になる）'}")
    return notes


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    if not root.is_dir():
        sys.exit(f"ディレクトリが見つかりません: {root}")

    scanned, hits, module_globals = scan(root)

    print("# 補助スキャン結果（確認すべき候補の一覧）\n")
    print(f"- 対象: {root}")
    print(f"- スキャンしたファイル: {scanned}（.venv・.git・node_modules などは除外）")
    print("- これは正規表現による候補の抽出です。各候補はコードを読んで、外部の値が届くかを確かめてください。\n")

    print("## プロジェクト全体\n")
    for note in gitignore_status(root) + git_status(root) + requirement_status(root):
        print(f"- {note}")
    if hits.get("llm-sdk") and not hits.get("token-cap"):
        print("- LLM SDK を使っていますが、出力トークン上限の指定が見つかりません")
    if not hits.get("auth"):
        print("- 認証の実装（st.login など）は見つかりません（公開する場合は要検討）")
    print()

    print("## 候補（カテゴリ別）\n")
    by_category: dict[str, list[Rule]] = defaultdict(list)
    for r in RULES:
        if hits.get(r.id):
            by_category[r.category].append(r)
    if module_globals:
        by_category["6. セッション分離とキャッシュ"].append(
            Rule("module-global", "", re.compile(""), "モジュールのトップレベルのミュータブル変数（import されるモジュールなら全セッションで共有。メインスクリプトなら問題なし）", "py")
        )
        hits["module-global"] = module_globals

    if not by_category:
        print("候補は見つかりませんでした。")
    for category in sorted(by_category, key=lambda c: (not c[0].isdigit(), int(c.split(".")[0]) if c[0].isdigit() else 0)):
        print(f"### {category}\n")
        for r in by_category[category]:
            found = hits[r.id]
            print(f"- **[{r.id}]** {r.note}（{len(found)} 件）")
            for rel, lineno, shown in found[:MAX_HITS_PER_RULE]:
                print(f"  - `{rel}:{lineno}` — `{shown}`")
            if len(found) > MAX_HITS_PER_RULE:
                print(f"  - …ほか {len(found) - MAX_HITS_PER_RULE} 件")
        print()


if __name__ == "__main__":
    main()
