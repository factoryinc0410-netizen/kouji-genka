"use client";

import { IconBell, IconUser } from "./icons";

interface HeaderProps {
  title: string;
  userName: string;
}

export default function Header({ title, userName }: HeaderProps) {
  return (
    <header
      className="fixed top-0 left-0 right-0 z-40 flex items-center justify-between bg-[var(--primary-dark)] text-white"
      style={{ height: "var(--header-height)" }}
    >
      {/* 左: ロゴ＋タイトル */}
      <div className="flex items-center gap-3 pl-4 lg:pl-5">
        <div className="w-8 h-8 rounded bg-[var(--accent-orange)] flex items-center justify-center font-bold text-sm">
          F
        </div>
        <div className="hidden sm:block">
          <h1 className="text-sm font-bold leading-tight">Factory Platform</h1>
          <p className="text-[10px] text-[var(--text-on-dark-muted)] leading-tight">
            風景をつくる。
          </p>
        </div>
      </div>

      {/* 中央: ページタイトル（モバイルのみ） */}
      <div className="lg:hidden absolute left-1/2 -translate-x-1/2">
        <span className="text-sm font-bold">{title}</span>
      </div>

      {/* 右: 通知＋ユーザー */}
      <div className="flex items-center gap-2 pr-4">
        <button className="relative p-2 rounded-lg hover:bg-white/10 transition-colors">
          <IconBell size={20} />
          <span className="absolute top-1 right-1 w-2 h-2 bg-[var(--accent-orange)] rounded-full" />
        </button>
        <div className="hidden sm:flex items-center gap-2 pl-2 border-l border-white/20 ml-1">
          <div className="w-7 h-7 rounded-full bg-[var(--primary-light)] flex items-center justify-center">
            <IconUser size={14} />
          </div>
          <span className="text-xs">{userName}</span>
        </div>
      </div>
    </header>
  );
}
