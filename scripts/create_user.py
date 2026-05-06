#!/usr/bin/env python3
"""scripts/create_user.py — admin tool: register a new user account.

Reuses web_app.core.auth.hash_password (bcrypt) so the resulting row is
verifiable by the running app's existing login flow. The INSERT statement
mirrors web_app.core.auth.create_user but uses sync sqlite3 to avoid
spinning up an async event loop in a one-shot CLI.

Usage:
    # interactive (recommended — no plaintext in shell history)
    .venv/bin/python scripts/create_user.py --username taro --display-name 太郎

    # scripted / automation
    .venv/bin/python scripts/create_user.py --username taro --password 'p@ss'

    # target prod-app's DB explicitly
    .venv/bin/python scripts/create_user.py \\
        --db /home/ubuntu/prod-app/web_app/data/app.db \\
        --username taro --display-name 太郎

    # grant admin
    .venv/bin/python scripts/create_user.py --username root --admin
"""
from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "web_app" / "data" / "app.db"

# Make web_app importable regardless of cwd.
sys.path.insert(0, str(PROJECT_ROOT))

from web_app.core.auth import hash_password  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Register a new user account.")
    p.add_argument("--username", required=True,
                   help="login username (must be unique within the target DB)")
    p.add_argument("--display-name", default=None,
                   help="display name (defaults to --username)")
    p.add_argument("--password", default=None,
                   help="plaintext password; if omitted, you will be prompted")
    p.add_argument("--admin", action="store_true",
                   help="grant admin privileges (is_admin=1, default 0)")
    p.add_argument("--db", default=str(DEFAULT_DB),
                   help=f"target SQLite DB (default: {DEFAULT_DB})")
    return p.parse_args(argv)


def get_password(arg_password: str | None) -> str:
    if arg_password is not None:
        if not arg_password:
            raise SystemExit("[error] empty --password")
        return arg_password
    pw = getpass.getpass("Password: ")
    confirm = getpass.getpass("Password (confirm): ")
    if pw != confirm:
        raise SystemExit("[error] password mismatch")
    if not pw:
        raise SystemExit("[error] empty password")
    return pw


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"[error] DB not found: {db_path}", file=sys.stderr)
        return 1

    password = get_password(args.password)
    display_name = args.display_name or args.username
    user_id = uuid.uuid4().hex
    pw_hash = hash_password(password)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO users (id, username, display_name, password_hash, is_admin) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, args.username, display_name, pw_hash, int(args.admin)),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        msg = str(e)
        if "UNIQUE" in msg.upper():
            print(f"[error] username '{args.username}' already exists in {db_path}",
                  file=sys.stderr)
        else:
            print(f"[error] sqlite IntegrityError: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    print(f"[ok] created user '{args.username}' "
          f"(id={user_id}, display_name='{display_name}', admin={args.admin})")
    print(f"     db: {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
