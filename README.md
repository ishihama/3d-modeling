# 3d-modeling

家庭用の小物・おもちゃを **Bambu Lab X2D** で 3D プリントするためのリポジトリ。
モデルはすべて Python（build123d）でコード化し、Claude Code と対話しながら設計する。
**すべてローカル完結**（外部 SaaS・クラウド 3D 生成 API は使わない）。

設計ルール・ワークフローは [CLAUDE.md](CLAUDE.md) を参照。

## 構成

```
.
├── CLAUDE.md               # Claude 向けの方針・設計ルール・ワークフロー
├── .mcp.json               # build123d-mcp の登録
├── pyproject.toml          # uv 管理（Python 3.12 固定）
├── tools/
│   ├── export_model.py     # model.py → out/ に STEP / STL
│   ├── check_stl.py        # 印刷可能性チェック CLI
│   ├── selftest_check_stl.py  # check_stl.py の動作確認（ダミー STL で検証）
│   └── e2e.py              # 通し確認（書き出し → チェック → MCP 検証・4 方向レンダリング）
├── templates/spec.md       # 仕様書の雛形
├── models/<name>/
│   ├── spec.md             # 仕様（寸法・制約・対象年齢・変更履歴）
│   ├── model.py            # モデル本体（パラメータは先頭の定数、末尾で result = build()）
│   └── out/                # STEP / STL / check.json / レンダリング画像（git 管理外）
└── profiles/               # Bambu Studio プロファイルのエクスポート
```

## セットアップ（Mac）

1. uv をインストール

   ```sh
   brew install uv
   ```

2. 依存をインストール（Python 3.12 は uv が自動で用意する）

   ```sh
   uv sync
   ```

   > VTK / cadquery-ocp が Python 3.13+ に未対応のため、3.12 に固定している。

3. build123d-mcp の起動確認（初回はダウンロードに数分かかる）

   ```sh
   uv tool run --python 3.12 build123d-mcp   # 起動して待機すれば OK。Ctrl-C で終了
   ```

   `.mcp.json` に登録済みなので、このディレクトリで `claude` を起動すれば MCP サーバとして読み込まれる
   （初回は承認を求められる）。`/mcp` で `build123d` が connected になっていることを確認する。

4. 動作確認

   ```sh
   uv run tools/selftest_check_stl.py    # チェッカーがダミー STL を正しく判定するか
   uv run tools/e2e.py models/desk-tray  # サンプルで書き出し → チェック → MCP → レンダリングまで通す
   ```

   最後に `E2E: PASS` と出れば OK。`models/desk-tray/out/` に STL と 4 方向の PNG ができる。

5. Bambu Studio をインストールし、プリンタを **LAN オンリーモード** で接続する。

### Blender MCP（有機形状が必要になってから）

キャラクターや曲面主体のおもちゃを作るときだけ追加する。
**Blender 公式（Blender Lab）の MCP** を、その時点の公式ドキュメントの手順どおりに導入し、`.mcp.json` に追記する。
非公式の `ahujasid/blender-mcp` は使わない。

## モデルの作り方

1. `models/<name>/spec.md` を用意する（`templates/spec.md` をコピー）。Claude に頼めば確認しながら埋めてくれる。
2. Claude Code で「`models/<name>` を作って」と依頼する。Claude は CLAUDE.md のワークフローに従い、
   段階的にモデリング → `model.py` 保存 → `out/` に STEP/STL 出力 → チェック → レンダリング確認 → 変更履歴追記まで行う。
3. 手動で再生成・チェックする場合:

   ```sh
   uv run tools/e2e.py models/<name> [--toy]                          # 下の 2 つ + MCP 検証・レンダリングを一括
   uv run tools/export_model.py models/<name>                        # STEP / STL 書き出しのみ
   uv run tools/check_stl.py models/<name>/out/<name>.stl            # 実用品
   uv run tools/check_stl.py models/<name>/out/<name>.stl --toy      # おもちゃ（小部品判定）
   uv run tools/check_stl.py models/<name>/out/<name>.stl --dual     # 2 ノズル同時使用
   uv run tools/check_stl.py models/<name>/out/<name>.stl --allow-supports  # サポート前提の設計
   ```

   結果は `<name>.check.json` に保存され、ERROR があれば終了コード 1。

## 印刷手順（人が行う）

1. `check_stl.py` が **ERROR 0** であることを確認する（WARN は spec.md に理由があるか確認）。
2. Bambu Studio で `models/<name>/out/<name>.stl`（または `.step`）を読み込む。
3. 向きを確認する（最大平面が底。Claude は印刷向きでモデルを出力している）。
4. プリセットを選ぶ: ノズル 0.4 mm / 積層 0.2 mm / spec.md 記載の材料（既定 PLA、必要なら PETG）。
   保存済みプロファイルは `profiles/` にある。
5. サポートは spec.md で指定がない限り **無し**。2 ノズル使用時は spec.md の素材指定に従う。
6. スライスしてプレビューで確認（ブリッジ・オーバーハング・最初の層）し、LAN 経由でプリンタへ送信して印刷する。
7. 印刷結果（寸法のずれ・はめあい）を spec.md の変更履歴にメモしておくと次回の調整に使える。
