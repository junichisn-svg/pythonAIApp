"""ライティングツールの定義。ツールを増やすときは TOOLS に追加する。"""

from dataclasses import dataclass, field
from typing import Literal

SYSTEM_INSTRUCTION = """あなたは経験豊富なプロのライター兼編集者です。
- 依頼に対して、そのまま使える完成度の高い文章を作成します。
- 出力は Markdown で整形します。
- 「承知しました」などの前置きや、成果物のあとの補足説明は書かず、成果物だけを出力します（依頼で求められた場合を除く）。
- 入力に書かれていない事実（固有名詞・数値・日付など）を作り上げません。必要な場合は【要確認: ○○】のように明示します。
- 指定がない限り日本語で書きます。"""

CHAT_SYSTEM_INSTRUCTION = """あなたは文章作成を手伝う、経験豊富なライター兼編集者のアシスタントです。
ユーザーの相談に、日本語で具体的かつ簡潔に答えてください。
文章を作成・修正する場合は、そのまま使える形で提示してください。"""

REFINE_TEMPLATE = """直前の出力を、次の指示に従って修正してください。修正後の全文だけを出力してください。

## 修正指示
{instruction}"""


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: Literal["text", "textarea", "select"] = "text"
    options: tuple[str, ...] = ()
    placeholder: str = ""
    required: bool = False
    height: int | None = None


@dataclass(frozen=True)
class WritingTool:
    id: str
    name: str
    icon: str
    description: str
    instruction: str = ""
    fields: tuple[Field, ...] = field(default_factory=tuple)


def build_prompt(tool: WritingTool, values: dict[str, str]) -> str:
    """ツールの指示と、入力された項目からプロンプトを組み立てる。"""
    lines = [tool.instruction.strip(), "", "---"]
    for f in tool.fields:
        value = str(values.get(f.key) or "").strip()
        if value:
            lines += ["", f"## {f.label}", value]
    return "\n".join(lines)


TONES = ("親しみやすい", "丁寧・誠実", "専門的・論理的", "カジュアル", "熱量高め")
PATTERN_COUNTS = ("1", "2", "3")
IDEA_COUNTS = ("5", "10", "20")

