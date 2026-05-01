"use client";

import { useState, useEffect } from "react";
import Header from "./portal/components/Header";
import Sidebar, { PageId } from "./portal/components/Sidebar";
import BottomNav from "./portal/components/BottomNav";
import HomePage from "./portal/components/pages/HomePage";
import {
  ChatPage,
  SchedulePage,
  WorkflowPage,
  AttendancePage,
  SkillsPage,
} from "./portal/components/pages/PlaceholderPage";

const PAGE_TITLES: Record<PageId, string> = {
  home: "掲示板",
  chat: "チャット",
  schedule: "スケジュール",
  workflow: "ワークフロー",
  attendance: "勤怠管理",
  skills: "Factoryskill",
};

function PageContent({ page }: { page: PageId }) {
  switch (page) {
    case "home":
      return <HomePage />;
    case "chat":
      return <ChatPage />;
    case "schedule":
      return <SchedulePage />;
    case "workflow":
      return <WorkflowPage />;
    case "attendance":
      return <AttendancePage />;
    case "skills":
      return <SkillsPage />;
  }
}

export default function App() {
  const [activePage, setActivePage] = useState<PageId>("home");
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 1024);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  return (
    <div className="h-full flex flex-col bg-[var(--bg-page)]">
      {/* ヘッダー */}
      <Header title={PAGE_TITLES[activePage]} userName="田中 太郎" />

      {/* サイドバー（PC） */}
      <Sidebar activePage={activePage} onNavigate={setActivePage} />

      {/* メインコンテンツ */}
      <main
        className="flex-1 overflow-hidden"
        style={{
          marginTop: "var(--header-height)",
          marginLeft: isMobile ? 0 : "var(--sidebar-width)",
          marginBottom: isMobile ? "var(--bottom-nav-height)" : 0,
        }}
      >
        <PageContent page={activePage} />
      </main>

      {/* ボトムナビ（スマホ） */}
      <BottomNav activePage={activePage} onNavigate={setActivePage} />
    </div>
  );
}
