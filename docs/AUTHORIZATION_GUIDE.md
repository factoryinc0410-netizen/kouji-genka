# 権限管理ガイド — 新しい業務機能を追加するときの手順

本プロジェクトの権限管理システムは、機能（feature）ごとに **none / general / manager** の 3 段階で
ユーザーごとのアクセスレベルを管理します。本ドキュメントは、新しい業務機能（例: 在庫管理、請求書発行、
マスタデータ管理など）を追加する際に、確立済みの「横展開パターン」に沿って権限を適用する手順を
ステップバイステップで示します。

すでに `daily_report`（工事日報集計）と `order_docs`（注文書作成）で同じパターンが適用済みなので、
それらを参考にしながら読み進めてください。

---

## 全体像

権限管理は次の 4 つのレイヤーで構成されています。

| レイヤー | 担当ファイル | 責務 |
|---|---|---|
| データ | `web_app/core/database.py` の `user_permissions` テーブル | `(user_id, feature_name) → access_level` の保存 |
| ロジック | `web_app/core/auth.py`（`has_permission` ほか） | レベル比較・upsert・取得 |
| ガード | `web_app/core/dependencies.py` の `RequirePermission` | FastAPI Depends 用の認可 |
| UI | `web_app/core/templates.py` の `has_perm` Jinja グローバル | テンプレートでの表示制御 |
| 管理 UI | `web_app/routers/admin_users.py` の `_FEATURE_CATALOG` | `/admin/users/{id}/permissions` の選択肢生成 |

レベル階層は `manager > general > none` の 3 段（auth.py の `ACCESS_LEVELS` 参照）。
`is_admin=True` のユーザーはすべての機能・レベルで素通しになります（`has_permission` 内の
ショートカット）。

---

## ステップ 1: 機能カタログに登録

`web_app/routers/admin_users.py` の `_FEATURE_CATALOG` に 1 行追加します。
ここに登録された機能は `/admin/users/{id}/permissions` の編集 UI に自動的に行が増えます。

```python
# web_app/routers/admin_users.py
_FEATURE_CATALOG: list[tuple[str, str, str]] = [
    (
        "daily_report",
        "工事日報集計",
        "general: 日報入力・集計閲覧 / manager: 現場・作業員マスタや確定・ロールバック",
    ),
    (
        "order_docs",
        "注文書作成",
        "general: ジョブ一覧/状態確認/成果物DL / manager: Excel アップロード・発行（キュー投入）",
    ),
    # ↓ 新規追加
    (
        "inventory",                       # feature_name キー（user_permissions.feature_name と一致）
        "在庫管理",                         # 管理画面に表示する日本語ラベル
        "general: 在庫照会 / manager: 入出庫登録・棚卸",  # general/manager の差分を 1 行で
    ),
]
```

**命名ルール**:
- `feature_name` は半角英小文字 + アンダースコア（例: `inventory`, `billing`, `master_data`）
- ルーターのプレフィックス（`/inventory` など）と必ずしも一致させなくてよい（権限の単位は業務単位）
- 既存の `daily_report` は機能名としてのキー、`construction_cost` はルーター URL/モジュール名として
  あえて分離している（ID と権限キーをカップリングしないことで将来の改名が楽になる）

---

## ステップ 2: ルーター冒頭で定数を定義

新しい業務ルーター（例: `web_app/routers/inventory.py`）を作るときは、ファイル先頭で機能キーと
依存関数のシングルトンを定義します。これは `construction_cost.py` / `order_docs.py` と同じパターンです。

```python
# web_app/routers/inventory.py
from fastapi import APIRouter, Depends
from web_app.core.dependencies import RequirePermission

router = APIRouter(prefix="/inventory", tags=["inventory"])

# 機能名 — user_permissions.feature_name と一致させること
_FEATURE_KEY = "inventory"

# Depends 用シングルトン。テスト側で dependency_overrides を貼る際にも
# 同一インスタンスを参照することで一括差し替えできる。
_RequireGeneral = RequirePermission(_FEATURE_KEY, "general")
_RequireManager = RequirePermission(_FEATURE_KEY, "manager")
```

シングルトン化（モジュールレベルで 1 度だけ生成）するメリット:
- `app.dependency_overrides[_RequireGeneral]` で一括上書きできる
- 1 リクエストごとにインスタンスを再生成するオーバーヘッドを避けられる

