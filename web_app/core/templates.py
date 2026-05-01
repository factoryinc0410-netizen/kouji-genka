"""共有 Jinja2Templates — アプリ全体で 1 つのインスタンスを共有する。

Jinja2 の env.globals に以下を登録することで、すべてのテンプレートから
名前参照で使える：

  - app_version        : 旧 API 互換。複合キャッシュキー（基盤+全スキル）
  - core_version       : 基盤 (Factoryskills 本体) のバージョン
  - skill_versions     : スキルキー → バージョン の辞書
  - skill_display_names: スキルキー → UI 表示名 の辞書

各ルーターがテンプレートをレンダリングする際、コンテキストに
  "skill_key": "order_docs"
を含めると、base.html のバッジが「注文書作成 v{ver}」形式で描画される。
skill_key を渡さない場合は「基盤 v{core_version}」が表示される。
"""
from fastapi.templating import Jinja2Templates

from web_app.core.versions import (
    CORE_VERSION,
    SKILL_DISPLAY_NAMES,
    SKILL_VERSIONS,
    compound_cache_key,
)

# ── プロジェクト全体で共有する Jinja2Templates インスタンス ─────
templates = Jinja2Templates(directory="web_app/templates")

# ── グローバル変数（テンプレートから直接参照可能） ─────────────
# CSS/JS のキャッシュバスティングキーは「基盤 + 全スキルの複合キー」。
# 1 つでも上がれば自動的にブラウザキャッシュが無効化される。
templates.env.globals["app_version"] = compound_cache_key()

# バッジ表示用の基礎データ
templates.env.globals["core_version"] = CORE_VERSION
templates.env.globals["skill_versions"] = SKILL_VERSIONS
templates.env.globals["skill_display_names"] = SKILL_DISPLAY_NAMES
