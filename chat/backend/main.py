"""
Factory Chat Platform — バックエンド
建設現場と事務所をつなぐチャット・勤怠・写真共有API
"""

import os
import uuid
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import aiosqlite

# ── 設定 ──────────────────────────────────────────────────────
DATABASE_URL = os.getenv("FACTORY_CHAT_DB", "factory_chat.db")
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
# 添付画像の最大サイズ (10MB)
MAX_FILE_SIZE = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}

os.makedirs(UPLOAD_DIR, exist_ok=True)

# ── DB 初期化 ─────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    UNIQUE NOT NULL,
    display_name TEXT   NOT NULL,
    role        TEXT    NOT NULL DEFAULT 'worker',
    avatar_url  TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS channels (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    description TEXT    DEFAULT '',
    project_id  TEXT,
    created_by  INTEGER REFERENCES users(id),
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS channel_members (
    channel_id  INTEGER REFERENCES channels(id) ON DELETE CASCADE,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    joined_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (channel_id, user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id  INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    content     TEXT    NOT NULL DEFAULT '',
    parent_id   INTEGER REFERENCES messages(id),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS message_attachments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    file_name   TEXT    NOT NULL,
    file_path   TEXT    NOT NULL,
    file_size   INTEGER NOT NULL DEFAULT 0,
    mime_type   TEXT    NOT NULL DEFAULT 'image/jpeg',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS attendance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    record_date TEXT    NOT NULL,
    clock_in    TEXT,
    clock_out   TEXT,
    location_in TEXT,
    location_out TEXT,
    note        TEXT    DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(user_id, record_date)
);

CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id, created_at);
CREATE INDEX IF NOT EXISTS idx_attachments_message ON message_attachments(message_id);
CREATE INDEX IF NOT EXISTS idx_attendance_user_date ON attendance(user_id, record_date);
"""


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DATABASE_URL)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db():
    db = await get_db()
    try:
        await db.executescript(SCHEMA_SQL)
        await db.commit()
    finally:
        await db.close()


# ── FastAPI アプリ ────────────────────────────────────────────

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _seed_demo_data()
    yield


app = FastAPI(title="Factory Chat Platform", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 添付画像の静的配信
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# ── デモデータ ────────────────────────────────────────────────

async def _seed_demo_data():
    db = await get_db()
    try:
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM users")
        row = await cursor.fetchone()
        if row[0] > 0:
            return

        # デモユーザー
        await db.execute(
            "INSERT INTO users (username, display_name, role) VALUES (?, ?, ?)",
            ("tanaka", "田中 太郎", "manager"),
        )
        await db.execute(
            "INSERT INTO users (username, display_name, role) VALUES (?, ?, ?)",
            ("suzuki", "鈴木 一郎", "worker"),
        )
        await db.execute(
            "INSERT INTO users (username, display_name, role) VALUES (?, ?, ?)",
            ("sato", "佐藤 花子", "admin"),
        )

        # デモチャンネル
        await db.execute(
            "INSERT INTO channels (name, description, project_id, created_by) VALUES (?, ?, ?, ?)",
            ("全体連絡", "全社共通の連絡チャンネル", None, 3),
        )
        await db.execute(
            "INSERT INTO channels (name, description, project_id, created_by) VALUES (?, ?, ?, ?)",
            ("○○橋梁補修工事", "令和6年度 ○○橋梁補修工事の現場チャンネル", "PRJ-2024-001", 1),
        )
        await db.execute(
            "INSERT INTO channels (name, description, project_id, created_by) VALUES (?, ?, ?, ?)",
            ("△△道路改良工事", "△△地区道路改良工事", "PRJ-2024-002", 1),
        )

        # メンバー登録
        for ch_id in [1, 2, 3]:
            for user_id in [1, 2, 3]:
                await db.execute(
                    "INSERT INTO channel_members (channel_id, user_id) VALUES (?, ?)",
                    (ch_id, user_id),
                )

        # デモメッセージ
        await db.execute(
            "INSERT INTO messages (channel_id, user_id, content) VALUES (?, ?, ?)",
            (2, 1, "本日の打設完了しました。養生シート設置済みです。"),
        )
        await db.execute(
            "INSERT INTO messages (channel_id, user_id, content) VALUES (?, ?, ?)",
            (2, 2, "了解です。明日の段取り確認お願いします。"),
        )
        await db.execute(
            "INSERT INTO messages (channel_id, user_id, content) VALUES (?, ?, ?)",
            (1, 3, "今週金曜に安全会議があります。各現場代理人は出席をお願いします。"),
        )

        await db.commit()
    finally:
        await db.close()


# ── Pydantic スキーマ ─────────────────────────────────────────

class UserCreate(BaseModel):
    username: str
    display_name: str
    role: str = "worker"


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    avatar_url: Optional[str] = None
    is_active: bool = True


class ChannelCreate(BaseModel):
    name: str
    description: str = ""
    project_id: Optional[str] = None
    created_by: int


class MessageCreate(BaseModel):
    user_id: int
    content: str = ""
    parent_id: Optional[int] = None


class AttendanceOut(BaseModel):
    id: int
    user_id: int
    display_name: Optional[str] = None
    record_date: str
    clock_in: Optional[str] = None
    clock_out: Optional[str] = None
    location_in: Optional[str] = None
    location_out: Optional[str] = None
    note: str = ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  API エンドポイント
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── ヘルスチェック ────────────────────────────────────────────

@app.get("/")
async def health():
    return {"status": "ok", "service": "Factory Chat Platform"}


# ── Users ─────────────────────────────────────────────────────

@app.get("/api/users")
async def list_users():
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, username, display_name, role, avatar_url, is_active FROM users WHERE is_active = 1"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


@app.post("/api/users", status_code=201)
async def create_user(user: UserCreate):
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO users (username, display_name, role) VALUES (?, ?, ?)",
            (user.username, user.display_name, user.role),
        )
        await db.commit()
        return {"id": cursor.lastrowid, **user.model_dump()}
    except Exception:
        raise HTTPException(status_code=409, detail="ユーザー名が既に存在します")
    finally:
        await db.close()


# ── Channels ──────────────────────────────────────────────────

@app.get("/api/channels")
async def list_channels():
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT c.*, COUNT(cm.user_id) as member_count
               FROM channels c
               LEFT JOIN channel_members cm ON c.id = cm.channel_id
               WHERE c.is_archived = 0
               GROUP BY c.id
               ORDER BY c.created_at DESC"""
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


@app.post("/api/channels", status_code=201)
async def create_channel(ch: ChannelCreate):
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO channels (name, description, project_id, created_by) VALUES (?, ?, ?, ?)",
            (ch.name, ch.description, ch.project_id, ch.created_by),
        )
        channel_id = cursor.lastrowid
        # 作成者を自動的にメンバーに追加
        await db.execute(
            "INSERT INTO channel_members (channel_id, user_id) VALUES (?, ?)",
            (channel_id, ch.created_by),
        )
        await db.commit()
        return {"id": channel_id, **ch.model_dump()}
    finally:
        await db.close()


