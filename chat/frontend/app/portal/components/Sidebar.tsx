"use client";

import { useState } from "react";
import {
  IconHome,
  IconChat,
  IconCalendar,
  IconWorkflow,
  IconClock,
  IconBook,
} from "./icons";

export type PageId =
  | "home"
  | "chat"
  | "schedule"
  | "workflow"
  | "attendance"
  | "skills";

interface NavItem {
  id: PageId;
  label: string;
  icon: React.ComponentType<{ className?: string; size?: number }>;
  badge?: number;
}

const NAV_ITEMS: NavItem[] = [
  { id: "home", label: "掲示板", icon: IconHome },
  { id: "chat", label: "チャット", icon: IconChat, badge: 3 },
  { id: "schedule", label: "スケジュール", icon: IconCalendar },
  { id: "workflow", label: "ワークフロー", icon: IconWorkflow, badge: 1 },
  { id: "attendance", label: "勤怠管理", icon: IconClock },
  { id: "skills", label: "Factoryskill", icon: IconBook },
];

interface SidebarProps {
  activePage: PageId;
  onNavigate: (page: PageId) => void;
}

export default function Sidebar({ activePage, onNavigate }: SidebarProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <aside
      onMouseEnter={() => setExpanded(true)}
      onMouseLeave={() => setExpanded(false)}
      className="hidden lg:flex flex-col fixed left-0 top-[var(--header-height)] bottom-0 z-30 bg-[var(--primary)] transition-all duration-200 ease-in-out"
      style={{ width: expanded ? "var(--sidebar-expanded)" : "var(--sidebar-width)" }}
    >
      {/* ナビゲーション */}
      <nav className="flex-1 py-3 flex flex-col gap-1">
        {NAV_ITEMS.map((item) => {
          const isActive = activePage === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onNavigate(item.id)}
              className={`relative flex items-center gap-3 mx-2 rounded-lg transition-colors overflow-hidden ${
                isActive
                  ? "bg-[var(--primary-light)] text-white"
                  : "text-[var(--text-on-dark-muted)] hover:bg-[var(--primary-light)] hover:text-white"
              }`}
              style={{ height: 44, paddingLeft: 14, paddingRight: 14 }}
              title={!expanded ? item.label : undefined}
            >
              {/* アクティブインジケーター */}
              {isActive && (
                <div className="absolute left-0 top-2 bottom-2 w-[3px] rounded-r bg-[var(--accent-orange)]" />
              )}

              <item.icon size={20} className="flex-shrink-0" />

              {/* ラベル（展開時のみ表示） */}
              <span
                className={`text-sm font-medium whitespace-nowrap transition-opacity duration-200 ${
                  expanded ? "opacity-100" : "opacity-0 w-0"
                }`}
              >
                {item.label}
              </span>

              {/* バッジ */}
              {item.badge && item.badge > 0 && (
                <span
                  className={`absolute flex items-center justify-center text-[10px] font-bold text-white bg-[var(--accent-orange)] rounded-full ${
                    expanded
                      ? "right-3 min-w-[20px] h-5 px-1.5"
                      : "top-1 right-1 min-w-[16px] h-4 px-1"
                  }`}
                >
                  {item.badge}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* ロゴ / ブランド */}
      <div className="p-3 border-t border-[var(--primary-light)]">
        <div className="flex items-center gap-3 px-2">
          <div className="w-8 h-8 rounded bg-[var(--accent-orange)] flex items-center justify-center text-white font-bold text-xs flex-shrink-0">
            F
          </div>
          <span
            className={`text-xs text-[var(--text-on-dark-muted)] whitespace-nowrap transition-opacity duration-200 ${
              expanded ? "opacity-100" : "opacity-0 w-0"
            }`}
          >
            Factory Platform
          </span>
        </div>
      </div>
    </aside>
  );
}
