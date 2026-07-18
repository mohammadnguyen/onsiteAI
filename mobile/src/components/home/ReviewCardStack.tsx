import { useEffect, useMemo, useRef, useState } from 'react';
import { View, Text, Pressable, StyleSheet, ActivityIndicator } from 'react-native';
import { useTranslation } from 'react-i18next';
import { useRouter, type Href } from 'expo-router';
import axios from 'axios';
import { useQueryClient } from '@tanstack/react-query';

import {
  useOpenReviewQueue,
  usePendingExpenseSummaries,
  type ReviewQueueItem,
} from '../../api/hooks/useReviewQueue';
import {
  useResolveQueueItem,
  useRejectQueueItem,
  type ExpensePublic,
} from '../../api/hooks/useExpenses';
import { useJobs } from '../../api/hooks/useJobs';
import { formatMoney } from '../../util/format';
import { formatDateAU } from '../../util/dates';
import { useScaledStyles } from '../../ui/type';
import { tokens } from '../../ui/tokens';
import { StatusBadge } from '../../ui/kit';

/**
 * forey F1 (handoff §2 待你审核卡堆): triage the queue IN PLACE on the
 * Today page instead of walking a list.
 *
 * Only the queue HEAD renders as a full card; the rest is implied by
 * two stacked bars + a "{n} more" footer. Approve/reject dequeue
 * optimistically — the next card is up before the server answers — and
 * a failure puts the card back with a message.
 *
 * Two review findings shaped this:
 *  - The "books are clean" card is gated on the QUEUE being empty, not
 *    on the join being empty. A failed summaries fetch used to erase
 *    every row and render the green all-clear over a queue full of
 *    pending money — a lie about the books.
 *  - A queue row whose expense summary is missing (the 500-item
 *    summaries cap; the queue is ordered oldest-first, so the head is
 *    exactly what falls off) is still COUNTED and still shown, as a
 *    degraded card that opens the detail. It is never silently dropped.
 *
 * Admin-only by construction: the parent gates on role, and both
 * mutations are admin-only server-side regardless.
 */

type Row = { queue: ReviewQueueItem; expense: ExpensePublic | null };

/** After a dequeue the next card mounts under the user's finger with
 *  its buttons in the same place. Without this window a double-tap on
 *  通过 approves a SECOND, unseen expense. Same guard family as the
 *  capture submit lock / useOneShotBack. */
const ADVANCE_LOCK_MS = 500;

export function ReviewCardStack({
  dequeued,
  onDequeue,
  onRestore,
  onToast,
}: {
  /** Review ids removed optimistically. Owned by the Today page so the
   *  stat cards count the same queue the stack shows. */
  dequeued: string[];
  onDequeue: (reviewId: string) => void;
  onRestore: (reviewId: string) => void;
  onToast: (text: string) => void;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const router = useRouter();
  const queue = useOpenReviewQueue();
  const summaries = usePendingExpenseSummaries();
  const jobs = useJobs();

  const [locked, setLocked] = useState(false);
  const lockTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (lockTimer.current) clearTimeout(lockTimer.current);
    },
    [],
  );
  const lockAdvance = () => {
    setLocked(true);
    if (lockTimer.current) clearTimeout(lockTimer.current);
    lockTimer.current = setTimeout(() => setLocked(false), ADVANCE_LOCK_MS);
  };

  const expenseById = useMemo(() => {
    const m = new Map<string, ExpensePublic>();
    summaries.data?.items.forEach((e) => m.set(e.expense_id, e));
    return m;
  }, [summaries.data]);

  // Every open queue row survives the join; a missing summary only
  // costs the row its details, never its existence.
  const rows = useMemo<Row[]>(
    () =>
      (queue.data ?? [])
        .filter((q) => !dequeued.includes(q.review_id))
        .map((q) => ({ queue: q, expense: expenseById.get(q.expense_id) ?? null })),
    [queue.data, expenseById, dequeued],
  );

  const jobName = (id: string) =>
    jobs.data?.find((j) => j.job_id === id)?.job_name ?? '';

  if (queue.isLoading) {
    return (
      <View style={s.loadingBox} testID="home-stack-loading">
        <ActivityIndicator color={tokens.primary} />
      </View>
    );
  }
  // X-2 house rule: blank to an error only with NO cached data to show.
  if (queue.isError && !queue.data) {
    return (
      <View style={s.card} testID="home-stack-error">
        <Text style={s.errorText}>{t('home.queue_error')}</Text>
      </View>
    );
  }
  // "Books are clean" requires POSITIVE evidence: a loaded, empty
  // queue. Never an empty join.
  if (queue.data && rows.length === 0) {
    return (
      <View style={[s.card, s.doneCard]} testID="home-stack-clear">
        <View style={s.doneTick}>
          <Text style={s.doneTickText}>{'✓'}</Text>
        </View>
        <Text style={s.doneText}>{t('home.all_clear')}</Text>
      </View>
    );
  }
  if (!queue.data) return null;

  const head = rows[0];
  const behind = rows.length - 1;

  return (
    <View testID="home-review-stack">
      <Text style={s.sectionTitle}>{t('home.for_your_review')}</Text>
      <HeadCard
        key={head.queue.review_id}
        row={head}
        jobName={head.expense ? jobName(head.expense.job_id) : ''}
        locked={locked}
        onDone={(id) => {
          lockAdvance();
          onDequeue(id);
        }}
        onRestore={onRestore}
        onToast={onToast}
        onOpen={() =>
          router.push(
            `/expenses/${head.queue.expense_id}` as unknown as Href,
          )
        }
      />
      {behind >= 1 ? <View style={[s.stackBar, s.stackBar1]} /> : null}
      {behind >= 2 ? <View style={[s.stackBar, s.stackBar2]} /> : null}
      <Text style={s.stackFoot}>
        {behind > 0
          ? t('home.more_pending', { count: behind })
          : t('home.last_one')}
      </Text>
    </View>
  );
}

