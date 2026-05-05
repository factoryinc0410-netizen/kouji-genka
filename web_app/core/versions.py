"""システム全体のバージョン管理 — 基盤 (CORE) + 各スキル

バージョン命名規約（プロジェクト CLAUDE.md 参照）:
  - セマンティック・バージョニング (MAJOR.MINOR.PATCH) に従う
  - 末尾にハイフン繋ぎで変更内容を付記する
  - スペース・括弧・日本語・大文字は使用禁止（URL 安全な ASCII 小文字のみ）
  例: "1.3.0-flow-layout", "2.1.0-multi-tenant"

使い方:
  - Python コード:
      from web_app.core.versions import CORE_VERSION, SKILL_VERSIONS
  - Jinja2 テンプレート:
      {{ core_version }}           … 基盤バージョン
      {{ skill_versions[key] }}    … 各スキルのバージョン
      {{ skill_display_names[key] }} … UI に表示するスキル名
"""
from __future__ import annotations

# ── 基盤 (Factoryskills 本体) のバージョン ─────────────────
# FastAPI / ルーティング / 共通 UI など、スキル横断の基盤部分に関する変更で更新。
CORE_VERSION: str = "1.1.0-linux-portability"

# ── 各スキルのバージョン（スキル側の config.py から集約）──
# スキルを追加したときは、ここにインポート文とエントリを増やすだけで
# ポータルの一覧・キャッシュバスティング・バッジ表示に反映される。
from chat.version import CHAT_VERSION  # noqa: E402
from skills.construction_cost.config import CONSTRUCTION_COST_VERSION  # noqa: E402
from skills.order_docs.config import ORDER_DOCS_VERSION  # noqa: E402

SKILL_VERSIONS: dict[str, str] = {
    "order_docs": ORDER_DOCS_VERSION,
    "construction_cost": CONSTRUCTION_COST_VERSION,
    "chat": CHAT_VERSION,
}

# ── スキルキー → UI 表示名 ─────────────────────────────────
# バッジやポータル一覧で表示するスキルの和名。
SKILL_DISPLAY_NAMES: dict[str, str] = {
    "order_docs": "注文書作成",
    "construction_cost": "工事日報集計",
    "chat": "ファクトリーチャット",
    "core": "基盤",
}


def compound_cache_key() -> str:
    """CSS/JS キャッシュバスティング用の複合キー。

    基盤 + 全スキルのバージョンをハイフン区切りで連結し、どれか 1 つでも
    上がれば自動的にブラウザキャッシュが無効化されるようにする。
    """
    parts = [CORE_VERSION] + [v for v in SKILL_VERSIONS.values()]
    return "-".join(parts)
