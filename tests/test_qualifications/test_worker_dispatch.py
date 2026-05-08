"""services/worker.py の dispatch ロジックの単体テスト。

実際のワーカースレッド起動や DB 接続は伴わない。dispatch / unpack の
ピュアな関数挙動だけを検証する。
"""
from __future__ import annotations

import logging

import pytest

from web_app.services import worker as worker_mod


class TestUnpackPayload:
    def test_string_falls_back_to_order_docs(self):
        """str 単体は後方互換で order_docs の job_id とみなす。"""
        assert worker_mod._unpack_payload("abc123") == ("order_docs", "abc123")

    def test_tuple_with_skill(self):
        assert worker_mod._unpack_payload(("qualifications", "xyz")) == (
            "qualifications", "xyz",
        )
        assert worker_mod._unpack_payload(("order_docs", "xyz")) == (
            "order_docs", "xyz",
        )

    @pytest.mark.parametrize("bad", [
        123,
        ("only_one",),
        ("a", "b", "c"),
        None,
        [],
    ])
    def test_invalid_payload_raises(self, bad):
        with pytest.raises((ValueError, TypeError)):
            worker_mod._unpack_payload(bad)


class TestDispatch:
    def test_dispatch_order_docs(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr(
            worker_mod, "_process_job", lambda jid: called.append(jid),
        )
        worker_mod._dispatch("order_docs", "abc123")
        assert called == ["abc123"]

    def test_dispatch_qualifications(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr(
            worker_mod, "_process_qualifications_job",
            lambda jid: called.append(jid),
        )
        worker_mod._dispatch("qualifications", "xyz789")
        assert called == ["xyz789"]

    def test_dispatch_unknown_skill_logs_error(self, monkeypatch, caplog):
        """未知のスキルは関数を呼ばずにエラーログだけ出す (致命的にはしない)。"""
        # 既知のディスパッチ先が呼ばれていないことを保証
        monkeypatch.setattr(
            worker_mod, "_process_job",
            lambda jid: pytest.fail("呼ばれてはいけない"),
        )
        monkeypatch.setattr(
            worker_mod, "_process_qualifications_job",
            lambda jid: pytest.fail("呼ばれてはいけない"),
        )
        with caplog.at_level(logging.ERROR, logger="web_app.worker"):
            worker_mod._dispatch("unknown_skill", "abc")
        log_text = "\n".join(r.message for r in caplog.records)
        assert "未知のスキル" in log_text
        assert "unknown_skill" in log_text

    def test_dispatch_does_not_swallow_exceptions(self, monkeypatch):
        """ディスパッチ先が例外を投げたら _dispatch はそれを上に伝搬する。

        最外殻で捕捉するのは _worker_main の責務であり、_dispatch は素通し。
        """
        def _boom(_jid):
            raise RuntimeError("boom")

        monkeypatch.setattr(worker_mod, "_process_job", _boom)
        with pytest.raises(RuntimeError, match="boom"):
            worker_mod._dispatch("order_docs", "abc")
