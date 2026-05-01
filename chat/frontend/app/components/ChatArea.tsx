"use client";

import { useEffect, useRef, useState } from "react";
import { Message, Channel, attachmentUrl } from "../lib/api";
import MessageInput from "./MessageInput";

interface ChatAreaProps {
  channel: Channel | null;
  messages: Message[];
  onSendMessage: (content: string) => void;
  onSendWithAttachment: (content: string, file: File) => void;
  onOpenSidebar: () => void;
  onOpenAttendance: () => void;
  isMobile: boolean;
}

function formatTime(dateStr: string): string {
  try {
    const d = new Date(dateStr);
    return d.toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return dateStr;
  }
}

function formatDateLabel(dateStr: string): string {
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString("ja-JP", {
      year: "numeric",
      month: "long",
      day: "numeric",
      weekday: "short",
    });
  } catch {
    return dateStr;
  }
}

function getInitial(name: string): string {
  return name.charAt(0);
}

const AVATAR_COLORS = [
  "bg-blue-500",
  "bg-green-500",
  "bg-purple-500",
  "bg-orange-500",
  "bg-pink-500",
  "bg-teal-500",
];

function avatarColor(userId: number): string {
  return AVATAR_COLORS[userId % AVATAR_COLORS.length];
}

export default function ChatArea({
  channel,
  messages,
  onSendMessage,
  onSendWithAttachment,
  onOpenSidebar,
  onOpenAttendance,
  isMobile,
}: ChatAreaProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  if (!channel) {
    return (
      <div className="flex-1 flex items-center justify-center bg-[var(--gray-50)]">
        <div className="text-center text-[var(--gray-400)]">
          <p className="text-5xl mb-4">💬</p>
          <p className="text-lg font-medium">チャンネルを選択してください</p>
        </div>
      </div>
    );
  }

  // 日付ごとにメッセージをグループ化
  let lastDate = "";

  return (
    <div className="flex-1 flex flex-col h-full bg-white">
      {/* ヘッダー */}
      <div className="flex items-center gap-3 px-4 py-3 bg-white border-b border-[var(--gray-200)] shadow-sm">
        {isMobile && (
          <button
            onClick={onOpenSidebar}
            className="p-2 -ml-2 rounded-lg hover:bg-[var(--gray-100)] text-xl"
            aria-label="メニュー"
          >
            ☰
          </button>
        )}
        <div className="flex-1 min-w-0">
          <h2 className="font-bold text-[var(--navy)] truncate">
            # {channel.name}
          </h2>
          {channel.description && (
            <p className="text-xs text-[var(--gray-500)] truncate">
              {channel.description}
            </p>
          )}
        </div>
        {isMobile && (
          <button
            onClick={onOpenAttendance}
            className="px-3 py-2 bg-[var(--accent)] text-[var(--navy)] rounded-lg font-bold text-sm hover:bg-[var(--accent-hover)] transition-colors"
          >
            出退勤
          </button>
        )}
      </div>

      {/* メッセージ一覧 */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {messages.length === 0 && (
          <div className="text-center text-[var(--gray-400)] py-12">
            <p>まだメッセージがありません</p>
            <p className="text-sm mt-1">最初のメッセージを送信しましょう</p>
          </div>
        )}
        {messages.map((msg) => {
          const msgDate = msg.created_at.split("T")[0].split(" ")[0];
          let showDate = false;
          if (msgDate !== lastDate) {
            showDate = true;
            lastDate = msgDate;
          }
          return (
            <div key={msg.id}>
              {showDate && (
                <div className="flex items-center gap-3 my-4">
                  <div className="flex-1 h-px bg-[var(--gray-200)]" />
                  <span className="text-xs text-[var(--gray-400)] font-medium">
                    {formatDateLabel(msg.created_at)}
                  </span>
                  <div className="flex-1 h-px bg-[var(--gray-200)]" />
                </div>
              )}
              <div className="flex gap-3 py-2 hover:bg-[var(--gray-50)] -mx-2 px-2 rounded-lg transition-colors group">
                {/* アバター */}
                <div
                  className={`w-10 h-10 rounded-full flex-shrink-0 flex items-center justify-center text-white font-bold text-sm ${avatarColor(msg.user_id)}`}
                >
                  {getInitial(msg.display_name)}
                </div>
                {/* 本文 */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline gap-2">
                    <span className="font-bold text-sm text-[var(--navy)]">
                      {msg.display_name}
                    </span>
                    <span className="text-xs text-[var(--gray-400)]">
                      {formatTime(msg.created_at)}
                    </span>
                  </div>
                  {msg.content && (
                    <p className="text-sm text-[var(--gray-600)] mt-1 whitespace-pre-wrap break-words">
                      {msg.content}
                    </p>
                  )}
                  {/* 添付画像 */}
                  {msg.attachments.length > 0 && (
                    <div className="flex flex-wrap gap-2 mt-2">
                      {msg.attachments.map((att) => (
                        <button
                          key={att.id}
                          onClick={() => setLightboxSrc(attachmentUrl(att.file_path))}
                          className="block rounded-lg overflow-hidden border border-[var(--gray-200)] hover:border-[var(--info)] transition-colors max-w-xs"
                        >
                          <img
                            src={attachmentUrl(att.file_path)}
                            alt={att.file_name}
                            className="max-h-48 object-cover"
                            loading="lazy"
                          />
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>

      {/* メッセージ入力 */}
      <MessageInput
        onSend={onSendMessage}
        onSendWithFile={onSendWithAttachment}
        isMobile={isMobile}
        channelName={channel.name}
      />

      {/* 画像ライトボックス */}
      {lightboxSrc && (
        <div
          className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4"
          onClick={() => setLightboxSrc(null)}
        >
          <img
            src={lightboxSrc}
            alt="拡大画像"
            className="max-w-full max-h-full object-contain rounded-lg"
          />
          <button
            onClick={() => setLightboxSrc(null)}
            className="absolute top-4 right-4 text-white text-3xl hover:text-[var(--accent)]"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
}
