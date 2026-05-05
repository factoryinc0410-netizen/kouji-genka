# 開発環境セットアップガイド

このドキュメントは、`dev-app` 環境を新たに構築する手順と、過去に発生した
落とし穴（B-1 venv シバン汚染問題）を再発させないための運用ルールをまとめます。

---

## 1. Python 仮想環境 (venv) の構築

### 1.1 鉄則

> **venv は絶対に `cp -r` などでコピーしないこと。**
> 各プロジェクト独自に `python3 -m venv .venv` で新規作成する。

過去に dev-app の venv が prod-app からコピー作成されていた結果、`pyvenv.cfg`
の `command` フィールドおよび一部のスクリプト shebang が prod-app を参照する
状態となり、`pip install` が誤って prod-app 配下にパッケージを入れる事故が
発生した（BACKLOG.md B-1 参照）。

venv の中身（site-packages, スクリプト類）は **作成時のパスにハードコード
される**ため、別ディレクトリへのコピー / 移動は機能的にも・メタデータ的にも
壊す行為である。

### 1.2 推奨手順

```bash
cd /home/ubuntu/dev-app

# 1) 既存 venv があればバックアップしてから消す
[ -d .venv ] && mv .venv .venv.bak.$(date +%Y%m%d)

# 2) 新規 venv 作成（システム Python から起動するのが安全）
/usr/bin/python3 -m venv .venv

# 3) pip を最新化（古い pip だと一部 wheel の解決に失敗することがある）
.venv/bin/pip install --upgrade pip

# 4) 依存をインストール
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# 5) 動作確認
.venv/bin/python -m pytest -m "not slow"

# 6) バックアップが不要になったら削除
rm -rf .venv.bak.*
```

### 1.3 検証ワンライナー

新規構築直後 / 不審な挙動が出たときは、下記で venv の健全性をチェックする:

```bash
# pyvenv.cfg の command 行が現プロジェクトを指していることを確認
grep "^command" .venv/pyvenv.cfg
# 期待:  command = /usr/bin/python3 -m venv /home/ubuntu/dev-app/.venv

# .venv/bin/ 配下のスクリプトの shebang が現プロジェクトを指していることを確認
.venv/bin/python -c "
from pathlib import Path
issues = [(f.name, open(f).readline().rstrip())
          for f in Path('.venv/bin').iterdir()
          if f.is_file() and not f.is_symlink()
          and open(f, 'rb').read(2) == b'#!'
          and not open(f).readline().startswith('#!/home/ubuntu/dev-app/.venv/bin/python')]
print(f'WRONG shebangs: {len(issues)}')
for n, l in issues:
    print(f'  {n}: {l}')
"
# 期待: WRONG shebangs: 0
```

---

## 2. Playwright Chromium

Playwright のブラウザバイナリは `~/.cache/ms-playwright/` に配置され、
**venv の外側**にある。よって venv を作り直しても Chromium の再
ダウンロードは不要。

ただし新規マシンで一度も入れていない場合は次が必要:

```bash
.venv/bin/playwright install chromium
sudo .venv/bin/playwright install-deps chromium   # apt 依存
```

---

## 3. 本番デプロイとの関係

このサーバの prod-app は `/home/ubuntu/prod-app/` + `factory-prod.service` で
稼働している。dev-app から prod-app へコードを反映する正規ルートは下記:

```bash
# prod-app 側で
sudo systemctl stop factory-prod
git fetch devapp && git merge --ff-only devapp/main
find . -name __pycache__ -type d -exec rm -rf {} +
sudo systemctl start factory-prod
curl http://127.0.0.1:8000/health   # "OK" を確認
```

`__pycache__` の事前削除は **必須**（過去のリファクタで re-export を消した際、
古い .pyc を踏んで無音のロード失敗が起きるリスクがあるため）。

なお prod-app の venv も **dev-app の venv をコピーしてはいけない**。
prod-app 側で同じく `python3 -m venv .venv` から作る。

---

## 4. 関連ドキュメント

- `SYSTEM_SPEC.md` — システム全体仕様
- `skills/order_docs/CLAUDE.md` — order_docs スキル規約 + Linux コマンド
- `BACKLOG.md` — 技術負債・改善タスクのトラッキング
