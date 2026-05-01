"use client";

import { IconSearch } from "../icons";

interface PlaceholderPageProps {
  title: string;
  description: string;
  icon: React.ComponentType<{ className?: string; size?: number }>;
  features: string[];
}

export default function PlaceholderPage({
  title,
  description,
  icon: Icon,
  features,
}: PlaceholderPageProps) {
  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-3xl mx-auto px-4 py-8">
        <div className="text-center mb-8">
          <div className="w-16 h-16 rounded-2xl bg-[var(--primary)]/10 flex items-center justify-center mx-auto mb-4 text-[var(--primary)]">
            <Icon size={32} />
          </div>
          <h1 className="text-xl font-bold text-[var(--text-primary)]">{title}</h1>
          <p className="text-sm text-[var(--text-secondary)] mt-2">{description}</p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {features.map((feature, i) => (
            <div
              key={i}
              className="bg-white rounded-xl p-5 border border-[var(--border-light)] shadow-sm"
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-[var(--bg-warm)] flex items-center justify-center text-sm font-bold text-[var(--primary)]">
                  {i + 1}
                </div>
                <p className="text-sm font-medium text-[var(--text-primary)]">{feature}</p>
              </div>
            </div>
          ))}
        </div>

        <div className="mt-8 p-6 bg-[var(--bg-warm)] rounded-xl border border-dashed border-[var(--border)] text-center">
          <p className="text-sm text-[var(--text-secondary)]">
            この機能は今後実装予定です
          </p>
        </div>
      </div>
    </div>
  );
}

// ── 各ページのエクスポート ──────────────────────────────────

export function ChatPage() {
  return (
    <PlaceholderPage
      title="チャット"
      description="現場と事務所をリアルタイムでつなぐコミュニケーション"
      icon={({ className, size }) => (
        <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      )}
      features={[
        "工事案件ごとのチャンネル",
        "写真・ファイル添付",
        "スレッド返信",
        "メンション通知",
      ]}
    />
  );
}

export function SchedulePage() {
  return (
    <PlaceholderPage
      title="スケジュール"
      description="工事日程と個人予定を一元管理"
      icon={({ className, size }) => (
        <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
          <line x1="16" y1="2" x2="16" y2="6" />
          <line x1="8" y1="2" x2="8" y2="6" />
          <line x1="3" y1="10" x2="21" y2="10" />
        </svg>
      )}
      features={[
        "月間/週間カレンダー表示",
        "工事案件ごとの色分け",
        "打合せ・検査日程の管理",
        "予定の共有と通知",
      ]}
    />
  );
}

export function WorkflowPage() {
  return (
    <PlaceholderPage
      title="ワークフロー"
      description="申請・承認をデジタルで効率化"
      icon={({ className, size }) => (
        <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="9 11 12 14 22 4" />
          <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
        </svg>
      )}
      features={[
        "休暇・経費の申請",
        "多段階承認フロー",
        "ステータス追跡",
        "申請履歴の一覧",
      ]}
    />
  );
}

export function AttendancePage() {
  return (
    <PlaceholderPage
      title="勤怠管理"
      description="出退勤記録と労働時間の管理"
      icon={({ className, size }) => (
        <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10" />
          <polyline points="12 6 12 12 16 14" />
        </svg>
      )}
      features={[
        "ワンタップ出退勤打刻",
        "月間勤怠表",
        "残業時間の自動計算",
        "管理者向け一覧ビュー",
      ]}
    />
  );
}

export function SkillsPage() {
  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto px-4 py-5 space-y-5">
        {/* 検索バー */}
        <div className="relative">
          <IconSearch size={18} className="absolute left-4 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)]" />
          <input
            type="text"
            placeholder="施工ノウハウ・マニュアルを検索..."
            className="w-full pl-11 pr-4 py-3 bg-white rounded-xl border border-[var(--border)] text-sm focus:outline-none focus:border-[var(--primary-light)] focus:ring-1 focus:ring-[var(--primary-light)]"
          />
        </div>

        {/* カテゴリタブ */}
        <div className="flex gap-2 overflow-x-auto pb-1">
          {["すべて", "施工ノウハウ", "安全管理", "品質管理", "資格情報"].map((tab, i) => (
            <button
              key={tab}
              className={`px-4 py-2 rounded-full text-sm font-medium whitespace-nowrap transition-colors ${
                i === 0
                  ? "bg-[var(--primary)] text-white"
                  : "bg-white text-[var(--text-secondary)] border border-[var(--border)] hover:bg-[var(--bg-warm)]"
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        {/* カードグリッド */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {[
            { title: "コンクリート打設手順（夏季）", category: "施工ノウハウ", author: "田中 太郎", date: "2026-03-20", tags: ["コンクリート", "夏季"] },
            { title: "高所作業時の安全チェックリスト", category: "安全管理", author: "佐藤 花子", date: "2026-03-18", tags: ["高所", "チェックリスト"] },
            { title: "鉄筋組立の品質管理ポイント", category: "品質管理", author: "鈴木 一郎", date: "2026-03-15", tags: ["鉄筋", "品質"] },
            { title: "型枠脱型のタイミング判断", category: "施工ノウハウ", author: "田中 太郎", date: "2026-03-10", tags: ["型枠", "養生"] },
            { title: "KY活動の実施手順", category: "安全管理", author: "佐藤 花子", date: "2026-03-08", tags: ["KY活動", "朝礼"] },
            { title: "舗装工事の転圧管理基準", category: "品質管理", author: "鈴木 一郎", date: "2026-03-05", tags: ["舗装", "転圧"] },
          ].map((skill, i) => (
            <div
              key={i}
              className="bg-white rounded-xl overflow-hidden border border-[var(--border-light)] shadow-sm hover:shadow-md transition-shadow cursor-pointer group"
            >
              {/* サムネイルプレースホルダー */}
              <div className="h-32 bg-gradient-to-br from-[var(--primary)]/5 to-[var(--primary)]/15 flex items-center justify-center">
                <div className="w-12 h-12 rounded-lg bg-[var(--primary)]/10 flex items-center justify-center text-[var(--primary)]">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                    <circle cx="8.5" cy="8.5" r="1.5" />
                    <polyline points="21 15 16 10 5 21" />
                  </svg>
                </div>
              </div>
              <div className="p-4">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[10px] font-bold text-[var(--primary)] bg-[var(--primary)]/10 px-2 py-0.5 rounded">
                    {skill.category}
                  </span>
                </div>
                <h3 className="text-sm font-bold text-[var(--text-primary)] group-hover:text-[var(--primary-light)] transition-colors line-clamp-2">
                  {skill.title}
                </h3>
                <div className="flex items-center gap-2 mt-2 flex-wrap">
                  {skill.tags.map((tag) => (
                    <span key={tag} className="text-[10px] text-[var(--text-tertiary)] bg-[var(--bg-warm)] px-1.5 py-0.5 rounded">
                      #{tag}
                    </span>
                  ))}
                </div>
                <div className="flex items-center justify-between mt-3 pt-2 border-t border-[var(--border-light)]">
                  <span className="text-[11px] text-[var(--text-tertiary)]">{skill.author}</span>
                  <span className="text-[11px] text-[var(--text-tertiary)]">{skill.date}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
