import React from "react";

/* ================= helpers ================= */

export const cn = (...xs: Array<string | false | null | undefined>) =>
  xs.filter(Boolean).join(" ");

/** $1,234.56(dp=2)或 $1,234(dp=0)。narrowSymbol 保证显示 "$" 而非 "A$"。 */
export const money = (n: number, dp: 0 | 2 = 2) =>
  n.toLocaleString("en-AU", {
    style: "currency",
    currency: "AUD",
    currencyDisplay: "narrowSymbol",
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  });

/* ================= tone(数据状态) ================= */

export type Tone = "ok" | "warn" | "bad";

/** 预算阈值:<70 绿 · 70–90 琥珀 · ≥90 红 */
export function budgetTone(pctUsed: number): Tone {
  if (pctUsed >= 90) return "bad";
  if (pctUsed >= 70) return "warn";
  return "ok";
}

const toneText: Record<Tone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
};
const toneFill: Record<Tone, string> = {
  ok: "bg-ok",
  warn: "bg-warn-fill",
  bad: "bg-bad",
};

/* ================= margin 口径(全部基于 ex-GST) ================= */

/** 预计 margin = (ex-GST 收入 − 总预算) ÷ ex-GST 收入 */
export const projectedMarginPct = (revenueExGst: number, budgetExGst: number) =>
  revenueExGst > 0 ? ((revenueExGst - budgetExGst) / revenueExGst) * 100 : 0;

/** 到目前 margin = (ex-GST 收入 − 已花成本) ÷ ex-GST 收入(仅参考,持续走低) */
export const marginToDatePct = (revenueExGst: number, spentExGst: number) =>
  revenueExGst > 0 ? ((revenueExGst - spentExGst) / revenueExGst) * 100 : 0;

/* ================= icons(内联,免依赖) ================= */

type IconProps = { className?: string };

export const PlusIcon = ({ className }: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" className={className}>
    <path d="M12 5v14M5 12h14" />
  </svg>
);

export const ChevronRightIcon = ({ className }: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" className={className}>
    <path d="m9 5 7 7-7 7" />
  </svg>
);

export const AlertIcon = ({ className }: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={className}>
    <path d="M10.3 4.2 2.9 17a2 2 0 0 0 1.7 3h14.8a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0Z" />
    <path d="M12 9.5v4M12 16.8h.01" />
  </svg>
);

/* ================= actions ================= */

/** 实心蓝按钮 —— 每屏尽量只出现一个 */
export function PrimaryButton({
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...props}
      className={cn(
        "w-full rounded-xl bg-primary px-4 py-3 text-center text-[14.5px] font-bold text-white",
        "shadow-[0_1px_2px_rgba(37,99,235,0.25)] transition active:bg-primary-deep disabled:opacity-40",
        className,
      )}
    />
  );
}

/* ================= selected states(tonal) ================= */

