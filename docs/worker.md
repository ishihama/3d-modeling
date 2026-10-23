# 3D ワーカー（Slack のずんだもんから 3D モデリングを動かす）

Slack ボット [lightcafecreation/slack-agent-bot](https://github.com/lightcafecreation/slack-agent-bot)（ずんだもん）が、
このリポジトリの機能を **ツールとして呼ぶ** ための HTTP サービス。Cloud Run で動かす。

- 状態: **ワーカーは実装済み**（`worker/`・`Dockerfile`・`tests/test_worker.py`）。**ずんだもん側の組み込みと Cloud Run のデプロイ（Terraform）は次の段階**
- Claude の呼び出しはずんだもん側（Messages API + 自前のツールループ、slack-agent-bot の ADR-002 / ADR-008）。ワーカーは Claude を呼ばない

## 全体像

```
Slack のスレッド
  ↓
ずんだもん（slack-agent-bot, Cloud Run）… 会話・履歴（Slack が SSOT）・Claude のツールループ・ファイルの投稿
  │  起動時に GET /v1/tools でツール定義を取得し、AgentTool として登録
  │  ツール呼び出しを HTTP に変換（thread は Claude に見せずボットが付ける）
  ↓ IAM 認証（ID トークン）
3D ワーカー（このリポジトリ, Cloud Run）
  ├─ スレッドごとの作業場所 = git クローン + ブランチ slack/<thread>（状態はブランチに置く）
  ├─ build123d コードの試し実行（体積・寸法・有効性を返す）
  ├─ models/<name>/spec.md・model.py の保存（commit・push）
  ├─ e2e（書き出し → 全パーツのチェック → 4 方向の画像 → ビューア）
  ├─ 成果物の配信（PNG・viewer.html・STL）
  └─ PR の作成（ユーザーが頼んだときだけ）
```

## ツール（`GET /v1/tools` が返す。定義の正本は `worker/tools_spec.py`）

| ツール | 呼び出し先 | 内容 |
| --- | --- | --- |
| `modeling_read_file` | `POST /v1/read_file` | CLAUDE.md・README.md と models/ printlib/ templates/ docs/ tools/ 以下を読む（out/ と .git は不可） |
| `modeling_list_models` | `POST /v1/list_models` | モデルの一覧 |
| `modeling_run_build123d` | `POST /v1/run_build123d` | build123d のコードを試しに実行し、`result`（または `parts`）の体積・外形・有効性を返す。MCP の代わり |
| `modeling_write_model` | `POST /v1/write_model` | spec.md・model.py を書き、スレッドのブランチに commit・push |
| `modeling_run_e2e` | `POST /v1/run_e2e` | `tools/e2e.py --no-mcp --no-browser`。印刷用 STL ごとのチェック結果（ERROR / WARN の内容）と成果物の一覧を返す |
| `modeling_share_files` | `GET /v1/artifacts/{thread}/{name}/{file}` | **ボット側で実行**: ワーカーからファイルを取り、`files_upload_v2` でスレッドに投稿 |
| `modeling_open_pr` | `POST /v1/open_pr` | ブランチを push して PR を作る（既にあればその URL） |

すべてのリクエストに `thread`（例: `<channel>-<thread_ts>`。英数字と `._-` の 80 文字まで）を付ける。

## 安全面

Slack から Claude にコードを書かせて実行するので、**実行するコード（試し実行のコード・model.py）は信頼しない**前提で守る。
本当の境界は 2・3 で、1 は早めに断るための補助。

1. **静的チェック**（`worker/codecheck.py`）: model.py のルール（CLAUDE.md）と同じく import を build123d・printlib・math・numpy などに限定し、
   `eval` / `open` / `__import__`、`_` で始まる属性、`os` / `inspect` / `sys` などに届く属性（例: `dataclasses.inspect.os`）を拒否する。
   Python の静的チェックには抜け道がある（例: 文字列の `format` で属性をたどる）ので、これには頼らない
2. **権限を落として実行**（`worker/sandbox.py`）: `sandbox` ユーザーで、秘密情報を含まない最小限の環境変数・CPU 時間・プロセス数・ファイルサイズの上限つき。
   - 実行は **ワーカー全体で 1 つずつ**。終わったら（タイムアウトでも正常終了でも）**sandbox ユーザーのプロセスを全部止める**
     （二重 fork + setsid でプロセスグループから抜けた子も残さない。残るとパイプを握ってワーカーが待ち続けたり、別スレッドの実行に干渉したりするため）
   - sandbox の HOME は実行ごとに新しく作って消す。Python のユーザー site（`~/.local`）は読まない
3. **sandbox が書ける場所を root が読み書きしない**（`worker/workspace.py` の冒頭の表）:
   - リポジトリのクローン（`repo/`）は root だけの持ち物。sandbox は書けない（spec.md・model.py の保存は root がする）
   - 試し実行のコードは root だけが書ける `in/` に置いて読ませる
   - e2e は、モデルを使い捨ての `runs/<乱数>/<name>/` に写してから sandbox に渡す。親ディレクトリは root のまま。終わったら消す
   - 成果物は、`O_NOFOLLOW` で開いた「通常のファイル・リンクでない・ハードリンクでない・sandbox の持ち物」だけを root 側の `artifacts/` にコピーし、配信・集計はそこから行う
   - ファイルの読み取り（`read_file`）は、たどった先のパスで判定し、途中にリンクがあれば拒否する
4. **秘密情報**: GitHub トークンは root の git 操作（コマンドごとに渡し、`.git/config` に書かない）と PR 作成だけで使う。`.git` は 0700。
   トークンは環境変数より **ファイル**（`WORKER_GITHUB_TOKEN_FILE`。Cloud Run のシークレットのボリュームをモード 0400 で）で渡す
5. **書ける場所の限定**: commit するのは `models/<name>/` の spec.md・model.py だけ。PR は人がレビューしてからマージする
6. **プリンタには送らない**（CLAUDE.md の「やらないこと」のまま）

テスト（`tests/test_worker.py`）では、**静的チェックを無効にした状態で**次の攻撃を再現し、防げることを確認している:
root プロセスの環境（`/proc/<pid>/environ`）・`.git/config` の読み取り、リポジトリ・入力置き場への書き込みとリンクの設置、
成果物の場所に root の環境へのリンクを仕込む、実行場所の親の差し替え、setsid で逃げた子プロセスによるタイムアウトの無効化、
改ざんされたチェック結果の一覧。

残るリスク:
- sandbox からのネットワーク通信は止めていない。必要になったら Cloud Run の VPC 出口（Direct VPC egress + ファイアウォール）で制限する
- スレッドごとのロックと「実行は 1 つずつ」はインスタンス内だけ。最大インスタンス数 1 が前提（下記）
- sandbox のユーザーは全スレッドで共通（実行を 1 つずつにし、毎回プロセスを止めて、同時に存在しないようにしている）。
  利用者が増えて並列にしたくなったら、実行ごとのユーザー・名前空間（または gVisor / 別ジョブ）に分ける
- 浅いクローン（`--depth 50`）なので、ブランチの履歴が深くなると取り込み（fast-forward）に失敗して 409 になることがある

## 状態の持ち方

- **会話**: Slack のスレッド（ずんだもんの ADR-001 と同じ）
- **モデル**: git のブランチ `slack/<thread>`。保存のたびに push するので、インスタンスが替わっても続きから再開できる（テスト済み）
- **成果物（out/）**: インスタンスのローカル（/tmp）。別インスタンスに振られると `GET /v1/artifacts` が 404 になるので、
  **最大インスタンス数は 1** にする（個人・社内用途の負荷なら十分）。増やすときは成果物を GCS に置く形に変える
- ツールと printlib は **ワーカーのイメージのもの** を使い、作業場所からは models/ だけを扱う。main にマージしたらイメージを作り直す

## Cloud Run の設定（次の段階で Terraform 化。slack-agent-bot の terraform/ にそろえる）

| 項目 | 値 | 理由 |
| --- | --- | --- |
| 認証 | `--no-allow-unauthenticated`、ずんだもんのサービスアカウントに `roles/run.invoker` | 外部から叩けないように |
| CPU / メモリ | 2 vCPU / 4 GiB（要実測） | OCP の処理・層ごとのチェック・描画 |
| タイムアウト | 900 秒 | e2e は小さいモデルで約 20 秒、slim-bin（8 パーツ）で 1〜2 分 |
| 最大インスタンス | 1 | 成果物がインスタンスのローカルにあるため（上記） |
| 環境変数 | `WORKER_REPO_REMOTE=https://github.com/ishihama/3d-modeling.git`、`WORKER_PUSH=1` | |
| シークレット | `WORKER_GITHUB_TOKEN_FILE`（Secret Manager をボリュームで、モード 0400） | このリポジトリだけに Contents と Pull requests の読み書きを許した fine-grained トークン（または GitHub App） |

処理はすべてリクエストの中で終わる（非同期ジョブにしない）ので、CPU の常時割り当ては不要。
ずんだもん側のタイムアウト（LLM 900 秒・全体 960 秒・Cloud Run 1000 秒）の中に収まる。

## ずんだもん側でやること（次の段階）

1. 起動時に `GET /v1/tools` を呼び、各ツールを `AgentTool` として登録（`executor: "bot"` 以外は HTTP POST に変換。`thread` を付ける）
2. `modeling_share_files`: `GET /v1/artifacts/...` で取り、`files_upload_v2` でスレッドに投稿（scope `files:write` を追加。ADR-003 の最小権限の見直し）
3. Cloud Run の ID トークンでワーカーを呼ぶ（`gcp_auth.py` の仕組みを流用）
4. system prompt に「3D モデリングの依頼では、まず modeling_read_file で CLAUDE.md を読む」を足す
5. 利用者・チャンネルを限定する（ADR-003 のセーフガードに合わせる）

## 手元での起動・テスト

```sh
uv sync --group worker
uv run --group worker tests/test_worker.py                      # 通しのテスト（git・build123d を実際に使う）
WORKER_WORK_ROOT=/tmp/3d-worker uv run --group worker uvicorn worker.app:app --port 8080
curl -s localhost:8080/v1/tools
```

イメージ: `docker build -t 3d-worker .`（Docker Hub のレート制限に当たるときは `--build-arg BASE_IMAGE=mirror.gcr.io/library/python:3.12-slim`）。
