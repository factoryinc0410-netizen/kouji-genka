"use client";

import { useState } from "react";
import {
  AttendanceRecord,
  clockIn,
  clockOut,
} from "../lib/api";

interface AttendancePanelProps {
  userId: number;
  todayRecords: AttendanceRecord[];
  onUpdate: () => void;
  onClose?: () => void;
  isMobile: boolean;
}

export default function AttendancePanel({
  userId,
  todayRecords,
  onUpdate,
  onClose,
  isMobile,
}: AttendancePanelProps) {
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  const myRecord = todayRecords.find((r) => r.user_id === userId);
  const hasClockedIn = !!myRecord?.clock_in;
  const hasClockedOut = !!myRecord?.clock_out;

  const handleClockIn = async () => {
    setLoading(true);
    setMessage("");
    try {
      const res = await clockIn(userId);
      setMessage(`出勤: ${res.time}`);
      onUpdate();
    } catch (e: unknown) {
      setMessage(e instanceof Error ? e.message : "エラーが発生しました");
    } finally {
      setLoading(false);
    }
  };

  const handleClockOut = async () => {
    setLoading(true);
    setMessage("");
    try {
      const res = await clockOut(userId);
      setMessage(`退勤: ${res.time}`);
      onUpdate();
    } catch (e: unknown) {
      setMessage(e instanceof Error ? e.message : "エラーが発生しました");
    } finally {
      setLoading(false);
    }
  };

  const content = (
    <div className="flex flex-col h-full">
      {/* ヘッダー */}
      <div className="flex items-center justify-between px-4 py-4 border-b border-[var(--gray-200)]">
        <h2 className="font-bold text-lg text-[var(--navy)]">出退勤</h2>
        {onClose && (
          <button
            onClick={onClose}
            className="p-2 rounded hover:bg-[var(--gray-100)] text-xl"
          >
            ✕
          </button>
        )}
      </div>

      {/* 打刻ボタン */}
      <div className="p-4 space-y-3">
        <div className="text-center mb-4">
          <p className="text-sm text-[var(--gray-500)]">
            {new Date().toLocaleDateString("ja-JP", {
              year: "numeric",
              month: "long",
              day: "numeric",
              weekday: "short",
            })}
          </p>
        </div>

        <button
          onClick={handleClockIn}
          disabled={loading || hasClockedIn}
          className={`w-full font-bold rounded-xl transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
            isMobile ? "py-5 text-xl" : "py-3 text-base"
          } ${
            hasClockedIn
              ? "bg-[var(--gray-200)] text-[var(--gray-500)]"
              : "bg-[var(--success)] text-white hover:bg-green-600"
          }`}
        >
          {hasClockedIn ? `出勤済 ${myRecord?.clock_in}` : "出勤"}
        </button>

        <button
          onClick={handleClockOut}
          disabled={loading || !hasClockedIn || hasClockedOut}
          className={`w-full font-bold rounded-xl transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
            isMobile ? "py-5 text-xl" : "py-3 text-base"
          } ${
            hasClockedOut
              ? "bg-[var(--gray-200)] text-[var(--gray-500)]"
              : "bg-[var(--danger)] text-white hover:bg-red-600"
          }`}
        >
          {hasClockedOut ? `退勤済 ${myRecord?.clock_out}` : "退勤"}
        </button>

        {message && (
          <p className="text-center text-sm font-medium text-[var(--info)] mt-2">
            {message}
          </p>
        )}
      </div>

      {/* 本日の出勤状況 */}
      <div className="flex-1 overflow-y-auto px-4 pb-4">
        <p className="text-xs font-semibold text-[var(--gray-400)] uppercase mb-2">
          本日の出勤状況
        </p>
        {todayRecords.length === 0 && (
          <p className="text-sm text-[var(--gray-400)]">まだ打刻がありません</p>
        )}
        {todayRecords.map((r) => (
          <div
            key={r.id}
            className="flex items-center justify-between py-2 border-b border-[var(--gray-100)]"
          >
            <span className="text-sm font-medium">{r.display_name}</span>
            <div className="text-xs text-[var(--gray-500)] space-x-2">
              {r.clock_in && <span className="text-[var(--success)]">出 {r.clock_in}</span>}
              {r.clock_out && <span className="text-[var(--danger)]">退 {r.clock_out}</span>}
              {!r.clock_in && <span>未出勤</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );

  // モバイル: フルスクリーンオーバーレイ
  if (isMobile && onClose) {
    return (
      <div className="fixed inset-0 z-40 bg-white">{content}</div>
    );
  }

  // PC: サイドパネル
  return (
    <div className="w-72 border-l border-[var(--gray-200)] bg-white flex-shrink-0 hidden lg:flex flex-col">
      {content}
    </div>
  );
}
