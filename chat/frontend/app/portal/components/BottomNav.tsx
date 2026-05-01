"use client";

import { useState } from "react";
import { PageId } from "./Sidebar";
import {
  IconHome,
  IconChat,
  IconCalendar,
  IconClock,
  IconMoreHorizontal,
  IconWorkflow,
  IconBook,
} from "./icons";

interface BottomNavItem {
  id: PageId | "more";
  label: string;
  icon: React.ComponentType<{ className?: string; size?: number }>;
  badge?: number;
}

const BOTTOM_ITEMS: BottomNavItem[] = [
  { id: "home", label: "ホーム", icon: IconHome },
  { id: "chat", label: "チャット", icon: IconChat, badge: 3 },
  { id: "schedule", label: "スケジュール", icon: IconCalendar },
  { id: "attendance", label: "勤怠", icon: IconClock },
  { id: "more", label: "その他", icon: IconMoreHorizontal },
];

const MORE_ITEMS: { id: PageId; label: string; icon: React.ComponentType<{ className?: string; size?: number }> }[] = [
  { id: "workflow", label: "ワークフロー", icon: IconWorkflow },
  { id: "skills", label: "Factoryskill", icon: IconBook },
];

interface BottomNavProps {
  activePage: PageId;
  onNavigate: (page: PageId) => void;
}

export default function BottomNav({ activePage, onNavigate }: BottomNavProps) {
  const [showMore, setShowMore] = useState(false);

  return (
    <>
      {/* その他メニューのオーバーレイ */}
      {showMore && (
        <div className="lg:hidden fixed inset-0 z-40" onClick={() => setShowMore(false)}>
          <div className="absolute inset-0 bg-black/30" />
          <div
            className="absolute bottom-[var(--bottom-nav-height)] left-0 right-0 bg-white rounded-t-2xl shadow-2xl p-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="w-10 h-1 bg-[var(--border)] rounded-full mx-auto mb-4" />
            <div className="grid grid-cols-2 gap-3">
              {MORE_ITEMS.map((item) => (
                <button
                  key={item.id}
                  onClick={() => {
                    onNavigate(item.id);
                    setShowMore(false);
                  }}
                  className={`flex items-center gap-3 p-4 rounded-xl transition-colors ${
                    activePage === item.id
                      ? "bg-[var(--primary)] text-white"
                      : "bg-[var(--bg-warm)] text-[var(--text-primary)] hover:bg-[var(--border)]"
                  }`}
                >
                  <item.icon size={22} />
                  <span className="text-sm font-medium">{item.label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ボトムナビゲーション */}
      <nav
        className="lg:hidden fixed bottom-0 left-0 right-0 z-40 bg-white border-t border-[var(--border)] flex items-center justify-around"
        style={{ height: "var(--bottom-nav-height)" }}
      >
        {BOTTOM_ITEMS.map((item) => {
          const isMore = item.id === "more";
          const isActive = !isMore && activePage === item.id;
          const isMoreActive = isMore && MORE_ITEMS.some((m) => m.id === activePage);

          return (
            <button
              key={item.id}
              onClick={() => {
                if (isMore) {
                  setShowMore(!showMore);
                } else {
                  onNavigate(item.id as PageId);
                  setShowMore(false);
                }
              }}
              className={`flex flex-col items-center justify-center gap-0.5 flex-1 py-1 transition-colors relative ${
                isActive || isMoreActive
                  ? "text-[var(--primary)]"
                  : "text-[var(--text-tertiary)]"
              }`}
            >
              {(isActive || isMoreActive) && (
                <div className="absolute top-0 left-1/2 -translate-x-1/2 w-8 h-[3px] rounded-b bg-[var(--accent-orange)]" />
              )}
              <div className="relative">
                <item.icon size={22} />
                {item.badge && item.badge > 0 && (
                  <span className="absolute -top-1 -right-2 min-w-[16px] h-4 px-1 bg-[var(--accent-orange)] text-white text-[10px] font-bold rounded-full flex items-center justify-center">
                    {item.badge}
                  </span>
                )}
              </div>
              <span className="text-[10px] font-medium">{item.label}</span>
            </button>
          );
        })}
      </nav>
    </>
  );
}
