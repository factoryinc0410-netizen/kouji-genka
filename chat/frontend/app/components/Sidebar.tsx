"use client";

import { Channel } from "../lib/api";

interface SidebarProps {
  channels: Channel[];
  activeChannelId: number | null;
  onSelectChannel: (id: number) => void;
  onClose?: () => void;
  currentUserName: string;
}

export default function Sidebar({
  channels,
  activeChannelId,
  onSelectChannel,
  onClose,
  currentUserName,
}: SidebarProps) {
  return (
    <div className="flex flex-col h-full bg-[var(--navy)] text-white">
      {/* ヘッダー */}
      <div className="flex items-center justify-between px-4 py-4 border-b border-[var(--navy-light)]">
        <div>
          <h1 className="text-lg font-bold tracking-wide">Factory Chat</h1>
          <p className="text-xs text-[var(--gray-400)] mt-0.5">{currentUserName}</p>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="lg:hidden p-2 rounded hover:bg-[var(--navy-light)] text-xl leading-none"
            aria-label="閉じる"
          >
            ✕
          </button>
        )}
      </div>

      {/* チャンネル一覧 */}
      <div className="flex-1 overflow-y-auto py-2">
        <p className="px-4 py-2 text-xs font-semibold text-[var(--gray-400)] uppercase tracking-wider">
          チャンネル
        </p>
        {channels.map((ch) => (
          <button
            key={ch.id}
            onClick={() => {
              onSelectChannel(ch.id);
              onClose?.();
            }}
            className={`w-full text-left px-4 py-3 flex items-center gap-3 transition-colors ${
              activeChannelId === ch.id
                ? "bg-[var(--navy-light)] border-l-4 border-[var(--accent)]"
                : "hover:bg-[var(--navy-light)] border-l-4 border-transparent"
            }`}
          >
            <span className="text-lg">#</span>
            <div className="min-w-0 flex-1">
              <p className="font-medium truncate text-sm">{ch.name}</p>
              {ch.project_id && (
                <p className="text-xs text-[var(--gray-400)] truncate">
                  {ch.project_id}
                </p>
              )}
            </div>
            <span className="text-xs text-[var(--gray-400)]">
              {ch.member_count}人
            </span>
          </button>
        ))}
      </div>

      {/* フッター */}
      <div className="p-4 border-t border-[var(--navy-light)]">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-[var(--accent)] flex items-center justify-center text-[var(--navy)] font-bold text-sm">
            {currentUserName.charAt(0)}
          </div>
          <span className="text-sm">{currentUserName}</span>
        </div>
      </div>
    </div>
  );
}
