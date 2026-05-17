"""代価表リンク設定スキル — 公開 API。

Excel ファイルの「基準」列に含まれる代価表参照キーワード
（単 N 号 / 施 N 号 / Ｐ N 号 / 明 N 号）を、同一ワークブック内の
ターゲット行（A〜D 列の同名セル）への内部ハイパーリンクに自動変換する。
"""
from skills.daika_link_setting.config import DAIKA_LINK_SETTING_VERSION
from skills.daika_link_setting.processor import LinkStats, process

__all__ = ["DAIKA_LINK_SETTING_VERSION", "LinkStats", "process"]
