"""
COM 排他ロックモジュール — Excel COM 操作の同時実行を防止する。

問題:
    Excel COM (win32com) はプロセス内でシングルスレッドでしか安全に動作しない。
    Streamlit は各ユーザーセッションを別スレッドで実行するため、
    複数ユーザーが同時に注文書生成を実行すると Excel プロセスが競合してクラッシュする。

解決策:
    プロセス全体で 1 つの threading.Lock を保持し、COM 操作を直列化する。
    タイムアウト付きの acquire で、長時間のデッドロックを防止する。

使い方:
    from .com_lock import com_lock, acquire_com_lock

    # 方法1: コンテキストマネージャ（推奨）
    with acquire_com_lock(timeout=300):
        # COM 操作
        app = win32com.client.DispatchEx("Excel.Application")
        ...

    # 方法2: 直接ロック
    com_lock.acquire()
    try:
        ...
    finally:
        com_lock.release()
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  プロセスグローバル排他ロック
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

com_lock = threading.Lock()

# デフォルトタイムアウト（秒）
# 1 ジョブ（5 業者 × 2 シート変換）を想定し、余裕をもった値
DEFAULT_TIMEOUT = 600  # 10 分


class COMBusyError(TimeoutError):
    """COM ロックの取得がタイムアウトした場合に送出される例外。"""

    def __init__(self, timeout: float):
        self.timeout = timeout
        super().__init__(
            f"Excel COM 操作のロック取得がタイムアウトしました（{timeout}秒）。"
            f"別のユーザーの処理が完了するまでお待ちください。"
        )


@contextmanager
def acquire_com_lock(timeout: float = DEFAULT_TIMEOUT):
    """
    COM 操作用の排他ロックをタイムアウト付きで取得するコンテキストマネージャ。

    Parameters
    ----------
    timeout : float
        ロック取得の最大待機時間（秒）。デフォルト 600 秒（10 分）。

    Raises
    ------
    COMBusyError
        タイムアウト内にロックを取得できなかった場合。

    Example
    -------
    >>> with acquire_com_lock(timeout=300):
    ...     # この中では他スレッドが COM 操作をできない
    ...     result = generate_from_excel(excel_path, output_dir)
    """
    caller = threading.current_thread().name
    logger.debug("COM ロック取得待機: thread=%s", caller)
    start = time.monotonic()

    acquired = com_lock.acquire(timeout=timeout)

    if not acquired:
        elapsed = time.monotonic() - start
        logger.error(
            "COM ロック取得タイムアウト: thread=%s, elapsed=%.1f秒",
            caller, elapsed,
        )
        raise COMBusyError(timeout)

    elapsed = time.monotonic() - start
    if elapsed > 1.0:
        logger.info(
            "COM ロック取得: thread=%s (%.1f秒待機)",
            caller, elapsed,
        )
    else:
        logger.debug("COM ロック取得: thread=%s", caller)

    try:
        yield
    finally:
        com_lock.release()
        logger.debug("COM ロック解放: thread=%s", caller)