---

## ステップ 3: 各エンドポイントを `Depends(...)` で保護

下表の判断軸でレベルを使い分けます。

| 操作の種類 | 推奨レベル | 例 |
|---|---|---|
| 閲覧・一覧・状態参照（GET） | `general` | 在庫一覧、検索、レポート閲覧 |
| 自分の成果物のダウンロード（GET） | `general` | 自分が出した在庫レポート PDF |
| マスタ管理画面（GET、編集フォーム含む） | `manager` | 商品マスタ画面（編集 UI を含むため） |
| 作成・更新・削除（POST/PUT/DELETE） | `manager` | 入出庫登録、棚卸確定、商品追加 |
| 確定・ロールバック等の破壊的操作 | `manager` | 月次締めの取り消し |

```python
# 閲覧（general）
@router.get("/", response_class=HTMLResponse)
async def inventory_list(
    request: Request,
    user: dict = Depends(_RequireGeneral),
):
    ...

# 入出庫登録（manager）
@router.post("/transactions/add")
async def add_transaction(
    user: dict = Depends(_RequireManager),
    item_id: int = Form(...),
    qty: float = Form(...),
):
    ...
```

### 補足: `is_admin` ユーザーのバイパス
`RequirePermission` は内部で `has_permission(user, ..., level)` を呼び、その関数の冒頭で
`user.is_admin` が True なら True を返します。よって `_RequireManager` を要求するエンドポイントでも、
管理者は `user_permissions` への登録なしにアクセスできます。

### 補足: 権限不足のレスポンス
- `Accept: text/html`（ブラウザ）→ `web_app/main.py` の `http_exception_handler` が「権限がありません(403)」HTML を返却
- `Accept: application/json`（API/JS）→ `{"detail": "この操作には機能『...』の『...』以上の権限が必要です。"}` の JSON
- 振り分けは自動。エンドポイント側で個別にハンドリングする必要はない。

---

## ステップ 4: テンプレートの表示制御

`has_perm` 関数が `Jinja2Templates.env.globals` に登録されているので、テンプレートからそのまま呼べます
（`web_app/core/templates.py` 参照）。

シグネチャ:
```python
has_perm(user, feature_name: str, required_level: str = "general") -> bool
```

- `user` が `None` / 空の場合は `False`
- `user.is_admin == True` なら `True`（バックドア兼運用上の保険）
- それ以外は `user.permissions[feature_name]` と `required_level` を階層比較

`user.permissions` は `web_app/core/dependencies.py` の `get_current_user` がリクエストごとに
SQLite から再ロードします。**セッションキャッシュは挟んでいないため、管理者が権限を変更すると
次のリクエストから即座に反映されます**（ホットスワップ対応）。

### 4-1. ナビ・メニューの表示／非表示

機能の入口リンク（ドロップダウン全体・メニュー項目）を gate します。

```jinja
{# トップナビ — 注文書作成リンクは order_docs:general 以上で表示 #}
{% if has_perm(user, 'order_docs', 'general') %}
<li class="nav-item">
    <a class="nav-link" href="/orders/">注文書作成</a>
</li>
{% endif %}

{# 工事日報のドロップダウン — daily_report:general 未満なら丸ごと非表示 #}
{% if has_perm(user, 'daily_report', 'general') %}
<li class="nav-item dropdown">
    <a class="nav-link dropdown-toggle" data-bs-toggle="dropdown">工事日報集計</a>
    <ul class="dropdown-menu">
        <li><a class="dropdown-item" href="/construction-cost/">ダッシュボード</a></li>
        <li><a class="dropdown-item" href="/construction-cost/history">履歴</a></li>
        {# manager 専用項目はネストして個別 gate #}
        {% if has_perm(user, 'daily_report', 'manager') %}
        <li><hr class="dropdown-divider"></li>
        <li><a class="dropdown-item" href="/construction-cost/sites">現場マスタ管理</a></li>
        <li><a class="dropdown-item" href="/construction-cost/aggregate">集計処理</a></li>
        {% endif %}
    </ul>
</li>
{% endif %}
```

### 4-2. ページ内の重要ボタン

