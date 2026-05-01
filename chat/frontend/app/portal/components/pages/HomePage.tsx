"use client";

import { IconChevronRight, IconChat, IconCalendar, IconCamera } from "../icons";

// ── デモデータ ─────────────────────────────────────────────

const ANNOUNCEMENTS = [
  {
    id: 1,
    category: "全社",
    categoryColor: "bg-[var(--primary)]",
    title: "今週金曜に安全会議を実施します",
    body: "各現場代理人は必ず出席をお願いします。議題は第三四半期の安全実績と秋季作業の注意点です。",
    author: "佐藤 花子",
    date: "2026-03-27",
    isNew: true,
  },
  {
    id: 2,
    category: "現場",
    categoryColor: "bg-[var(--accent-orange)]",
    title: "○○橋梁補修工事：来週より夜間作業開始",
    body: "交通規制計画書を確認の上、4/1(水)より夜間作業に移行します。シフト表を添付しますのでご確認ください。",
    author: "田中 太郎",
    date: "2026-03-26",
    isNew: true,
  },
  {
    id: 3,
    category: "安全",
    categoryColor: "bg-[var(--danger)]",
    title: "熱中症対策の早期実施について",
    body: "例年より気温上昇が早い予報のため、4月上旬より塩飴・経口補水液の現場配備を開始します。",
    author: "佐藤 花子",
    date: "2026-03-25",
    isNew: false,
  },
  {
    id: 4,
    category: "総務",
    categoryColor: "bg-[var(--text-secondary)]",
    title: "年度末の経費精算提出期限について",
    body: "令和7年度分の経費精算は3/31(火)までに提出してください。",
    author: "佐藤 花子",
    date: "2026-03-24",
    isNew: false,
  },
];

const TODAY_SCHEDULE = [
  { id: 1, time: "08:00", title: "朝礼（○○橋梁補修工事）", project: "PRJ-2024-001", color: "border-[var(--primary-light)]" },
  { id: 2, time: "10:00", title: "安全パトロール", project: "PRJ-2024-002", color: "border-[var(--accent-orange)]" },
  { id: 3, time: "14:00", title: "発注者定例打合せ", project: "PRJ-2024-001", color: "border-[var(--primary-light)]" },
];

const UNREAD_CHATS = [
  { id: 1, channel: "○○橋梁補修工事", sender: "鈴木 一郎", message: "コンクリート打設の写真を共有します", time: "07:45" },
  { id: 2, channel: "全体連絡", sender: "佐藤 花子", message: "安全会議の資料をアップしました", time: "07:30" },
  { id: 3, channel: "△△道路改良工事", sender: "田中 太郎", message: "本日の段取り確認をお願いします", time: "昨日" },
];

// ── メインコンポーネント ────────────────────────────────────