# ── Messages ──────────────────────────────────────────────────

@app.get("/api/channels/{channel_id}/messages")
async def list_messages(channel_id: int, limit: int = 50, before_id: Optional[int] = None):
    db = await get_db()
    try:
        if before_id:
            cursor = await db.execute(
                """SELECT m.id, m.channel_id, m.user_id, m.content, m.parent_id, m.created_at,
                          u.display_name, u.avatar_url
                   FROM messages m
                   JOIN users u ON m.user_id = u.id
                   WHERE m.channel_id = ? AND m.id < ?
                   ORDER BY m.created_at DESC LIMIT ?""",
                (channel_id, before_id, limit),
            )
        else:
            cursor = await db.execute(
                """SELECT m.id, m.channel_id, m.user_id, m.content, m.parent_id, m.created_at,
                          u.display_name, u.avatar_url
                   FROM messages m
                   JOIN users u ON m.user_id = u.id
                   WHERE m.channel_id = ?
                   ORDER BY m.created_at DESC LIMIT ?""",
                (channel_id, limit),
            )
        rows = await cursor.fetchall()
        messages = []
        for r in rows:
            msg = dict(r)
            # 添付画像を取得
            att_cursor = await db.execute(
                "SELECT id, file_name, file_path, file_size, mime_type FROM message_attachments WHERE message_id = ?",
                (msg["id"],),
            )
            msg["attachments"] = [dict(a) for a in await att_cursor.fetchall()]
            messages.append(msg)
        return list(reversed(messages))
    finally:
        await db.close()


@app.post("/api/channels/{channel_id}/messages", status_code=201)
async def send_message(channel_id: int, msg: MessageCreate):
    if not msg.content.strip():
        raise HTTPException(status_code=400, detail="メッセージが空です")
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO messages (channel_id, user_id, content, parent_id) VALUES (?, ?, ?, ?)",
            (channel_id, msg.user_id, msg.content.strip(), msg.parent_id),
        )
        message_id = cursor.lastrowid
        await db.commit()

        # 作成したメッセージを返す
        cursor = await db.execute(
            """SELECT m.id, m.channel_id, m.user_id, m.content, m.parent_id, m.created_at,
                      u.display_name, u.avatar_url
               FROM messages m JOIN users u ON m.user_id = u.id
               WHERE m.id = ?""",
            (message_id,),
        )
        row = await cursor.fetchone()
        result = dict(row)
        result["attachments"] = []
        return result
    finally:
        await db.close()


# ── 画像添付アップロード ──────────────────────────────────────