export function Chip({
  selected,
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { selected?: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      {...props}
      className={cn(
        "whitespace-nowrap rounded-full border px-3.5 py-1.5 text-[12.5px]",
        selected
          ? "border-sel-border bg-sel font-semibold text-sel-text"
          : "border-line bg-white font-medium text-ink-2",
        className,
      )}
    />
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  className,
}: {
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  onChange: (v: T) => void;
  className?: string;
}) {
  return (
    <div className={cn("flex gap-0.5 rounded-[11px] bg-line-soft p-[3px]", className)} role="tablist">
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onChange(o.value)}
            className={cn(
              "flex-1 whitespace-nowrap rounded-lg py-1.5 text-xs font-semibold",
              on
                ? "bg-sel text-sel-text ring-1 ring-inset ring-sel-border"
                : "text-ink-2",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/* ================= status badge ================= */

export type ExpenseStatus = "pending" | "reviewed" | "active";

const badgeStyle: Record<ExpenseStatus, string> = {
  pending: "border-warn-border bg-warn-bg text-warn",
  reviewed: "border-ok-border bg-ok-bg text-ok",
  active: "border-ok-border bg-ok-bg text-ok",
};
const badgeLabel: Record<ExpenseStatus, string> = {
  pending: "Pending",
  reviewed: "Reviewed",
  active: "Active",
};

export function StatusBadge({ status, children }: { status: ExpenseStatus; children?: React.ReactNode }) {
  return (
    <span className={cn("whitespace-nowrap rounded-full border px-2 py-[3px] text-[10.5px] font-bold", badgeStyle[status])}>
      {children ?? badgeLabel[status]}
    </span>
  );
}

/* ================= budget bar(阈值色 + used 语义) ================= */

export function BudgetBar({
  spentExGst,
  budgetExGst,
  showRemaining = true,
  className,
}: {
  spentExGst: number;
  budgetExGst: number;
  /** true: 左侧显示 "$X left";false: 显示 "of budget (ex GST)" */
  showRemaining?: boolean;
  className?: string;
}) {
  const pct = budgetExGst > 0 ? (spentExGst / budgetExGst) * 100 : 0;
  const tone = budgetTone(pct);
  const width = Math.min(100, Math.max(0, pct));
  return (
    <div className={className}>
      <div className="h-1.5 overflow-hidden rounded-full bg-[#EDF1F6]">
        <div className={cn("h-full rounded-full", toneFill[tone])} style={{ width: `${width}%` }} />
      </div>
      <div className="mt-1.5 flex items-baseline justify-between">
        {showRemaining ? (
          <span className="text-[12.5px] text-ink-2 tabular-nums">
            {money(Math.max(0, budgetExGst - spentExGst))} left
          </span>
        ) : (
          <span className="text-[11px] text-ink-3">of budget (ex GST)</span>
        )}
        <span className={cn("text-xs font-bold tabular-nums", toneText[tone])}>
          {Math.round(pct)}% used
        </span>
      </div>
    </div>
  );
}

/* ================= margin summary card ================= */

export function MarginSummary({
  revenueExGst,
  budgetExGst,
  spentExGst,
  targetPct,
}: {
  revenueExGst: number;
  budgetExGst: number;
  spentExGst: number;
  targetPct: number;
}) {
  const projected = projectedMarginPct(revenueExGst, budgetExGst);
  const toDate = marginToDatePct(revenueExGst, spentExGst);
  const delta = projected - targetPct;
  const up = delta >= 0;
  return (
    <section className="rounded-2xl border border-line bg-white p-3.5">
      <h3 className="text-[13.5px] font-bold">Projected margin</h3>
      <p className="mt-0.5 text-[11px] text-ink-3">(ex-GST revenue − total budget) ÷ ex-GST revenue</p>
      <div className="mt-2 flex items-baseline gap-2.5">
        <span className="text-[29px] font-extrabold tracking-tight tabular-nums">
          {projected.toFixed(1)}%
        </span>
        <span
          className={cn(
            "rounded-full border px-2 py-[3px] text-[10.5px] font-bold tabular-nums",
            up ? "border-ok-border bg-ok-bg text-ok" : "border-bad-border bg-bad-bg text-bad",
          )}
        >
          {up ? "+" : ""}
          {delta.toFixed(1)}pp vs target {targetPct.toFixed(1)}%
        </span>
      </div>
      <hr className="my-3 border-line-soft" />
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-ink-2">Margin to date</span>
        <span className="text-sm font-bold text-ink-2 tabular-nums">{toDate.toFixed(1)}%</span>
      </div>
      <p className="mt-1 text-[11px] leading-relaxed text-ink-3">
        按已发生成本计算 — 项目推进会持续拉低该值,仅供参考。
      </p>
    </section>
  );
}

/* ================= labour cost:不完整标注 ================= */

/** 部分记录缺 rate 时,金额带琥珀 "+",提示实际成本更高 */
export function IncompleteAmount({ value, incomplete }: { value: number; incomplete?: boolean }) {
  return (
    <span className="tabular-nums">
      {money(value, 0)}
      {incomplete && (
        <span className="font-extrabold text-warn" title="部分记录缺时薪,实际成本更高">
          +
        </span>
      )}
    </span>
  );
}

export function RateGapBanner({
  missing,
  total,
  onFix,
}: {
  /** 缺 rate 的记录数 */
  missing: number;
  total: number;
  onFix?: () => void;
}) {
  if (missing <= 0) return null;
  return (
    <button
      type="button"
      onClick={onFix}
      className="flex w-full items-center gap-2.5 rounded-xl border border-warn-border bg-warn-bg p-3 text-left"
    >
      <AlertIcon className="h-[19px] w-[19px] flex-none text-warn-fill" />
      <span className="min-w-0 flex-1">
        <span className="block text-[12.5px] font-bold leading-snug text-[#92400E]">
          {missing} / {total} 条记录缺时薪,成本被低估
        </span>
        <span className="mt-0.5 block text-[11px] text-warn">点按补全工人 rate</span>
      </span>
      <ChevronRightIcon className="h-[15px] w-[15px] flex-none text-warn-fill" />
    </button>
  );
}
