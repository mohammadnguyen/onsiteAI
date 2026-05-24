import { useMemo } from 'react';
import { View, Text, StyleSheet, ActivityIndicator, Pressable } from 'react-native';
import { Link, type Href } from 'expo-router';
import { useTranslation } from 'react-i18next';
import type { UseQueryResult } from '@tanstack/react-query';
import { useJobs } from '../api/hooks/useJobs';
import type { ExpensePublic, ExpenseListResponse } from '../api/hooks/useExpenses';
import { formatMoney } from '../util/format';
import { formatDateAU } from '../util/dates';

/**
 * Mobile Capture v1 Sub-batch A: read-only "My Captures" list.
 *
 * Rendered below the capture form / result card on the Expenses
 * tab. Honours the product rule that capture remains the primary
 * action — this component is passive confirmation only. No edit,
 * no delete, no filters, no pagination.
 *
 * Row content is restricted to fields available on `ExpensePublic`
 * (no nested supplier/category lookups). Job name is resolved
 * opportunistically via the existing `useJobs()` cache; if jobs
 * haven't loaded yet the row simply omits the job line — no
 * blocking spinner, no error path of its own.
 *
 * Mobile Expense Detail (v1): each row is now tappable and routes
 * to the read-only detail screen at `/expenses/{expense_id}`. The
 * visual treatment is unchanged; only the wrapping element changes
 * from a plain `View` to a `Link`-driven `Pressable` with
 * accessibility metadata.
 */

type Props = {
  query: UseQueryResult<ExpenseListResponse, unknown>;
  /**
   * Optional heading text override. When set, replaces the default
   * "My Captures" label. Used by the job detail modal to render the
   * same row list under an "Expenses" label without forking the
   * component. Pass an already-translated string.
   */
  heading?: string;
  /**
   * Optional navigation-context source. When set, row taps include
   * `?from=job&jobId=<id>` on the expense-detail href so the detail
   * screen knows to return to the Jobs tab modal (rather than the
   * Capture screen) after back/delete actions. Capture-screen
   * callers leave this unset; their Links stay context-free and
   * the detail screen falls back to its current router.back()
   * behaviour.
   */
  fromJobId?: string;
};

const STATUS_COLORS = {
  pending: { bg: '#fef3c7', fg: '#92400e' },
  reviewed: { bg: '#dcfce7', fg: '#15803d' },
  rejected: { bg: '#fee2e2', fg: '#991b1b' },
} as const;

const PREVIEW_MAX = 60;

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max).trimEnd() + '…';
}

export function RecentCapturesList({ query, heading, fromJobId }: Props) {
  const { t } = useTranslation();
  const jobs = useJobs();

  const jobMap = useMemo(() => {
    const m = new Map<string, string>();
    jobs.data?.forEach((j) => m.set(j.job_id, j.job_name));
    return m;
  }, [jobs.data]);

  // Operator dogfood signal: after Delete, the soft-deleted row was
  // still appearing in mobile lists with a "Rejected" pill — which
  // doesn't match the mental model of "delete = gone from active
  // workflow". Filter rejected rows out of the default list view.
  // The row still exists in the backend (audit-preserved) and remains
  // visible on admin web, but mobile correctly treats it as gone.
  // Applies to BOTH callers of this component: My Captures on the
  // Capture screen + per-job expense list in the Job detail modal.
  const items = (query.data?.items ?? []).filter(
    (e) => e.review_status !== 'rejected',
  );

  return (
    <View style={s.section} testID="recent-captures-section">
      <Text style={s.heading}>{heading ?? t('capture.recent.title')}</Text>

      {query.isLoading ? (
        <View style={s.state} testID="recent-loading">
          <ActivityIndicator color="#1e293b" />
        </View>
      ) : query.isError ? (
        <View style={s.state} testID="recent-error">
          <Text style={s.errorText}>{t('capture.recent.error')}</Text>
        </View>
      ) : items.length === 0 ? (
        <View style={s.state} testID="recent-empty">
          <Text style={s.emptyText}>{t('capture.recent.empty')}</Text>
        </View>
      ) : (
        <View testID="recent-list">
          {items.map((e) => (
            <ExpenseRow
              key={e.expense_id}
              expense={e}
              jobName={jobMap.get(e.job_id)}
              fromJobId={fromJobId}
            />
          ))}
        </View>
      )}
    </View>
  );
}

