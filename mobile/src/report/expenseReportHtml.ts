import type { TFunction } from 'i18next';

/**
 * Builds the print HTML for the PDF expense report (founder decision
 * 2026-08-24), rendered on-device by `expo-print`.
 *
 * Slice 1 is the OVERVIEW page only — the per-project pages and the
 * accountant summary follow once on-device print fidelity is proven.
 *
 * Rules this file follows deliberately:
 *
 *  - It NEVER computes money. Every total, rollup and series arrives
 *    pre-computed from `GET /reports/expenses-report`; this module
 *    formats and lays out only (CLAUDE.md: no business logic in the
 *    frontend). The one arithmetic here is bar WIDTH as a share of the
 *    largest row — presentation, not a figure anyone reads.
 *  - Every user-visible string goes through `t`, so EN and CN come from
 *    the existing locale files rather than a forked template.
 *  - All CSS is inline/embedded and self-contained: the print engine
 *    has no network, so no web fonts and no external assets.
 *  - `table { break-inside: auto }` is explicit. Chrome and WebKit
 *    otherwise treat a bordered table as one unbreakable block and push
 *    it whole onto the next page, leaving half-empty pages (observed
 *    while prototyping this report).
 */

export type ReportData = {
  meta: {
    from_date: string | null;
    to_date: string | null;
    generated_at: string;
    include_pending: boolean;
    job_count: number;
    expense_count: number;
  };
  totals: {
    actual_inc_gst: string;
    actual_ex_gst: string;
    gst_amount: string;
    receipts_on_file: number;
    receipts_expected_later: number;
  };
  jobs: Array<{
    job_id: string;
    job_name: string;
    job_code: string | null;
    site_address: string | null;
    expense_count: number;
    period_inc_gst: string;
    period_gst: string;
    period_ex_gst: string;
    contract_value_ex_gst: string | null;
    total_budget_ex_gst: string | null;
    all_time_ex_gst: string;
    remaining_ex_gst: string | null;
    percent_consumed: string | null;
    overspend: boolean;
  }>;
  categories: Array<{
    category_id: string | null;
    category_name: string | null;
    actual_ex_gst: string;
    actual_inc_gst: string;
  }>;
  months: Array<{ month: string; actual_inc_gst: string }>;
};

const ACCENT = '#2563EB';
const INK = '#17171c';
const INK2 = '#6e6e78';
const INK3 = '#8a8a94';
const LINE = '#e7e7ec';
const TRACK = '#f0f0f4';
const BAD = '#b3261e';
const WARN = '#9a6700';
const OK = '#3a7d44';

/** HTML-escape. The report embeds user-entered names and descriptions;
 *  an unescaped `<` would silently break the document. */