TOOLS: list[WritingTool] = [
    WritingTool(
        id="blog",
        name="ブログ記事作成",
        icon="📝",
        description="テーマとキーワードから、見出し付きのブログ記事や構成案を作成します。",
        instruction="""以下の条件でブログ記事を執筆してください。
- 1行目に「# 記事タイトル」、続けて読者の興味を引く導入文（リード文）を書く
- 本文は「##」「###」の見出しで構成し、最後に「## まとめ」を入れる
- 指定された文字数を目安にする
- キーワードが指定されていれば、タイトル・見出し・本文に自然に含める（不自然な詰め込みはしない）
- 「出力内容」が「構成案のみ」の場合は、タイトル案3つと、各見出しで書く内容の要点つきの見出し構成だけを出力する""",
        fields=(
            Field("theme", "テーマ", placeholder="例: 在宅ワークの集中力を高める方法", required=True),
            Field("reader", "想定読者", placeholder="例: 在宅勤務を始めたばかりの会社員"),
            Field("keywords", "キーワード", placeholder="例: 在宅ワーク, 集中力, 環境づくり"),
            Field("tone", "トーン", "select", TONES),
            Field("length", "文字数", "select", ("約2,000字", "約1,000字", "約3,000字", "約5,000字")),
            Field("mode", "出力内容", "select", ("記事本文", "構成案のみ")),
            Field("notes", "盛り込みたい内容・メモ", "textarea", placeholder="体験談や伝えたいポイントなど"),
        ),
    ),
    WritingTool(
        id="email",
        name="メール作成・返信",
        icon="✉️",
        description="受信メールへの返信文や、新しく送るメールの文面を作成します。",
        instruction="""メールの文面を作成してください。
- 「受信したメール」があればそれへの返信を、なければ新規メールを作成する
- 1行目に「件名: ...」を書き（返信なら「件名: Re: ...」）、続けて本文を書く
- 相手との関係とトーンに合った敬語・言葉遣いにする
- 「伝えたいこと」を漏れなく、わかりやすい順序で盛り込む
- 署名・名前が指定されていれば末尾に入れる
- パターン数が2以上の場合は「### パターン1」のように見出しで区切り、それぞれ方向性を変える""",
        fields=(
            Field("received", "受信したメール（返信する場合）", "textarea", height=200),
            Field(
                "points",
                "伝えたいこと",
                "textarea",
                placeholder="例: 打ち合わせは来週火曜14時で了承。資料は金曜までに送る",
                required=True,
            ),
            Field("relation", "相手との関係", "select", ("社外の取引先・顧客", "社内の上司", "社内の同僚・部下", "友人・知人")),
            Field("tone", "トーン", "select", ("丁寧", "標準", "簡潔", "お詫び", "お断り（角を立てずに）", "感謝")),
            Field("signature", "署名・名前", placeholder="例: 山田（〇〇株式会社）"),
            Field("count", "パターン数", "select", PATTERN_COUNTS),
        ),
    ),
    WritingTool(
        id="summary",
        name="要約",
        icon="📋",
        description="長い文章を、指定した形式と長さで要約します。",
        instruction="""以下の文章を要約してください。
- 指定された形式と長さに従う
- 元の文章にない情報や解釈を加えない
- 「重視する観点」が指定されていれば、その観点に関わる内容を優先する""",
        fields=(
            Field("text", "要約する文章", "textarea", required=True, height=300),
            Field("format", "形式", "select", ("箇条書き", "文章（段落）", "3行要約", "一言要約＋詳細の箇条書き")),
            Field("length", "長さ", "select", ("標準", "短め", "詳しめ")),
            Field("focus", "重視する観点（任意）", placeholder="例: コストに関する部分"),
        ),
    ),
    WritingTool(
        id="proofread",
        name="校正・推敲",
        icon="🔍",
        description="誤字脱字や文法の誤りを直し、読みやすい文章に整えます。修正点の一覧つき。",
        instruction="""以下の文章を校正・推敲してください。次の2つを順に出力します。
1. 「## 修正後の文章」: 修正を反映した全文
2. 「## 主な修正点」: 「修正前 → 修正後（理由）」の形の箇条書き。修正がなければ「修正点はありません」と書く
「チェックの範囲」が「誤字脱字・文法のみ」の場合は、表現や構成は変えずに明らかな誤りだけを直す。""",
        fields=(
            Field("text", "校正する文章", "textarea", required=True, height=300),
            Field("scope", "チェックの範囲", "select", ("読みやすさも含めて推敲", "誤字脱字・文法のみ", "全面的にブラッシュアップ")),
            Field("style", "文体", "select", ("元の文体のまま", "です・ます調に統一", "だ・である調に統一")),
        ),
    ),
    WritingTool(
        id="rewrite",
        name="リライト・言い換え",
        icon="🔄",
        description="文章の意味を保ったまま、トーンや難易度、長さを変えて書き直します。",
        instruction="""以下の文章を、指定されたスタイルに書き換えてください。
- 元の文章の意味・情報は保つ
- 書き換えた文章だけを出力する""",
        fields=(
            Field("text", "元の文章", "textarea", required=True, height=250),
            Field(
                "style",
                "書き換えのスタイル",
                "select",
                (
                    "ビジネス向けに丁寧に",
                    "カジュアル・フレンドリーに",
                    "やさしく・わかりやすく（専門用語を避ける）",
                    "簡潔に短く",
                    "詳しく膨らませる",
                    "説得力を高める",
                    "子ども向けに",
                ),
            ),
            Field("extra", "追加の指示（任意）", placeholder="例: 200字以内に"),
        ),
    ),
    WritingTool(
        id="translate",
        name="翻訳",
        icon="🌐",
        description="自然な表現で多言語に翻訳します。",
        instruction="""以下の文章を指定された言語に翻訳してください。
- 翻訳文だけを出力する
- 固有名詞や専門用語は、その言語で一般的な表記にする
- 「訳し方」に従って調整する""",
        fields=(
            Field("text", "翻訳する文章", "textarea", required=True, height=250),
            Field(
                "target",
                "翻訳先の言語",
                "select",
                ("英語", "日本語", "中国語（簡体字）", "中国語（繁体字）", "韓国語", "スペイン語", "フランス語", "ドイツ語"),
            ),
            Field("style", "訳し方", "select", ("自然な意訳", "原文に忠実", "ビジネス文書向け", "カジュアル")),
        ),
    ),
    WritingTool(
        id="title",
        name="タイトル・キャッチコピー",
        icon="💡",
        description="記事タイトル、メール件名、キャッチコピーなどの案をまとめて出します。",
        instruction="""以下の内容に合うタイトル・キャッチコピーの案を作成してください。
- 指定された個数の案を番号付きリストで出力する
- 切り口（ベネフィット、数字、疑問形、意外性など）にバリエーションを持たせる
- 各案のあとに「— 」に続けて、その案の狙いを一言添える
- 最後に「## おすすめ」として、特に良い案を1つ選び理由を書く""",
        fields=(
            Field("content", "内容・概要", "textarea", required=True),
            Field(
                "usage",
                "用途",
                "select",
                ("ブログ記事タイトル", "メール件名", "キャッチコピー", "YouTube動画タイトル", "セミナー・イベント名", "商品・サービス名"),
            ),
            Field("count", "案の数", "select", IDEA_COUNTS),
            Field("direction", "方向性・含めたい言葉（任意）"),
        ),
    ),
    WritingTool(
        id="sns",
        name="SNS投稿",
        icon="📣",
        description="X・Instagram・LinkedIn などに合わせた投稿文を作成します。",
        instruction="""以下の内容をもとに、SNS投稿文を作成してください。
- 指定されたプラットフォームの文字数と文化に合わせる（例: X は全角140字以内、Instagram は改行と絵文字で読みやすく、LinkedIn はビジネス向けの語り口）
- 1文目で興味を引く
- 「ハッシュタグ」が「つける」の場合は、投稿の末尾に関連性の高いハッシュタグを3〜5個つける
- パターン数が2以上の場合は「### パターン1」のように見出しで区切り、それぞれ切り口を変える""",
        fields=(
            Field("content", "投稿したい内容", "textarea", required=True),
            Field("platform", "プラットフォーム", "select", ("X（旧Twitter）", "Instagram", "Threads", "LinkedIn", "Facebook")),
            Field("tone", "トーン", "select", ("カジュアル", "親しみやすい", "ビジネス", "エモーショナル", "ユーモア")),
            Field("hashtags", "ハッシュタグ", "select", ("つける", "つけない")),
            Field("count", "パターン数", "select", PATTERN_COUNTS),
        ),
    ),
    WritingTool(
        id="minutes",
        name="議事録作成",
        icon="🗒️",
        description="会議のメモや文字起こしから、決定事項と TODO を整理した議事録を作ります。",
        instruction="""以下の会議メモ・文字起こしをもとに、議事録を作成してください。次の構成で出力します。
- 「# 議事録: 会議名」（会議名が空欄なら内容から簡潔に付ける）
- 「## 概要」: 日時・参加者（記載があれば）と会議の目的
- 「## 議題と議論の内容」
- 「## 決定事項」
- 「## TODO」: 「担当者 / 内容 / 期限」の表。不明な項目は「未定」とする
- 「## 保留事項・次回までの課題」
メモにない内容は補わない。""",
        fields=(
            Field("memo", "会議メモ・文字起こし", "textarea", required=True, height=300),
            Field("title", "会議名（任意）"),
            Field("meta", "日時・参加者（任意）", placeholder="例: 10/21 14:00〜 佐藤、鈴木、田中"),
        ),
    ),
    WritingTool(
        id="ideas",
        name="アイデア出し",
        icon="🧠",
        description="記事ネタや企画など、テーマに沿ったアイデアを幅広く出します。",
        instruction="""以下のテーマについて、アイデアを出してください。
- 指定された個数のアイデアを番号付きリストで出し、各アイデアに1〜2文の説明をつける
- ありきたりな案だけでなく、切り口の異なる案も混ぜる
- 最後に「## 次の一歩」として、特に有望なアイデアを形にするための具体的な行動を3つ提案する""",
        fields=(
            Field("theme", "テーマ・お題", "textarea", required=True, placeholder="例: 30代向け副業ブログの記事ネタ"),
            Field("purpose", "目的・使い道（任意）"),
            Field("count", "アイデアの数", "select", IDEA_COUNTS),
            Field("constraints", "条件・制約（任意）"),
        ),
    ),
]

CHAT_TOOL = WritingTool(
    id="chat",
    name="フリーチャット",
    icon="💬",
    description="文章の相談や、ほかのツールにない依頼を対話形式で自由に頼めます。",
)
