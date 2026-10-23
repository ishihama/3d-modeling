"""ずんだもん（Slack ボット）に渡すツール定義（Anthropic の tools 形式）と、呼び出し先の対応表。

ボットは GET /v1/tools でこの一覧を取り、各ツールを HTTP 呼び出しに変換する。
`thread`（Slack のスレッドの識別子）は Claude に見せず、ボットが毎回付ける。
executor が "bot" のツールはボット側で実行する（ワーカーからファイルを取り、スレッドに上げる）。
"""

from __future__ import annotations

_COMMON = (
    "家庭用の小物・おもちゃを 3D プリント（Bambu Lab X2D）するモデルを作るためのツール。"
    "使う前に必ず modeling_read_file で CLAUDE.md を読み、そのルールとワークフローに従うこと"
    "（寸法が曖昧なまま作り始めない、spec.md に無い寸法を勝手に決めない、子供向けの安全ルールなど）。"
)

TOOLS = [
    {
        "endpoint": "/v1/read_file",
        "definition": {
            "name": "modeling_read_file",
            "description": _COMMON + " リポジトリ内のテキストファイルを読む。読めるのは CLAUDE.md・README.md と "
                           "models/・printlib/・templates/・docs/・tools/ 以下（out/ は除く）。"
                           "見本: models/desk-tray/model.py（単体）、models/slim-bin/model.py（複数パーツ・可動部・試し刷り）。",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "リポジトリのルートからの相対パス（例: CLAUDE.md）"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "endpoint": "/v1/list_models",
        "definition": {
            "name": "modeling_list_models",
            "description": "既存のモデル（models/<name>/）の一覧と、spec.md の見出し・model.py の有無を返す。",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "endpoint": "/v1/run_build123d",
        "definition": {
            "name": "modeling_run_build123d",
            "description": "build123d のコードを試しに実行し、できた形状の体積・外形寸法・有効性を返す。"
                           "形状は 1 ステップずつ作り、ここで毎回確かめてから次へ進む。"
                           "最後に result = <形状>（複数パーツなら parts = {...}）を置くとそれを測る。"
                           "import できるのは build123d・printlib・math・numpy など（model.py と同じ制限）。",
            "input_schema": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "実行する Python コード"}},
                "required": ["code"],
                "additionalProperties": False,
            },
        },
    },
    {
        "endpoint": "/v1/write_model",
        "definition": {
            "name": "modeling_write_model",
            "description": "models/<name>/spec.md と model.py を書いて保存する（スレッドのブランチに commit）。"
                           "spec.md はユーザーと合意した内容だけを書く。model.py は CLAUDE.md の書き方"
                           "（先頭に定数、build123d・printlib だけを import、末尾で result = build()）に従う。"
                           "片方だけ渡せば、もう片方は今のまま。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "モデル名（英小文字・数字・ハイフン。例: slim-bin）"},
                    "spec_md": {"type": "string", "description": "spec.md の全文"},
                    "model_py": {"type": "string", "description": "model.py の全文"},
                    "message": {"type": "string", "description": "変更の要約（commit メッセージ）"},
                },
                "required": ["name", "message"],
                "additionalProperties": False,
            },
        },
    },
    {
        "endpoint": "/v1/run_e2e",
        "definition": {
            "name": "modeling_run_e2e",
            "description": "保存済みのモデルを通しで確認する: STEP/STL 書き出し → 印刷可能性チェック（全パーツ・試し刷り）"
                           " → 4 方向の画像 → 回せる 3D ビューア。ERROR が 0 になるまで修正すること。"
                           "結果のファイルは modeling_share_files でスレッドに送る。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "モデル名"},
                    "toy": {"type": "boolean", "description": "子供向け（小部品シリンダー判定をする）"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "endpoint": "/v1/artifacts/{thread}/{name}/{filename}",
        "method": "GET",
        "executor": "bot",
        "definition": {
            "name": "modeling_share_files",
            "description": "modeling_run_e2e が作ったファイルを Slack のスレッドに送る。"
                           "ふつうは <name>-iso.png（プレビュー）と <name>-viewer.html（回せる 3D ビューア）と印刷用 STL。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "モデル名"},
                    "files": {"type": "array", "items": {"type": "string"},
                              "description": "modeling_run_e2e が返した artifacts のファイル名"},
                },
                "required": ["name", "files"],
                "additionalProperties": False,
            },
        },
    },
    {
        "endpoint": "/v1/open_pr",
        "definition": {
            "name": "modeling_open_pr",
            "description": "このスレッドで作ったモデルの PR を GitHub に作る。ユーザーがはっきり頼んだときだけ使う。"
                           "プリンタへの送信はしない（印刷は人が Bambu Studio で行う）。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string", "description": "変更内容と確認結果（check_stl の結果など）"},
                },
                "required": ["title", "body"],
                "additionalProperties": False,
            },
        },
    },
]