function HeadCard({
  row,
  jobName,
  locked,
  onDone,
  onRestore,
  onToast,
  onOpen,
}: {
  row: Row;
  jobName: string;
  locked: boolean;
  onDone: (reviewId: string) => void;
  onRestore: (reviewId: string) => void;
  onToast: (text: string) => void;
  onOpen: () => void;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const qc = useQueryClient();
  const reviewId = row.queue.review_id;
  // Hooks bind to THIS review id; the card is keyed by it upstream, so
  // a new head remounts with correctly-bound mutations.
  const approve = useResolveQueueItem(reviewId);
  const reject = useRejectQueueItem(reviewId);
  const expense = row.expense;
  const busy = approve.isPending || reject.isPending || locked;

  const money = expense ? formatMoney(expense.amount_inc_gst) : '';

  const run = async (
    kind: 'approve' | 'reject',
    fire: () => Promise<unknown>,
  ) => {
    if (busy) return;
    onDone(reviewId); // optimistic: next card up immediately
    try {
      await fire();
      onToast(
        t(kind === 'approve' ? 'toast.approved' : 'toast.rejected', {
          sum: money,
        }),
      );
    } catch (err) {
      // 409/404 mean the row is already closed or gone — someone else
      // handled it, or a retry landed twice. Restoring the card would
      // hand the user a ghost whose buttons can only 409 again. Keep it
      // dequeued and refetch so the list settles on server truth.
      const status = axios.isAxiosError(err)
        ? err.response?.status
        : undefined;
      if (status === 409 || status === 404) {
        void qc.invalidateQueries({ queryKey: ['review-queue'] });
        void qc.invalidateQueries({ queryKey: ['expenses'] });
        onToast(t('review.already_handled'));
        return;
      }
      onRestore(reviewId);
      onToast(
        t(kind === 'approve' ? 'review.approve_failed' : 'review.reject_failed'),
      );
    }
  };

  // Degraded: the queue row is real but its expense details didn't
  // load. Show that honestly and send the user to the detail screen —
  // approving an amount you can't see is not an option we offer.
  if (!expense) {
    return (
      <Pressable
        style={({ pressed }) => [s.card, pressed && s.pressed]}
        onPress={onOpen}
        accessibilityRole="button"
        testID={`home-review-card-${reviewId}`}
      >
        <Text style={s.degradedTitle}>{t('home.item_unavailable')}</Text>
        <Text style={s.meta}>{t('home.item_unavailable_hint')}</Text>
      </Pressable>
    );
  }

  return (
    <View style={s.card} testID={`home-review-card-${reviewId}`}>
      <Pressable onPress={onOpen} accessibilityRole="button" disabled={busy}>
        <View style={s.cardTop}>
          <Text
            style={s.amount}
            numberOfLines={1}
            adjustsFontSizeToFit
            minimumFontScale={0.7}
          >
            {money}
          </Text>
          {/* Fidelity §2: the badge names the EXCEPTION (项目不确定 /
              疑似重复 …), not a generic 待审核 — that's what the operator
              needs to triage from. First reason wins the badge slot. */}
          {row.queue.review_reasons.length > 0 ? (
            <View
              style={[
                s.reasonBadge,
                row.queue.review_reasons[0] === 'duplicate_suspected' &&
                  s.reasonBadgeDup,
              ]}
            >
              <Text
                style={[
                  s.reasonBadgeText,
                  row.queue.review_reasons[0] === 'duplicate_suspected' &&
                    s.reasonBadgeTextDup,
                ]}
                numberOfLines={1}
              >
                {t(`review_reason.${row.queue.review_reasons[0]}`, {
                  defaultValue: row.queue.review_reasons[0],
                })}
              </Text>
            </View>
          ) : (
            <StatusBadge
              status={expense.review_status}
              label={t(`expense.status_${expense.review_status}`, {
                defaultValue: expense.review_status,
              })}
            />
          )}
        </View>
        <Text style={s.desc} numberOfLines={2}>
          {expense.raw_input_text || expense.description || '—'}
        </Text>
        <Text style={s.meta} numberOfLines={1}>
          {[jobName, formatDateAU(expense.expense_date)]
            .filter(Boolean)
            .join(' · ')}
        </Text>
      </Pressable>

      <View style={s.actions}>
        <Pressable
          style={({ pressed }) => [s.btn, s.btnGhost, pressed && s.pressed]}
          onPress={() => void run('reject', () => reject.mutateAsync())}
          disabled={busy}
          accessibilityRole="button"
          testID="home-review-reject"
        >
          <Text style={[s.btnText, s.btnTextReject]} numberOfLines={1}>
            {t('review.reject')}
          </Text>
        </Pressable>
        {/* 改项目 opens the expense detail, which owns the corrections
            flow (job/supplier/category + resolve). Duplicating that
            sheet here would fork the one place those edits happen. */}
        <Pressable
          style={({ pressed }) => [s.btn, s.btnGhost, pressed && s.pressed]}
          onPress={onOpen}
          disabled={busy}
          accessibilityRole="button"
          testID="home-review-fix"
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
          onPress={() => void run('approve', () => approve.mutateAsync())}
          disabled={busy}
          accessibilityRole="button"
          testID="home-review-approve"
        >
          <Text style={[s.btnText, s.btnTextApprove]} numberOfLines={1}>
            {t('review.approve')}
          </Text>
        </Pressable>
      </View>
    </View>
  );
}

const base = StyleSheet.create({
  sectionTitle: {
    fontSize: 16,
    fontWeight: '800',
    color: tokens.ink,
    marginBottom: 8,
  },
  loadingBox: { paddingVertical: 28, alignItems: 'center' },
  card: {
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 16,
    padding: 14,
    shadowColor: '#101828',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.06,
    shadowRadius: 16,
    elevation: 2,
  },
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
  desc: { fontSize: 14, color: tokens.ink2, marginTop: 6, lineHeight: 19 },
  meta: { fontSize: 12, color: tokens.muted, marginTop: 4 },
  degradedTitle: { fontSize: 15, fontWeight: '700', color: tokens.ink },
  reasonBadge: {
    paddingHorizontal: 9,
    paddingVertical: 3,
    borderRadius: 999,
    borderWidth: 1,
    backgroundColor: tokens.warnBg,
    borderColor: tokens.warnBorder,
    flexShrink: 1,
  },
  reasonBadgeText: { fontSize: 10.5, fontWeight: '700', color: tokens.warn },
  reasonBadgeDup: {
    backgroundColor: tokens.badBg,
    borderColor: tokens.badBorder,
  },
  reasonBadgeTextDup: { color: tokens.bad },
  actions: { flexDirection: 'row', gap: 8, marginTop: 12 },
  btn: {
    height: 42,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 6,
  },
  btnGhost: {
    flex: 1,
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
  },
  // The one filled action on this card — green because it means "done",
  // not because it's the primary CTA (blue stays the app's action
  // colour; §用色纪律).
  btnApprove: { flex: 1.4, backgroundColor: tokens.okFill },
  btnText: { fontSize: 14, fontWeight: '700' },
  btnTextReject: { color: tokens.bad },
  btnTextGhost: { color: tokens.ink2 },
  btnTextApprove: { color: '#ffffff' },
  pressed: { opacity: 0.75 },
  stackBar: {
    height: 8,
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
    borderBottomLeftRadius: 12,
    borderBottomRightRadius: 12,
    borderTopWidth: 0,
    alignSelf: 'center',
  },
  stackBar1: { width: '94%' },
  stackBar2: { width: '88%', height: 7 },
  stackFoot: {
    fontSize: 12,
    color: tokens.muted,
    textAlign: 'center',
    marginTop: 8,
  },
  doneCard: { alignItems: 'center', paddingVertical: 22, gap: 10 },
  doneTick: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: tokens.okBg,
    borderWidth: 1,
    borderColor: tokens.okBorder,
    alignItems: 'center',
    justifyContent: 'center',
  },
  doneTickText: { fontSize: 22, color: tokens.ok, fontWeight: '800' },
  doneText: { fontSize: 14, fontWeight: '600', color: tokens.ink2 },
  errorText: { fontSize: 13.5, color: tokens.ink3, textAlign: 'center' },
});
