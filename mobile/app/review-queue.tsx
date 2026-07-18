import { useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  ActivityIndicator,
  Pressable,
  RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, type Href } from 'expo-router';
import { useTranslation } from 'react-i18next';
import axios from 'axios';

import {
  useOpenReviewQueue,
  usePendingExpenseSummaries,
  type ReviewQueueItem,
} from '../src/api/hooks/useReviewQueue';
import { useJobs } from '../src/api/hooks/useJobs';
import {
  useResolveQueueItem,
  useRejectQueueItem,
  useDeleteExpense,
  type ExpensePublic,
} from '../src/api/hooks/useExpenses';
import { formatMoney } from '../src/util/format';
import { formatDateAU } from '../src/util/dates';
import { useOneShotBack } from '../src/util/navigation';
import { useScaledStyles } from '../src/ui/type';
import { tokens } from '../src/ui/tokens';
import { Toast } from '../src/ui/kit';

/**
 * forey F3 (handoff §3): the review queue as in-place triage CARDS.
 *
 * Each open row renders as a full card — amount, reasons, description,
 * job · date — with 驳回 / 改项目 / 通过 right on it (the same
 * mutations the Today stack uses; the expense detail keeps the full
 * corrections flow behind 改项目/tap). A duplicate-suspected card
 * swaps its actions for 删除重复 / 保留并通过, where delete uses the
 * existing soft-delete (reject + audit) on THIS expense — the original
 * it duplicates stays untouched.
 *
 * The amber banner totals the queue (count + $ + oldest). Evidence
 * rules (the F1 lesson): the $ total renders only when the summaries
 * query actually delivered; the green all-clear only on a loaded,
 * EMPTY queue.
 *
 * Join model unchanged (M3 Option 1): queue rows ⋈ pending summaries;
 * a join-miss renders degraded but stays actionable via the detail.
 */