general 閲覧可能なページに manager 専用ボタンが残っているケース（例: history.html の
「取り消し」ボタン）。

```jinja
{# 確定取り消しは破壊的操作のため manager 必須。
   権限なしユーザーには操作列を「-」で表示 #}
{% if log.status == 'confirmed' and has_perm(user, 'daily_report', 'manager') %}
<button class="btn btn-outline-danger" onclick="openRollbackModal({{ log.log_id }})">
    取り消し
</button>
{% else %}
<span class="text-muted small">-</span>
{% endif %}
```

### 4-3. ポータル（ホーム）のカード

`web_app/routers/portal.py` の `DEPARTMENTS` に `feature` キーを追加し、テンプレートで
`has_perm(user, tool.feature, 'general')` でフィルタすれば、その機能の権限が無いユーザーには
カードごと表示されません。

```python
# portal.py
{
    "id": "inventory",
    "feature": "inventory",   # ← user_permissions.feature_name と対応
    "name": "在庫管理",
    ...
}
```

```jinja
{# portal.html #}
{% for tool in dept.tools %}
  {% if has_perm(user, tool.feature, 'general') %}
    <div class="col-md-6 col-lg-4">
      <a href="{{ tool.url }}">...</a>
    </div>
  {% endif %}
{% endfor %}
```

---

## ステップ 5: 動作確認

最低限、以下の 3 ロールで疎通確認します。

| ロール | 期待動作 |
|---|---|
| `none`（権限未登録） | ホームのカード非表示・トップナビ非表示・URL 直打ちで 403 |
| `general` | 閲覧 OK、編集系で 403、編集ボタンが非表示 |
| `manager` | 全操作 OK |

簡易確認:
1. `admin / admin` でログイン（または既存管理者）
2. `/admin/users/create` でテストユーザーを作成
3. `/admin/users/{id}/permissions` で新機能のレベルを設定
4. テストユーザーでログインし直して挙動確認

回帰テスト:
```bash
.venv/bin/python -m pytest -q
```
ベースラインは **147 passed / 34 skipped**（プロジェクト時点の値）。

---

## セキュリティ上の注意

### UI 非表示は UX 改善のみ
テンプレートの `has_perm` ガードは見やすさのためのものです。**実際の認可は必ず `RequirePermission`
（サーバー側）で行ってください**。HTML を改竄しても POST が通らないことが本物のセキュリティです。
両方を組み合わせる二重防御が標準形です。

### 自分自身の権限編集は禁止
`/admin/users/{id}/permissions` は、操作対象が自分自身のときは編集不可（`admin_users.py` で
GET/POST どちらも `/admin/users` にリダイレクト）。is_admin の自己剥奪と同じ思想で、ロックアウト
事故を防ぎます。

### CSRF
権限を変更する POST（`/admin/users/{id}/permissions`）には `verify_csrf_token` が依存に入って
います。新しい業務機能の POST にも、状態を変える操作には CSRF 検証を組み込むことを推奨します
（既存の `construction_cost` 系 POST は CSRF 未対応のため、別タスクで段階導入予定）。

### 監査ログ
権限を変更すると `user_audit_logs` に `action="update_permissions"` で 1 行追記されます。
`/admin/users` ページ下部の監査ログ表で「権限変更」フィルタで確認できます。

---

## 参考: 既存の適用例

| 機能 | ルーター | テンプレート |
|---|---|---|
| `daily_report` | `web_app/routers/construction_cost.py` | `web_app/templates/construction_cost/*.html` |
| `order_docs` | `web_app/routers/order_docs.py` | `web_app/templates/order_docs/*.html` |

新機能を追加する際はこれらをコピーして書き換えるのが最速です。

## 参考: ホットスワップ動作

管理者がユーザーの権限を変更したあと、そのユーザーは再ログイン無しで次のリクエストから新しい権限が
適用されます。理由:

- セッションには user_id だけが保存され、permissions は保存していない
- `get_current_user` がリクエスト毎に `user_permissions` テーブルを参照する
- `RequirePermission` も同様にリクエスト毎に DB を見る

検証は `tests/_test_html_pdf` 等の既存ディレクトリには置かず、開発時に
`docs/AUTHORIZATION_GUIDE.md` の手順に沿って手動で行えば十分です。

---

最終更新: 権限管理システム導入完了時点
