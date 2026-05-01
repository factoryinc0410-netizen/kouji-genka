"""
pytest 共通設定 — tests/ 配下のテストがプロジェクトルート直下の
`skills` / `web_app` パッケージを参照できるように sys.path を調整する。

背景:
  テストスクリプトを `tests/` フォルダに移設したことで、
  `from skills.order_docs.*` / `from web_app.*` の import が
  CWD に依存しなくなるようにする必要があった。
  conftest.py はプロジェクトルートを sys.path の先頭に追加する。
"""
from __future__ import annotations

import sys
from pathlib import Path

# tests/ の親ディレクトリ (= プロジェクトルート) を sys.path に追加
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