function ExpenseRow({
  expense,
  jobName,
  fromJobId,
}: {
  expense: ExpensePublic;
  jobName: string | undefined;
  fromJobId?: string;
}) {
  const { t } = useTranslation();
  const statusColor = STATUS_COLORS[expense.review_status];
  const statusKey = `expense.status_${expense.review_status}`;
  const previewSource = expense.raw_input_text || expense.description || '';
  const preview = truncate(previewSource, PREVIEW_MAX);

  // Mobile Expense Detail (v1): the row is now a tappable Link that
  // routes to /expenses/{id}. `asChild` lets the Link delegate
  // press-handling to the Pressable, preserving native press feedback
  // (the dim-on-press in `pressed && s.rowPressed`).
  //
  // The `as Href` cast bridges expo-router's typed-routes manifest
  // for the newly-added /expenses/[id] route: the manifest is
  // regenerated by Metro on file changes, so a static tsc run before
  // the next dev-server start would otherwise reject the path string.
  // Runtime behaviour is unchanged. After the next Metro run the cast
  // can be removed (the path will be in the generated union).
  // When the row is rendered inside a job-context list (per-job
  // expenses in the Job detail modal), append `from=job&jobId=...`
  // so the expense detail screen knows to return to the Jobs tab
  // modal rather than the Capture screen on back/delete.
  const detailHref = (
    fromJobId
      ? `/expenses/${expense.expense_id}?from=job&jobId=${fromJobId}`
      : `/expenses/${expense.expense_id}`
  ) as unknown as Href;
  return (
    <Link href={detailHref} asChild>
      <Pressable
        testID={`recent-row-${expense.expense_id}`}
        accessibilityRole="button"
        accessibilityLabel={`${expense.expense_date} ${expense.amount_inc_gst}`}
        hitSlop={4}
        style={({ pressed }) => [s.row, pressed && s.rowPressed]}
      >
        <View style={s.rowTop}>
          <Text style={s.amount}>{formatMoney(expense.amount_inc_gst)}</Text>
          <View style={[s.pill, { backgroundColor: statusColor.bg }]}>
            <Text style={[s.pillText, { color: statusColor.fg }]}>
              {t(statusKey)}
            </Text>
          </View>
        </View>

        <View style={s.rowMid}>
          <Text style={s.date}>{formatDateAU(expense.expense_date)}</Text>
          {jobName ? <Text style={s.dot}> · </Text> : null}
          {jobName ? (
            <Text style={s.job} numberOfLines={1}>
              {jobName}
            </Text>
          ) : null}
        </View>

        <Text
          style={preview ? s.preview : s.previewMuted}
          numberOfLines={1}
          testID={`recent-row-${expense.expense_id}-preview`}
        >
          {preview || '—'}
        </Text>

        {(expense.duplicate_flag || expense.receipt_status === 'expected_later') && (
          <View style={s.rowFlags}>
            {expense.duplicate_flag ? (
              <View
                style={[s.flagPill, s.flagDuplicate]}
                testID={`recent-row-${expense.expense_id}-duplicate`}
              >
                <Text style={s.flagText}>{t('capture.recent.duplicate_flag')}</Text>
              </View>
            ) : null}
            {expense.receipt_status === 'expected_later' ? (
              <View
                style={[s.flagPill, s.flagReceipt]}
                testID={`recent-row-${expense.expense_id}-receipt`}
              >
                <Text style={s.flagText}>{t('capture.recent.receipt_later_flag')}</Text>
              </View>
            ) : null}
          </View>
        )}
      </Pressable>
    </Link>
  );
}

const s = StyleSheet.create({
  section: { marginTop: 24 },
  heading: {
    fontSize: 18,
    fontWeight: '600',
    color: '#0f172a',
    marginBottom: 4,
  },
  state: { paddingVertical: 24, alignItems: 'center' },
  emptyText: { color: '#64748b', fontSize: 14 },
  errorText: { color: '#b91c1c', fontSize: 14 },
  row: {
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  rowPressed: {
    backgroundColor: '#f1f5f9',
  },
  rowTop: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  amount: {
    fontSize: 16,
    fontWeight: '600',
    color: '#0f172a',
    fontVariant: ['tabular-nums'],
  },
  pill: {
    paddingHorizontal: 10,
    paddingVertical: 3,
    borderRadius: 12,
    overflow: 'hidden',
  },
  pillText: { fontSize: 11, fontWeight: '600' },
  rowMid: { flexDirection: 'row', marginTop: 4, alignItems: 'center' },
  date: { color: '#64748b', fontSize: 13 },
  dot: { color: '#94a3b8', fontSize: 13 },
  job: { color: '#64748b', fontSize: 13, flexShrink: 1 },
  preview: { color: '#334155', fontSize: 13, marginTop: 4 },
  previewMuted: { color: '#94a3b8', fontSize: 13, marginTop: 4 },
  rowFlags: { flexDirection: 'row', marginTop: 6 },
  flagPill: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 10,
    marginRight: 6,
  },
  flagDuplicate: { backgroundColor: '#fef3c7' },
  flagReceipt: { backgroundColor: '#e0e7ff' },
  flagText: { fontSize: 10, fontWeight: '600', color: '#1e293b' },
});
