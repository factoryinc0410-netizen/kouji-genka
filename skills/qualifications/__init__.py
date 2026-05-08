"""資格者証管理スキル — 建設業向けの資格者証台帳と Gemini OCR の統合。

このパッケージは Phase 2 以降で以下を提供する予定:

- ``ocr_gemini``  : Gemini API クライアント（gemini-2.5-flash）
- ``prompt``      : OCR 用プロンプト（和暦変換・表裏セット束ね指示）
- ``classifier``  : Gemini レスポンスから「ページ束 → 資格候補」へ整形
- ``schema``      : 抽出フィールド dataclass
- ``validator``   : 期限・形式・重複チェック
- ``storage``     : 原本ファイルの保存パス決定

Phase 1 ではテーブル骨組みと一覧表示のみで、OCR は未統合。
"""