export default function ReviewQueueScreen() {
  const s = useScaledStyles(base);
  const { t } = useTranslation();

  const queue = useOpenReviewQueue();
  const summaries = usePendingExpenseSummaries();
  const jobs = useJobs();
  const [userRefreshing, setUserRefreshing] = useState(false);
  const [toast, setToast] = useState<{ text: string; seq: number } | null>(null);
  const toastSeq = useRef(0);
  const showToast = (text: string) => {
    toastSeq.current += 1;
    setToast({ text, seq: toastSeq.current });
  };

  const jobMap = useMemo(() => {
    const m = new Map<string, string>();
    jobs.data?.forEach((j) => m.set(j.job_id, j.job_name));
    return m;
  }, [jobs.data]);

  const expenseMap = useMemo(() => {
    const m = new Map<string, ExpensePublic>();
    summaries.data?.items.forEach((e) => m.set(e.expense_id, e));
    return m;
  }, [summaries.data]);

  const onBack = useOneShotBack('/(tabs)/home' as unknown as Href);

  const onRefresh = () => {
    setUserRefreshing(true);
    void Promise.allSettled([queue.refetch(), summaries.refetch()]).finally(
      () => setUserRefreshing(false),
    );
  };

  const isForbidden =
    queue.isError &&
    axios.isAxiosError(queue.error) &&
    queue.error.response?.status === 403;

  const loading = queue.isLoading || summaries.isLoading;
  const rows = queue.data ?? [];

  // Banner money: positive evidence only (F1 lesson — never "$0.00"
  // over a real count because the summaries fetch failed).
  const totalKnown = summaries.data !== undefined;
  const total = useMemo(() => {
    if (!totalKnown) return 0;
    const open = new Set(rows.map((q) => q.expense_id));
    return (summaries.data?.items ?? [])
      .filter((e) => open.has(e.expense_id))
      .reduce((acc, e) => acc + parseFloat(e.amount_inc_gst ?? '0'), 0);
  }, [rows, summaries.data, totalKnown]);
  const oldest = rows.length > 0 ? rows[0].opened_at.slice(0, 10) : null;

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <View style={s.header}>
        <Pressable
          onPress={onBack}
          hitSlop={12}
          testID="review-queue-back"
          accessibilityRole="button"
          accessibilityLabel={t('expense.back')}
          style={({ pressed }) => [s.backBtn, pressed && s.backBtnPressed]}
        >
          <Text style={s.backChevron}>{'‹'}</Text>
          <Text style={s.backLabel}>{t('tabs.home')}</Text>
        </Pressable>
        <Text style={s.headerTitle} numberOfLines={1}>
          {t('review_queue.title')}
        </Text>
        <View style={s.headerSpacer} />
      </View>

      <FlatList
        data={loading ? [] : rows}
        keyExtractor={(item) => item.review_id}
        renderItem={({ item }) => (
          <TriageCard
            item={item}
            expense={expenseMap.get(item.expense_id)}
            jobName={
              expenseMap.get(item.expense_id)
                ? jobMap.get(expenseMap.get(item.expense_id)!.job_id)
                : undefined
            }
            onToast={showToast}
          />
        )}
        ListHeaderComponent={
          !loading && rows.length > 0 ? (
            <View style={s.banner} testID="review-queue-banner">
              <View style={s.bannerDot} />
              <Text style={s.bannerText} numberOfLines={1}>
                {totalKnown
                  ? t('review_queue.banner', {
                      count: rows.length,
                      sum: formatMoney(total.toFixed(2)),
                    })
                  : t('review_queue.banner_count_only', {
                      count: rows.length,
                    })}
              </Text>
              {oldest ? (
                <Text style={s.bannerSub} numberOfLines={1}>
                  {t('review_queue.earliest', {
                    date: formatDateAU(oldest),
                  })}
                </Text>
              ) : null}
            </View>
          ) : null
        }
        ListFooterComponent={
          !loading && rows.length > 0 ? (
            <Text style={s.footNote}>{t('review_queue.post_note')}</Text>
          ) : null
        }
        style={s.list}
        contentContainerStyle={
          loading || rows.length === 0 ? s.listEmptyContainer : s.listContainer
        }
        refreshControl={
          <RefreshControl
            refreshing={userRefreshing}
            onRefresh={onRefresh}
            tintColor={tokens.ink3}
          />
        }
        testID="review-queue-list"
        ListEmptyComponent={
          loading ? (
            <View style={s.state} testID="review-queue-loading">
              <ActivityIndicator color={tokens.primary} />
              <Text style={s.stateText}>{t('common.loading')}</Text>
            </View>
          ) : isForbidden ? (
            <View style={s.state} testID="review-queue-forbidden">
              <Text style={s.stateText}>{t('expense.review_forbidden')}</Text>
            </View>
          ) : queue.isError && !queue.data ? (
            <View style={s.state} testID="review-queue-error">
              <Text style={[s.stateText, s.errorText]}>
                {t('review_queue.error')}
              </Text>
              <Pressable
                onPress={() => void queue.refetch()}
                style={({ pressed }) => [s.linkBtn, pressed && s.linkBtnPressed]}
                accessibilityRole="button"
                testID="review-queue-retry"
              >
                <Text style={s.linkBtnText}>{t('common.retry')}</Text>
              </Pressable>
            </View>
          ) : queue.isSuccess ? (
            // Positive evidence: queue.data is a loaded, empty list.
            <View style={s.stateWrap} testID="review-queue-empty">
              <View style={s.doneCard}>
                <View style={s.doneTick}>
                  <Text style={s.doneTickText}>{'✓'}</Text>
                </View>
                <Text style={s.doneTitle}>{t('home.all_clear')}</Text>
                <Text style={s.doneSub}>{t('review_queue.empty_sub')}</Text>
              </View>
            </View>
          ) : null
        }
      />
      <Toast
        text={toast?.text ?? null}
        seq={toast?.seq ?? 0}
        onDone={() => setToast(null)}
      />
    </SafeAreaView>
  );
}

/**
 * One triage card. Duplicate-suspected rows swap the action pair:
 * 删除重复 (soft-delete THIS expense) / 保留并通过 (resolve).
 */
