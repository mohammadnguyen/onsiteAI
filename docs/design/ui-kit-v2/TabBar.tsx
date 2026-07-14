import React from "react";
import { cn, PlusIcon } from "./ui-kit";

/* ============================================================
   TabBar — Home · Jobs · [➕] · Labour
   · 中央 ➕ 56px 凸起圆钮,常驻实心蓝,无文字标签,直达记账页
   · 记账页打开时 active 传 null(其余 tab 全部熄灭)
   · 页面滚动容器记得留底部空间:
     pb-[calc(78px+env(safe-area-inset-bottom))]
   ============================================================ */

export type TabKey = "home" | "jobs" | "labour";

export function TabBar({
  active,
  onTab,
  onAdd,
}: {
  active: TabKey | null;
  onTab: (tab: TabKey) => void;
  onAdd: () => void;
}) {
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-white pb-[env(safe-area-inset-bottom)]">
      <div className="relative mx-auto flex h-[62px] max-w-md items-stretch px-1.5">
        <div className="flex flex-1">
          <TabButton tab="home" label="Home" active={active} onTab={onTab} icon={<HomeIcon className="h-[21px] w-[21px]" />} />
          <TabButton tab="jobs" label="Jobs" active={active} onTab={onTab} icon={<JobsIcon className="h-[21px] w-[21px]" />} />
        </div>

        {/* 给 FAB 留出的空位 */}
        <div className="w-[76px]" aria-hidden="true" />

        <div className="flex flex-1">
          <TabButton tab="labour" label="Labour" active={active} onTab={onTab} icon={<LabourIcon className="h-[21px] w-[21px]" />} />
        </div>

        <button
          type="button"
          onClick={onAdd}
          aria-label="记一笔支出"
          className={cn(
            "absolute left-1/2 top-0 flex h-14 w-14 -translate-x-1/2 -translate-y-4",
            "items-center justify-center rounded-full bg-primary text-white",
            "shadow-[0_8px_18px_rgba(37,99,235,0.38),0_2px_4px_rgba(37,99,235,0.3)]",
            "transition-transform active:scale-95",
          )}
        >
          <PlusIcon className="h-[26px] w-[26px]" />
        </button>
      </div>
    </nav>
  );
}

function TabButton({
  tab,
  label,
  icon,
  active,
  onTab,
}: {
  tab: TabKey;
  label: string;
  icon: React.ReactNode;
  active: TabKey | null;
  onTab: (tab: TabKey) => void;
}) {
  const on = active === tab;
  return (
    <button
      type="button"
      onClick={() => onTab(tab)}
      aria-current={on ? "page" : undefined}
      className={cn(
        "flex flex-1 flex-col items-center justify-center gap-[3px] text-[10px] font-semibold",
        on ? "text-primary" : "text-ink-3",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

/* ---------------- icons ---------------- */

type IconProps = { className?: string };

const HomeIcon = ({ className }: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" className={className}>
    <path d="M3 10.8 12 3.4l9 7.4" />
    <path d="M5.3 9.2V20h13.4V9.2" />
    <path d="M9.8 20v-5.6h4.4V20" />
  </svg>
);

const JobsIcon = ({ className }: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9} className={className}>
    <rect x="3" y="7.5" width="18" height="12.5" rx="2.5" />
    <path d="M8.5 7.5V6a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v1.5" />
  </svg>
);

const LabourIcon = ({ className }: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" className={className}>
    <circle cx="9" cy="8" r="3.2" />
    <path d="M3.4 19.2c.6-3.1 2.9-4.7 5.6-4.7s5 1.6 5.6 4.7" />
    <circle cx="17.2" cy="9" r="2.5" />
    <path d="M15.8 14.8c2.3.3 4 1.7 4.7 4.4" />
  </svg>
);
