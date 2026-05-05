"""Factory Chat スキルのバージョン定数（独立モジュール）。

`web_app/core/versions.py` から軽量に import するため、
FastAPI アプリ初期化を含む `chat/backend/main.py` から切り離して定義する。

Factory Chat に変更を加えたら、このバージョンを必ず bump すること。
命名規約は skills/order_docs/CLAUDE.md §5.2 と同じ:
  MAJOR.MINOR.PATCH-<short-suffix>  （ASCII 小文字 + ハイフン）
"""
from __future__ import annotations

CHAT_VERSION: str = "1.0.0-initial-version"