export default function HomePage() {
  const now = new Date();
  const timeStr = now.toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" });
  const dateStr = now.toLocaleDateString("ja-JP", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "short",
  });

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto px-4 py-5 space-y-5">

        {/* ── 出退勤カード ─────────────────────────── */}
        <section className="bg-[var(--primary)] rounded-2xl p-5 text-white shadow-lg">
          <div className="flex items-center justify-between mb-4">
            <div>
              <p className="text-xs text-[var(--text-on-dark-muted)]">{dateStr}</p>
              <p className="text-3xl font-bold tracking-tight mt-0.5">{timeStr}</p>
            </div>
            <div className="text-right">
              <p className="text-xs text-[var(--text-on-dark-muted)]">ステータス</p>
              <p className="text-sm font-medium text-[var(--accent-orange)]">未出勤</p>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <button className="py-4 rounded-xl bg-[var(--accent-green)] hover:brightness-110 transition-all text-white font-bold text-lg shadow-md active:scale-[0.98]">
              出勤
            </button>
            <button className="py-4 rounded-xl bg-white/10 border border-white/20 text-white/50 font-bold text-lg cursor-not-allowed">
              退勤
            </button>
          </div>
        </section>

        {/* ── クイックアクション（スマホで特に重要） ───── */}
        <section className="grid grid-cols-3 gap-3">
          <button className="flex flex-col items-center gap-2 bg-white rounded-xl p-4 shadow-sm border border-[var(--border-light)] hover:shadow-md transition-shadow">
            <div className="w-10 h-10 rounded-full bg-[var(--accent-orange)]/10 flex items-center justify-center text-[var(--accent-orange)]">
              <IconCamera size={20} />
            </div>
            <span className="text-xs font-medium text-[var(--text-primary)]">写真報告</span>
          </button>
          <button className="flex flex-col items-center gap-2 bg-white rounded-xl p-4 shadow-sm border border-[var(--border-light)] hover:shadow-md transition-shadow">
            <div className="w-10 h-10 rounded-full bg-[var(--primary)]/10 flex items-center justify-center text-[var(--primary)]">
              <IconChat size={20} />
            </div>
            <span className="text-xs font-medium text-[var(--text-primary)]">チャット</span>
          </button>
          <button className="flex flex-col items-center gap-2 bg-white rounded-xl p-4 shadow-sm border border-[var(--border-light)] hover:shadow-md transition-shadow">
            <div className="w-10 h-10 rounded-full bg-[var(--accent-green)]/10 flex items-center justify-center text-[var(--accent-green)]">
              <IconCalendar size={20} />
            </div>
            <span className="text-xs font-medium text-[var(--text-primary)]">予定確認</span>
          </button>
        </section>

        {/* ── 2カラム: お知らせ + サイドカラム ──────── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">

          {/* お知らせ（2/3幅） */}
          <section className="lg:col-span-2 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-bold text-[var(--text-primary)]">お知らせ</h2>
              <button className="text-xs text-[var(--primary-light)] hover:underline flex items-center gap-0.5">
                すべて見る <IconChevronRight size={14} />
              </button>
            </div>
            <div className="space-y-3">
              {ANNOUNCEMENTS.map((a) => (
                <article
                  key={a.id}
                  className="bg-white rounded-xl p-4 shadow-sm border border-[var(--border-light)] hover:shadow-md transition-shadow cursor-pointer group"
                >
                  <div className="flex items-start gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1.5">
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-bold text-white ${a.categoryColor}`}
                        >
                          {a.category}
                        </span>
                        {a.isNew && (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold text-[var(--accent-orange)] bg-[var(--accent-orange)]/10">
                            NEW
                          </span>
                        )}
                        <span className="text-[11px] text-[var(--text-tertiary)] ml-auto">
                          {a.date}
                        </span>
                      </div>
                      <h3 className="text-sm font-bold text-[var(--text-primary)] group-hover:text-[var(--primary-light)] transition-colors">
                        {a.title}
                      </h3>
                      <p className="text-xs text-[var(--text-secondary)] mt-1 line-clamp-2">
                        {a.body}
                      </p>
                      <p className="text-[11px] text-[var(--text-tertiary)] mt-2">
                        {a.author}
                      </p>
                    </div>
                    <IconChevronRight
                      size={16}
                      className="text-[var(--text-tertiary)] group-hover:text-[var(--primary-light)] transition-colors mt-1 flex-shrink-0"
                    />
                  </div>
                </article>
              ))}
            </div>
          </section>

          {/* サイドカラム（1/3幅） */}
          <aside className="space-y-5">

            {/* 今日の予定 */}
            <section>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-base font-bold text-[var(--text-primary)]">今日の予定</h2>
                <span className="text-xs text-[var(--text-tertiary)]">{TODAY_SCHEDULE.length}件</span>
              </div>
              <div className="space-y-2">
                {TODAY_SCHEDULE.map((s) => (
                  <div
                    key={s.id}
                    className={`bg-white rounded-lg p-3 shadow-sm border-l-[3px] ${s.color} cursor-pointer hover:shadow-md transition-shadow`}
                  >
                    <p className="text-[11px] font-bold text-[var(--text-secondary)]">{s.time}</p>
                    <p className="text-sm font-medium text-[var(--text-primary)] mt-0.5">{s.title}</p>
                    <p className="text-[10px] text-[var(--text-tertiary)] mt-0.5">{s.project}</p>
                  </div>
                ))}
              </div>
            </section>

            {/* 未読チャット */}
            <section>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-base font-bold text-[var(--text-primary)]">未読チャット</h2>
                <span className="px-2 py-0.5 bg-[var(--accent-orange)] text-white text-[10px] font-bold rounded-full">
                  {UNREAD_CHATS.length}
                </span>
              </div>
              <div className="space-y-2">
                {UNREAD_CHATS.map((c) => (
                  <div
                    key={c.id}
                    className="bg-white rounded-lg p-3 shadow-sm border border-[var(--border-light)] cursor-pointer hover:shadow-md transition-shadow"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <p className="text-xs font-bold text-[var(--primary)]">#{c.channel}</p>
                      <span className="text-[10px] text-[var(--text-tertiary)]">{c.time}</span>
                    </div>
                    <p className="text-xs text-[var(--text-secondary)] truncate">
                      <span className="font-medium text-[var(--text-primary)]">{c.sender}: </span>
                      {c.message}
                    </p>
                  </div>
                ))}
              </div>
            </section>
          </aside>
        </div>
      </div>
    </div>
  );
}
