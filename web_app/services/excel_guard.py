"""
Excel プロセス安全管理 — PID トラッキングによるゾンビ防止

COM 経由で起動した Excel プロセスを PID レベルで追跡し、
処理完了後（正常・異常問わず）に確実に終了させる。
taskkill /IM EXCEL.EXE のような全体キルは絶対に行わない。
"""
import logging
import time
from contextlib import contextmanager

import psutil

logger = logging.getLogger("web_app.excel_guard")


def _get_excel_pids() -> set[int]:
    """現在動作中の全 EXCEL.EXE プロセスの PID を取得する。"""
    pids = set()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["name"] and proc.info["name"].upper() == "EXCEL.EXE":
                pids.add(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return pids


def _terminate_pid(pid: int, timeout: int = 15) -> bool:
    """指定 PID の EXCEL.EXE を安全に終了させる。

    1. まず terminate (SIGTERM相当) で穏やかに終了要求
    2. timeout 秒待っても終了しなければ kill (SIGKILL相当) で強制終了

    Returns: True=終了成功, False=対象が既に存在しない or 非Excelプロセス
    """
    try:
        proc = psutil.Process(pid)
        # 安全確認: 対象が本当に EXCEL.EXE であることを再チェック
        if proc.name().upper() != "EXCEL.EXE":
            logger.warning(
                "PID %d は EXCEL.EXE ではありません (%s) — スキップ",
                pid, proc.name(),
            )
            return False

        logger.info("Excel プロセス終了要求: PID=%d", pid)
        proc.terminate()

        try:
            proc.wait(timeout=timeout)
            logger.info("Excel プロセス正常終了: PID=%d", pid)
            return True
        except psutil.TimeoutExpired:
            logger.warning("Excel プロセスが応答しません — 強制終了: PID=%d", pid)
            proc.kill()
            proc.wait(timeout=5)
            logger.info("Excel プロセス強制終了完了: PID=%d", pid)
            return True

    except psutil.NoSuchProcess:
        logger.debug("PID %d は既に終了しています", pid)
        return False
    except psutil.AccessDenied:
        logger.error("PID %d の終了権限がありません", pid)
        return False
    except Exception:
        logger.exception("PID %d の終了処理で予期せぬエラー", pid)
        return False


@contextmanager
def track_excel_processes():
    """COM 操作前後の Excel プロセス差分を追跡するコンテキストマネージャ。

    使い方:
        with track_excel_processes() as guard:
            # COM 操作（generate_from_excel 等）
            result = generate_from_excel(...)
        # ← ここで自動的に新規 Excel プロセスが終了される

    例外発生時も finally で確実にクリーンアップされる。
    """
    before_pids = _get_excel_pids()
    logger.debug("Excel PID スナップショット（処理前）: %s", before_pids)

    guard = _ExcelGuard(before_pids)
    try:
        yield guard
    finally:
        guard.cleanup()


class _ExcelGuard:
    """Excel プロセスの差分追跡と安全なクリーンアップを行うガードオブジェクト。"""

    def __init__(self, before_pids: set[int]):
        self._before_pids = before_pids
        self._cleaned = False

    def cleanup(self) -> int:
        """処理中に新たに起動された Excel プロセスを全て安全に終了する。

        Returns: 終了させたプロセス数
        """
        if self._cleaned:
            return 0
        self._cleaned = True

        # 少し待機して COM の Quit() が完了する時間を与える
        time.sleep(1)

        after_pids = _get_excel_pids()
        new_pids = after_pids - self._before_pids

        if not new_pids:
            logger.debug("新規 Excel プロセスなし — クリーンアップ不要")
            return 0

        logger.warning(
            "処理後に残存する Excel プロセスを検出: %s — 終了処理を開始",
            new_pids,
        )

        terminated = 0
        for pid in new_pids:
            if _terminate_pid(pid):
                terminated += 1

        if terminated > 0:
            logger.info("Excel クリーンアップ完了: %d プロセスを終了", terminated)

        return terminated


def check_orphan_excel_processes() -> list[int]:
    """親プロセスが存在しない孤児 Excel プロセスを検出する（情報提供のみ）。

    Returns: 孤児と判定された EXCEL.EXE の PID リスト
    """
    orphans = []
    for proc in psutil.process_iter(["pid", "name", "ppid"]):
        try:
            if proc.info["name"] and proc.info["name"].upper() == "EXCEL.EXE":
                ppid = proc.info["ppid"]
                try:
                    parent = psutil.Process(ppid)
                    # 親が存在するなら孤児ではない
                    _ = parent.name()
                except psutil.NoSuchProcess:
                    orphans.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if orphans:
        logger.warning("孤児 EXCEL.EXE プロセスを検出: %s", orphans)

    return orphans