@app.post("/api/channels/{channel_id}/messages/with-attachment", status_code=201)
async def send_message_with_attachment(
    channel_id: int,
    user_id: int = Form(...),
    content: str = Form(""),
    file: UploadFile = File(...),
):
    # 拡張子チェック
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"対応していないファイル形式です。対応形式: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # ファイル読み込み & サイズチェック
    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="ファイルサイズが10MBを超えています")

    # ユニークなファイル名で保存
    unique_name = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(UPLOAD_DIR, unique_name)
    with open(save_path, "wb") as f:
        f.write(file_bytes)

    db = await get_db()
    try:
        # メッセージ作成
        cursor = await db.execute(
            "INSERT INTO messages (channel_id, user_id, content) VALUES (?, ?, ?)",
            (channel_id, user_id, content.strip()),
        )
        message_id = cursor.lastrowid

        # 添付レコード作成
        await db.execute(
            """INSERT INTO message_attachments (message_id, file_name, file_path, file_size, mime_type)
               VALUES (?, ?, ?, ?, ?)""",
            (message_id, file.filename, f"/uploads/{unique_name}", len(file_bytes), file.content_type or "image/jpeg"),
        )
        await db.commit()

        # レスポンス
        cursor = await db.execute(
            """SELECT m.id, m.channel_id, m.user_id, m.content, m.parent_id, m.created_at,
                      u.display_name, u.avatar_url
               FROM messages m JOIN users u ON m.user_id = u.id
               WHERE m.id = ?""",
            (message_id,),
        )
        row = await cursor.fetchone()
        result = dict(row)
        att_cursor = await db.execute(
            "SELECT id, file_name, file_path, file_size, mime_type FROM message_attachments WHERE message_id = ?",
            (message_id,),
        )
        result["attachments"] = [dict(a) for a in await att_cursor.fetchall()]
        return result
    except Exception:
        # 失敗時はファイル削除
        if os.path.exists(save_path):
            os.remove(save_path)
        raise
    finally:
        await db.close()


# ── 勤怠 (Attendance) ────────────────────────────────────────

@app.post("/api/attendance/clock-in")
async def clock_in(
    user_id: int = Form(...),
    location: str = Form(""),
):
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M:%S")
    db = await get_db()
    try:
        # 既に出勤済みかチェック
        cursor = await db.execute(
            "SELECT id, clock_in FROM attendance WHERE user_id = ? AND record_date = ?",
            (user_id, today),
        )
        existing = await cursor.fetchone()
        if existing and existing[1]:
            raise HTTPException(status_code=409, detail="本日は既に出勤打刻済みです")

        if existing:
            await db.execute(
                "UPDATE attendance SET clock_in = ?, location_in = ? WHERE id = ?",
                (now, location, existing[0]),
            )
        else:
            await db.execute(
                "INSERT INTO attendance (user_id, record_date, clock_in, location_in) VALUES (?, ?, ?, ?)",
                (user_id, today, now, location),
            )
        await db.commit()
        return {"message": "出勤を記録しました", "time": now, "date": today}
    finally:
        await db.close()


@app.post("/api/attendance/clock-out")
async def clock_out(
    user_id: int = Form(...),
    location: str = Form(""),
):
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M:%S")
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, clock_in, clock_out FROM attendance WHERE user_id = ? AND record_date = ?",
            (user_id, today),
        )
        existing = await cursor.fetchone()
        if not existing or not existing[1]:
            raise HTTPException(status_code=400, detail="先に出勤打刻をしてください")
        if existing[2]:
            raise HTTPException(status_code=409, detail="本日は既に退勤打刻済みです")

        await db.execute(
            "UPDATE attendance SET clock_out = ?, location_out = ? WHERE id = ?",
            (now, location, existing[0]),
        )
        await db.commit()
        return {"message": "退勤を記録しました", "time": now, "date": today}
    finally:
        await db.close()


@app.get("/api/attendance")
async def get_attendance(
    user_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    conditions = []
    params = []
    if user_id:
        conditions.append("a.user_id = ?")
        params.append(user_id)
    if date_from:
        conditions.append("a.record_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("a.record_date <= ?")
        params.append(date_to)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    db = await get_db()
    try:
        cursor = await db.execute(
            f"""SELECT a.id, a.user_id, u.display_name, a.record_date,
                       a.clock_in, a.clock_out, a.location_in, a.location_out, a.note
                FROM attendance a
                JOIN users u ON a.user_id = u.id
                {where}
                ORDER BY a.record_date DESC, u.display_name""",
            params,
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


@app.get("/api/attendance/today")
async def get_today_attendance():
    today = date.today().isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT a.id, a.user_id, u.display_name, a.record_date,
                      a.clock_in, a.clock_out, a.location_in, a.location_out, a.note
               FROM attendance a
               JOIN users u ON a.user_id = u.id
               WHERE a.record_date = ?
               ORDER BY a.clock_in""",
            (today,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()
