"""qualifications テスト群の共通フィクスチャ。

- ``app_env`` fixture を提供。temp DB / staging を切り替え、TestClient を返す。
- モジュールスコープ (= テストファイルごとに 1 インスタンス) で共有することで
  起動オーバーヘッドを抑えつつ、ファイル間の状態汚染は防ぐ。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def app_env():
    """temp DB / staging を切り替え、admin ユーザーと cc_workers をシードする。

    各テストモジュールはここで返る dict を `app_env` として受け取り、
    ``app_env["client"]`` で TestClient を、``app_env["db_path"]`` で DB 直アクセスを行う。
    """
    from _pytest.monkeypatch import MonkeyPatch

    tmp_root = Path(tempfile.mkdtemp(prefix="qual_app_env_"))
    tmp_db = tmp_root / "app.db"
    tmp_upload = tmp_root / "uploads"
    tmp_upload.mkdir()
    os.environ["DATABASE_PATH"] = str(tmp_db)
    os.environ["UPLOAD_DIR"] = str(tmp_upload)

    from fastapi.testclient import TestClient

    from web_app.core import config as cfg
    from web_app.core import database as db
    from web_app.core.dependencies import get_current_user, require_admin
    from web_app.main import app
    from skills.qualifications import pipeline as pipeline_mod
    from skills.qualifications import storage as storage_mod
    from web_app.routers import qualifications as q_router

    mpatch = MonkeyPatch()
    # 既にロード済みの絶対パスを上書きする (他テストで先に import される可能性に対応)
    mpatch.setattr(cfg, "DATABASE_PATH", tmp_db, raising=False)
    mpatch.setattr(cfg, "UPLOAD_DIR", tmp_upload, raising=False)
    mpatch.setattr(db, "_DB_PATH", str(tmp_db), raising=False)
    mpatch.setattr(pipeline_mod, "DATABASE_PATH", tmp_db, raising=False)
    mpatch.setattr(
        storage_mod, "QUALIFICATIONS_STAGING_ROOT",
        tmp_upload / "qualifications", raising=False,
    )
    mpatch.setattr(
        q_router, "QUALIFICATIONS_STAGING_ROOT",
        tmp_upload / "qualifications", raising=False,
    )

    asyncio.run(db.init_db())

    # users + cc_workers のシード (FK 用)
    conn = sqlite3.connect(str(tmp_db))
    conn.execute(
        "INSERT INTO users (id, username, display_name, password_hash, is_admin) "
        "VALUES ('admin-id', 'admin', '管理者', 'x', 1)"
    )
    conn.execute(
        "INSERT INTO cc_workers (worker_id, worker_name, group_name, is_active) "
        "VALUES (1, '山田太郎', 'A班', 1)"
    )
    conn.execute(
        "INSERT INTO cc_workers (worker_id, worker_name, group_name, is_active) "
        "VALUES (2, '佐藤花子', 'B班', 1)"
    )
    conn.execute(
        "INSERT INTO cc_workers (worker_id, worker_name, group_name, is_active) "
        "VALUES (3, '鈴木一郎', 'A班', 1)"
    )
    conn.commit()
    conn.close()

    admin_user = {
        "id": "admin-id", "username": "admin",
        "display_name": "管理者", "is_admin": 1,
    }
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[require_admin] = lambda: admin_user

    client = TestClient(app)
    yield {
        "client": client,
        "db_path": tmp_db,
        "staging_root": tmp_upload / "qualifications",
    }

    app.dependency_overrides.clear()
    mpatch.undo()
    shutil.rmtree(tmp_root, ignore_errors=True)