function TriageCard({
  item,
  expense,
  jobName,
  onToast,
}: {
  item: ReviewQueueItem;
  expense: ExpensePublic | undefined;
  jobName: string | undefined;
  onToast: (text: string) => void;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const router = useRouter();
  const approve = useResolveQueueItem(item.review_id);
  const reject = useRejectQueueItem(item.review_id);
  const del = useDeleteExpense(item.expense_id);
  const busy = approve.isPending || reject.isPending || del.isPending;

  const isDup = item.review_reasons.includes('duplicate_suspected');
  const isJobUnsure = item.review_reasons.includes('job_uncertain');
  const money = expense ? formatMoney(expense.amount_inc_gst) : null;
  const openDetail = () =>
    router.push(`/expenses/${item.expense_id}` as unknown as Href);

  const run = async (
    fire: () => Promise<unknown>,
    okText: string,
    failText: string,
  ) => {
    if (busy) return;
    try {
      await fire();
      onToast(okText);
      // Success invalidations (in the hooks) refetch the queue — the
      // card leaves the list on server truth, no optimistic state here.
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : undefined;
      if (status === 409 || status === 404) {
        onToast(t('review.already_handled'));
        return;
      }
      onToast(failText);
    }
  };

  return (
    <View style={s.card} testID={`triage-row-${item.review_id}`}>
      <Pressable
        onPress={openDetail}
        accessibilityRole="button"
        disabled={busy}
        style={({ pressed }) => pressed && s.cardPressed}
      >
        <View style={s.cardTop}>
          {money ? (
            <Text
              style={s.amount}
              numberOfLines={1}
              adjustsFontSizeToFit
              minimumFontScale={0.7}
            >
              {money}
            </Text>
          ) : (
            <Text style={s.degraded}>
              {t('review_queue.summary_unavailable')}
            </Text>
          )}
          {/* Fidelity §3: reason badges sit top-right beside the amount. */}
          <View style={s.reasonRowTop}>
            {item.review_reasons.map((code) => (
              <View
                key={code}
                style={[
                  s.reasonPill,
                  code === 'duplicate_suspected' && s.reasonPillDup,
                ]}
              >
                <Text
                  style={[
                    s.reasonText,
                    code === 'duplicate_suspected' && s.reasonTextDup,
                  ]}
                >
                  {t('review_reason.' + code, { defaultValue: code })}
                </Text>
              </View>
            ))}
          </View>
        </View>

        {expense ? (
          <Text style={s.preview} numberOfLines={2}>
            {expense.raw_input_text || expense.description || '—'}
          </Text>
        ) : null}
        {expense ? (
          <Text style={s.meta} numberOfLines={1}>
            {[jobName, formatDateAU(expense.expense_date)]
              .filter(Boolean)
              .join(' · ')}
          </Text>
        ) : null}

        {/* Fidelity §3: job-uncertain cards embed the SUGGESTION block —
            the parser's best-guess job + 换项目 (opens the detail, which
            owns the corrections flow). */}
        {expense && isJobUnsure ? (
          <View style={s.suggestBlock}>
            <Text style={s.suggestLabel}>{t('review.suggested_label')}</Text>
            <Text style={s.suggestName} numberOfLines={1}>
              {jobName || '—'}
            </Text>
            <Text style={s.suggestChange} onPress={openDetail}>
              {t('review.change_job')}
            </Text>
          </View>
        ) : null}
        {/* Fidelity §3: duplicate cards carry a red comparison block.
            The original's figures live on the detail screen — 对比
            opens it (a per-card fetch of the original here would be a
            request per row). */}
        {expense && isDup ? (
          <View style={s.dupBlock}>
            <Text style={s.dupBlockText} numberOfLines={1}>
              {t('review.dup_hint')}
            </Text>
            <Text style={s.dupCompare} onPress={openDetail}>
              {t('review.compare')}
            </Text>
          </View>
        ) : null}
      </Pressable>

      {/* Actions need a loaded summary — approving an amount you can't
          see is not offered (F1 rule). Degraded rows act via detail. */}
      {expense ? (
        <View style={s.actions}>
          {isDup ? (
            <>
              <Pressable
                style={({ pressed }) => [s.btn, s.btnGhost, pressed && s.pressed]}
                onPress={() =>
                  void run(
                    () => del.mutateAsync({}),
                    t('toast.dup_deleted', { sum: money ?? '' }),
                    t('review.reject_failed'),
                  )
                }
                disabled={busy}
                accessibilityRole="button"
                testID={`triage-delete-dup-${item.review_id}`}
              >
                <Text
                  style={[s.btnText, s.btnTextReject]}
                  numberOfLines={1}
                  adjustsFontSizeToFit
                  minimumFontScale={0.8}
                >
                  {t('review.delete_dup')}
                </Text>
              </Pressable>
              <Pressable
                style={({ pressed }) => [s.btn, s.btnApprove, pressed && s.pressed]}
                onPress={() =>
                  void run(
                    () => approve.mutateAsync(),
                    t('toast.approved', { sum: money ?? '' }),
                    t('review.approve_failed'),
                  )
                }
                disabled={busy}
                accessibilityRole="button"
                testID={`triage-keep-approve-${item.review_id}`}
              >
                <Text
                  style={[s.btnText, s.btnTextApprove]}
                  numberOfLines={1}
                  adjustsFontSizeToFit
                  minimumFontScale={0.8}
                >
                  {t('review.keep_approve')}
                </Text>
              </Pressable>
            </>
          ) : (
            <>
              <Pressable
                style={({ pressed }) => [s.btn, s.btnGhost, pressed && s.pressed]}
                onPress={() =>
                  void run(
                    () => reject.mutateAsync(),
                    t('toast.rejected', { sum: money ?? '' }),
                    t('review.reject_failed'),
                  )
                }
                disabled={busy}
                accessibilityRole="button"
                testID={`triage-reject-${item.review_id}`}
              >
                <Text style={[s.btnText, s.btnTextReject]} numberOfLines={1}>
                  {t('review.reject')}
                </Text>
              </Pressable>
              <Pressable
                style={({ pressed }) => [s.btn, s.btnGhost, pressed && s.pressed]}
                onPress={openDetail}
                disabled={busy}
                accessibilityRole="button"
                testID={`triage-fix-${item.review_id}`}
              >
                <Text
                  style={[s.btnText, s.btnTextGhost]}
                  numberOfLines={1}
                  adjustsFontSizeToFit
                  minimumFontScale={0.8}
                >
                  {t('review.fix_project')}
                </Text>
              </Pressable>
              <Pressable
                style={({ pressed }) => [s.btn, s.btnApprove, pressed && s.pressed]}
                onPress={() =>
                  void run(
                    () => approve.mutateAsync(),
                    t('toast.approved', { sum: money ?? '' }),
                    t('review.approve_failed'),
                  )
                }
                disabled={busy}
                accessibilityRole="button"
                testID={`triage-approve-${item.review_id}`}
              >
                <Text style={[s.btnText, s.btnTextApprove]} numberOfLines={1}>
                  {t('review.approve')}
                </Text>
              </Pressable>
            </>
          )}
        </View>
      ) : null}
    </View>
  );
}

const base = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: tokens.line,
    backgroundColor: tokens.surface,
  },
  backBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 4,
    minWidth: 72,
  },
  backBtnPressed: { opacity: 0.5 },
  backChevron: {
    fontSize: 28,
    color: tokens.primary,
    marginRight: 4,
    lineHeight: 28,
  },
  backLabel: { fontSize: 16, color: tokens.primary },
  headerTitle: {
    flex: 1,
    textAlign: 'center',
    fontSize: 17,
    fontWeight: '700',
    color: tokens.ink,
  },
  headerSpacer: { width: 72 },
  list: { flex: 1 },
  listContainer: { padding: 16, gap: 12 },
  listEmptyContainer: { flexGrow: 1, justifyContent: 'center' },
  state: { alignItems: 'center', padding: 24, gap: 12 },
  stateText: { color: tokens.ink3, fontSize: 15 },
  errorText: { color: tokens.bad },
  linkBtn: { paddingHorizontal: 12, paddingVertical: 8 },
  linkBtnPressed: { opacity: 0.5 },
  linkBtnText: { color: tokens.primary, fontSize: 15, fontWeight: '600' },
  stateWrap: { paddingHorizontal: 16 },
  // Fidelity §3: FULL green card — solid #12B76A circle, white tick.
  doneCard: {
    backgroundColor: tokens.okBg,
    borderWidth: 1,
    borderColor: tokens.okBorder,
    borderRadius: 18,
    paddingVertical: 34,
    paddingHorizontal: 16,
    alignItems: 'center',
    gap: 10,
  },
  doneTick: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: tokens.okFill,
    alignItems: 'center',
    justifyContent: 'center',
  },
  doneTickText: { fontSize: 21, color: '#ffffff', fontWeight: '800' },
  doneTitle: { fontSize: 15, fontWeight: '800', color: tokens.ok },
  doneSub: { fontSize: 12.5, color: '#3E7A5C', textAlign: 'center' },

  // Fidelity §3: single row — amber dot + bold text + right 最早.
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: tokens.warnBg,
    borderWidth: 1,
    borderColor: tokens.warnBorder,
    borderRadius: 14,
    paddingHorizontal: 14,
    paddingVertical: 11,
    marginBottom: 2,
  },
  bannerDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    backgroundColor: tokens.warnFill,
  },
  bannerText: {
    flex: 1,
    color: tokens.warn,
    fontSize: 13,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
  },
  bannerSub: { color: tokens.warnMid, fontSize: 12 },
  footNote: {
    color: tokens.muted,
    fontSize: 11.5,
    textAlign: 'center',
    paddingVertical: 10,
    lineHeight: 16,
  },

  card: {
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 16,
    padding: 14,
    shadowColor: '#101828',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.05,
    shadowRadius: 14,
    elevation: 2,
  },
  cardPressed: { opacity: 0.85 },
  cardTop: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 10,
  },
  amount: {
    flexShrink: 1,
    fontSize: 22,
    fontWeight: '800',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
    letterSpacing: -0.3,
  },
  waiting: { color: tokens.muted, fontSize: 12 },
  degraded: { color: tokens.ink3, fontSize: 14, fontStyle: 'italic' },
  preview: { color: tokens.ink2, fontSize: 14, marginTop: 6, lineHeight: 19 },
  meta: { color: tokens.muted, fontSize: 12, marginTop: 4 },
  reasonRowTop: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
    flexShrink: 1,
    justifyContent: 'flex-end',
  },
  suggestBlock: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: tokens.surfaceSub,
    borderWidth: 1,
    borderColor: tokens.inputBorder,
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: 8,
    marginTop: 8,
  },
  suggestLabel: { fontSize: 12, color: tokens.ink3 },
  suggestName: {
    flex: 1,
    fontSize: 12.5,
    fontWeight: '700',
    color: tokens.ink,
  },
  suggestChange: { fontSize: 12, fontWeight: '700', color: tokens.primary },
  dupBlock: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: '#FEF6F5',
    borderWidth: 1,
    borderColor: '#F9DEDC',
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: 8,
    marginTop: 8,
  },
  dupBlockText: { flex: 1, fontSize: 12, color: tokens.badDeep },
  dupCompare: { fontSize: 12, fontWeight: '700', color: tokens.bad },
  reasonPill: {
    paddingHorizontal: 9,
    paddingVertical: 3,
    borderRadius: 999,
    borderWidth: 1,
    backgroundColor: tokens.warnBg,
    borderColor: tokens.warnBorder,
  },
  reasonText: { fontSize: 10.5, fontWeight: '700', color: tokens.warn },
  reasonPillDup: {
    backgroundColor: tokens.badBg,
    borderColor: tokens.badBorder,
  },
  reasonTextDup: { color: tokens.bad },
  actions: { flexDirection: 'row', gap: 8, marginTop: 12 },
  btn: {
    flex: 1,
    height: 42,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 6,
  },
  btnGhost: {
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
  },
  btnApprove: { flex: 1.4, backgroundColor: tokens.okFill },
  btnText: { fontSize: 14, fontWeight: '700' },
  btnTextReject: { color: tokens.bad },
  btnTextGhost: { color: tokens.ink2 },
  btnTextApprove: { color: '#ffffff' },
  pressed: { opacity: 0.75 },
});
