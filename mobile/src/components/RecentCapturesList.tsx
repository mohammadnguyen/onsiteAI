import { useMemo } from 'react';
import { View, Text, StyleSheet, ActivityIndicator, Pressable } from 'react-native';
import { Link, type Href } from 'expo-router';
import { useTranslation } from 'react-i18next';
import type { UseQueryResult } from '@tanstack/react-query';
import { useJobs } from '../api/hooks/useJobs';
import type { ExpenseListResponse } from '../api/hooks/useExpenses';
import { ExpenseRow } from './ExpenseRow';

/**
 * Mobile Capture v1 Sub-batch A: read-only "My Captures" list.
 *
 * Rendered below the capture form / result card on the Expenses
 * tab. Honours the product rule that capture remains the primary
 * action — this component is passive confirmation only. No edit,
 * no delete, no filters, no pagination.
 *
 * Job name is resolved opportunistically via the existing
 * `useJobs()` cache; if jobs haven't loaded yet the row simply
 * omits the job line — no blocking spinner, no error path of its
 * own.
 *
 * M2-B: the row visual lives in `ExpenseRow` (shared with the full
 * expenses list); this component keeps the heading + states + the
 * rejected-rows display rule. The optional `showViewAll` prop
 * renders a "View all expenses" footer link to the M2-B full list —
 * set by the Expenses tab, left unset by the Jobs modal.
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
  /**
   * M2-B: when true, renders a "View all expenses" footer link to
   * the full expenses list at `/expenses/list`.
   */
  showViewAll?: boolean;
};

export function RecentCapturesList({
  query,
  heading,
  fromJobId,
  showViewAll,
}: Props) {
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
  // visible on admin web + the M2-B full list's explicit Rejected
  // filter, but this passive list correctly treats it as gone.
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

      {showViewAll ? (
        <Link href={'/expenses/list' as unknown as Href} asChild>
          <Pressable
            testID="view-all-expenses"
            accessibilityRole="button"
            hitSlop={8}
            style={({ pressed }) => [s.viewAll, pressed && s.viewAllPressed]}
          >
            <Text style={s.viewAllText}>{t('expense_list.view_all')} ›</Text>
          </Pressable>
        </Link>
      ) : null}
    </View>
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
  viewAll: { paddingVertical: 12 },
  viewAllPressed: { opacity: 0.5 },
  viewAllText: { color: '#1e293b', fontSize: 14, fontWeight: '600' },
});
