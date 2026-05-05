#!/usr/bin/env python3
"""scripts/backup_assets.py — daily ZIP backup of critical assets (Phase G-2).

Backs up the following files from $PROJECT_ROOT into ~/backups/assets_YYYYMMDD.zip:
    - web_app/data/app.db
    - chat/backend/factory_chat.db
    - .env

Retention: 7 days. Older assets_*.zip files are deleted automatically.

Usage:
    python3 scripts/backup_assets.py
    # cron: 0 3 * * * /home/ubuntu/dev-app/.venv/bin/python /home/ubuntu/dev-app/scripts/backup_assets.py
"""
from __future__ import annotations

import datetime as dt
import re
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = Path.home() / "backups"
RETENTION_DAYS = 7

TARGETS = [
    Path("web_app/data/app.db"),
    Path("chat/backend/factory_chat.db"),
    Path(".env"),
]

ARCHIVE_PATTERN = re.compile(r"^assets_(\d{8})\.zip$")


def make_archive(today: dt.date) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = BACKUP_DIR / f"assets_{today:%Y%m%d}.zip"

    missing: list[Path] = []
    included: list[Path] = []
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in TARGETS:
            src = PROJECT_ROOT / rel
            if not src.exists():
                missing.append(rel)
                continue
            zf.write(src, arcname=str(rel))
            included.append(rel)

    if not included:
        archive_path.unlink(missing_ok=True)
        raise SystemExit(f"[backup] FAIL: none of {TARGETS} exist under {PROJECT_ROOT}")

    print(f"[backup] wrote {archive_path} ({archive_path.stat().st_size:,} bytes)")
    for rel in included:
        print(f"[backup]   + {rel}")
    for rel in missing:
        print(f"[backup]   ! missing (skipped): {rel}", file=sys.stderr)

    return archive_path


def prune_old(today: dt.date) -> None:
    cutoff = today - dt.timedelta(days=RETENTION_DAYS - 1)
    for entry in BACKUP_DIR.iterdir():
        m = ARCHIVE_PATTERN.match(entry.name)
        if not m:
            continue
        try:
            stamp = dt.datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if stamp < cutoff:
            entry.unlink()
            print(f"[backup] pruned {entry.name} (older than {RETENTION_DAYS} days)")


def main() -> int:
    today = dt.date.today()
    make_archive(today)
    prune_old(today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