export function esc(v: string | null | undefined): string {
  if (v == null) return '';
  return v
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Money as `$1,234.56`. Input is a Decimal string from the API. */
export function money(v: string | null | undefined, dp = 2): string {
  if (v == null || v === '') return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return (
    '$' +
    n.toLocaleString('en-AU', {
      minimumFractionDigits: dp,
      maximumFractionDigits: dp,
    })
  );
}

/** Compact money for chart labels: `$68k`, `$1.2k`, `$940`. */
export function short(v: string): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  if (n >= 1000) return '$' + (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'k';
  return '$' + Math.round(n);
}

function pctNum(v: string | null): number | null {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Consumption colour: over budget red, near budget amber, else green. */
function pctColour(pct: number | null): string {
  if (pct == null) return INK3;
  if (pct > 100) return BAD;
  if (pct > 90) return WARN;
  return OK;
}

/** `2026-05` -> a short localized month label. */
function monthLabel(month: string, locale: string): string {
  const [y, m] = month.split('-').map(Number);
  if (!y || !m) return month;
  return new Date(y, m - 1, 1).toLocaleDateString(locale, { month: 'short' });
}

function dateLabel(iso: string | null, locale: string): string | null {
  if (!iso) return null;
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return iso;
  return new Date(y, m - 1, d).toLocaleDateString(locale, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

function periodLine(
  data: ReportData,
  t: TFunction,
  locale: string,
): string {
  const from = dateLabel(data.meta.from_date, locale);
  const to = dateLabel(data.meta.to_date, locale);
  const period =
    from && to
      ? `${from} – ${to}`
      : from
        ? t('report.period_from', { date: from })
        : to
          ? t('report.period_to', { date: to })
          : t('report.period_all');
  const bits = [
    t('report.period_label', { period }),
    data.meta.include_pending
      ? t('report.inclusion_with_pending')
      : t('report.inclusion_reviewed_only'),
    t('report.project_count', { count: data.meta.job_count }),
  ];
  return bits.join(' · ');
}

function kpiCard(label: string, value: string, colour = INK): string {
  return `<div class="kpi">
    <div class="kpi-l">${esc(label)}</div>
    <div class="kpi-v" style="color:${colour}">${esc(value)}</div>
  </div>`;
}

function jobBars(data: ReportData, t: TFunction): string {
  if (data.jobs.length === 0) return '';
  const max = Math.max(
    ...data.jobs.map((j) => Number(j.period_inc_gst) || 0),
    1,
  );
  const rows = data.jobs
    .map((j) => {
      const v = Number(j.period_inc_gst) || 0;
      const w = Math.max(1, Math.round((v / max) * 100));
      const pct = pctNum(j.percent_consumed);
      const pctText = pct == null ? '—' : `${Math.round(pct)}%`;
      return `<div class="bar-row">
        <div class="bar-name">${esc(j.job_name)}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${w}%"></div></div>
        <div class="bar-amt">${esc(short(j.period_inc_gst))}</div>
        <div class="bar-pct" style="color:${pctColour(pct)}">${esc(pctText)}</div>
      </div>`;
    })
    .join('');
  return `<div class="card">
    <div class="card-h">${esc(t('report.spend_by_project'))}<span class="card-sub">${esc(t('report.pct_is_budget_consumed'))}</span></div>
    ${rows}
  </div>`;
}

function categoryBars(data: ReportData, t: TFunction): string {
  if (data.categories.length === 0) return '';
  const top = data.categories.slice(0, 8);
  const max = Math.max(...top.map((c) => Number(c.actual_ex_gst) || 0), 1);
  const rows = top
    .map((c) => {
      const v = Number(c.actual_ex_gst) || 0;
      const w = Math.max(1, Math.round((v / max) * 100));
      const name = c.category_name ?? t('report.uncategorised');
      return `<div class="bar-row">
        <div class="bar-name">${esc(name)}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${w}%"></div></div>
        <div class="bar-amt">${esc(short(c.actual_ex_gst))}</div>
      </div>`;
    })
    .join('');
  return `<div class="card">
    <div class="card-h">${esc(t('report.spend_by_category'))}<span class="card-sub">${esc(t('report.ex_gst'))}</span></div>
    ${rows}
  </div>`;
}

function monthColumns(
  data: ReportData,
  t: TFunction,
  locale: string,
): string {
  if (data.months.length === 0) return '';
  const max = Math.max(
    ...data.months.map((m) => Number(m.actual_inc_gst) || 0),
    1,
  );
  const cols = data.months
    .map((m) => {
      const v = Number(m.actual_inc_gst) || 0;
      const h = Math.max(2, Math.round((v / max) * 88));
      return `<div class="col">
        <div class="col-v">${esc(short(m.actual_inc_gst))}</div>
        <div class="col-bar" style="height:${h}px"></div>
        <div class="col-l">${esc(monthLabel(m.month, locale))}</div>
      </div>`;
    })
    .join('');
  return `<div class="card">
    <div class="card-h">${esc(t('report.monthly_spend'))}<span class="card-sub">${esc(t('report.inc_gst'))}</span></div>
    <div class="cols">${cols}</div>
  </div>`;
}

function overBudgetBanner(data: ReportData, t: TFunction): string {
  const over = data.jobs.filter((j) => j.overspend);
  if (over.length === 0) return '';
  const names = over
    .map((j) => {
      const pct = pctNum(j.percent_consumed);
      return pct == null
        ? esc(j.job_name)
        : `${esc(j.job_name)} (${Math.round(pct)}%)`;
    })
    .join(', ');
  return `<div class="alert"><span class="alert-i">!</span>${esc(
    t('report.over_budget_prefix'),
  )} ${names}</div>`;
}

function projectTable(data: ReportData, t: TFunction): string {
  if (data.jobs.length === 0) {
    return `<div class="empty">${esc(t('report.no_expenses'))}</div>`;
  }
  const rows = data.jobs
    .map((j) => {
      const pct = pctNum(j.percent_consumed);
      return `<tr>
        <td>${esc(j.job_name)}</td>
        <td class="num">${j.expense_count}</td>
        <td class="num">${esc(money(j.period_inc_gst))}</td>
        <td class="num">${esc(money(j.period_gst))}</td>
        <td class="num">${esc(money(j.period_ex_gst))}</td>
        <td class="num">${esc(money(j.total_budget_ex_gst, 0))}</td>
        <td class="num" style="color:${pctColour(pct)}">${
          pct == null ? '—' : Math.round(pct) + '%'
        }</td>
      </tr>`;
    })
    .join('');
  return `<table>
    <thead><tr>
      <th>${esc(t('report.col_project'))}</th>
      <th class="num">${esc(t('report.col_expenses'))}</th>
      <th class="num">${esc(t('report.col_inc_gst'))}</th>
      <th class="num">${esc(t('report.col_gst'))}</th>
      <th class="num">${esc(t('report.col_ex_gst'))}</th>
      <th class="num">${esc(t('report.col_budget_ex'))}</th>
      <th class="num">${esc(t('report.col_consumed'))}</th>
    </tr></thead>
    <tbody>${rows}</tbody>
    <tfoot><tr>
      <td>${esc(t('report.total'))}</td>
      <td class="num">${data.meta.expense_count}</td>
      <td class="num">${esc(money(data.totals.actual_inc_gst))}</td>
      <td class="num">${esc(money(data.totals.gst_amount))}</td>
      <td class="num">${esc(money(data.totals.actual_ex_gst))}</td>
      <td class="num"></td>
      <td class="num"></td>
    </tr></tfoot>
  </table>`;
}

/**
 * Render the whole report document.
 *
 * `locale` drives date and month formatting only; all copy comes from
 * `t`. Both are passed in so this stays a pure function — trivially
 * unit-testable without mounting React or i18next.
 */
export function buildExpenseReportHtml(
  data: ReportData,
  t: TFunction,
  locale: string,
): string {
  const css = `
    @page { size: A4; margin: 14mm 12mm; }
    /* A printed report is a white document, never a themed UI. Without
       this, a device in dark mode renders it dark-on-dark — caught while
       previewing this template. */
    :root { color-scheme: light; }
    * { box-sizing: border-box; }
    html, body { background:#ffffff; }
    body { margin:0; font-family: -apple-system, "Helvetica Neue", "PingFang SC", sans-serif;
           color:${INK}; font-size:11px;
           -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    h1 { margin:0; font-size:21px; font-weight:600; letter-spacing:-0.02em; }
    .sub { margin-top:4px; font-size:11px; color:${INK2}; }
    header { padding-bottom:12px; border-bottom:2px solid ${INK}; display:flex;
             justify-content:space-between; align-items:flex-end; }
    .brand { font-size:10px; font-weight:600; letter-spacing:0.08em;
             text-transform:uppercase; color:${ACCENT}; }
    .unit { font-size:10px; color:${INK3}; }
    .alert { margin-top:12px; padding:9px 12px; background:#fdf1f0; border:1px solid #f3c6c2;
             border-radius:8px; color:#5c2320; font-size:11px; }
    .alert-i { display:inline-block; width:14px; height:14px; border-radius:7px; background:${BAD};
               color:#fff; text-align:center; line-height:14px; font-size:10px; font-weight:700;
               margin-right:7px; }
    .kpis { display:flex; gap:8px; margin-top:12px; }
    .kpi { flex:1; border:1px solid ${LINE}; border-radius:8px; padding:9px 11px; }
    .kpi-l { font-size:9px; font-weight:600; letter-spacing:0.06em; text-transform:uppercase;
             color:${INK3}; }
    .kpi-v { margin-top:3px; font-size:16px; font-weight:600; }
    .cards { display:flex; gap:10px; margin-top:12px; }
    .card { flex:1; border:1px solid ${LINE}; border-radius:8px; padding:11px 12px;
            break-inside:avoid; }
    .card-h { font-size:11px; font-weight:600; margin-bottom:8px; }
    .card-sub { font-weight:400; color:${INK3}; margin-left:6px; }
    .bar-row { display:flex; align-items:center; gap:7px; margin-bottom:5px; }
    .bar-name { width:33%; font-size:10px; color:${INK2}; overflow:hidden;
                text-overflow:ellipsis; white-space:nowrap; }
    .bar-track { flex:1; height:9px; background:${TRACK}; border-radius:4px; overflow:hidden; }
    .bar-fill { height:9px; background:${ACCENT}; border-radius:4px; }
    .bar-amt { width:44px; text-align:right; font-size:10px; font-weight:600; }
    .bar-pct { width:34px; text-align:right; font-size:10px; font-weight:600; }
    .cols { display:flex; align-items:flex-end; gap:8px; height:120px; }
    .col { flex:1; display:flex; flex-direction:column; align-items:center;
           justify-content:flex-end; }
    .col-v { font-size:9.5px; font-weight:600; margin-bottom:3px; }
    .col-bar { width:100%; max-width:46px; background:${ACCENT}; border-radius:3px 3px 0 0; }
    .col-l { margin-top:4px; font-size:9.5px; color:${INK3}; }
    table { width:100%; border-collapse:collapse; margin-top:14px; font-size:10px;
            break-inside:auto; }
    thead, tbody, tfoot { break-inside:auto; }
    tr { break-inside:avoid; }
    th { text-align:left; padding:6px 7px; font-size:8.5px; font-weight:600;
         letter-spacing:0.06em; text-transform:uppercase; color:${INK3};
         background:#fbfbfc; border-bottom:1px solid ${LINE}; }
    td { padding:5px 7px; border-top:1px solid #f0f0f3; }
    .num { text-align:right; }
    tfoot td { border-top:2px solid ${INK}; font-weight:600; background:#fbfbfc; }
    .empty { margin-top:20px; color:${INK3}; font-size:11px; }
    footer { margin-top:16px; padding-top:6px; border-top:1px solid #ececf0;
             display:flex; justify-content:space-between; font-size:9px; color:#9a9aa3; }
  `;

  return `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>${css}</style></head><body>
<header>
  <div>
    <div class="brand">${esc(t('report.brand'))}</div>
    <h1>${esc(t('report.title'))}</h1>
    <div class="sub">${esc(periodLine(data, t, locale))}</div>
  </div>
  <div class="unit">${esc(t('report.currency_note'))}</div>
</header>
${overBudgetBanner(data, t)}
<div class="kpis">
  ${kpiCard(t('report.kpi_total_inc'), money(data.totals.actual_inc_gst))}
  ${kpiCard(t('report.kpi_total_ex'), money(data.totals.actual_ex_gst))}
  ${kpiCard(t('report.kpi_gst'), money(data.totals.gst_amount), ACCENT)}
  ${kpiCard(t('report.kpi_expenses'), String(data.meta.expense_count))}
</div>
<div class="cards">
  ${jobBars(data, t)}
  <div style="flex:1;display:flex;flex-direction:column;gap:10px;">
    ${monthColumns(data, t, locale)}
    ${categoryBars(data, t)}
  </div>
</div>
${projectTable(data, t)}
<footer><span>${esc(t('report.footer_left'))}</span><span>${esc(t('report.footer_right'))}</span></footer>
</body></html>`;
}
