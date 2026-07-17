import { useEffect, useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Alert,
  Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLocalSearchParams, useRouter, type Href } from 'expo-router';
import { useTranslation } from 'react-i18next';
import axios from 'axios';

import {
  useExpense,
  useDeleteExpense,
  useRejectQueueItem,
} from '../../src/api/hooks/useExpenses';
import { useJobs } from '../../src/api/hooks/useJobs';
import { useMe } from '../../src/api/hooks/useAuth';
import type {
  ExpenseDetailPublic,
  ReviewReasonCode,
} from '../../src/api/hooks/useExpenses';
import { ReviewCorrectionsSheet } from '../../src/components/ReviewCorrectionsSheet';
import { formatMoney } from '../../src/util/format';
import { formatDateAU } from '../../src/util/dates';
import { localizeCategoryName } from '../../src/util/category';
import { useScaledStyles } from '../../src/ui/type';
import { StatusBadge } from '../../src/ui/kit';
import { tokens } from '../../src/ui/tokens';

/**
 * Mobile Expense Detail (v1) — read-only.
 *
 * Top-level expo-router route at /expenses/[id], pushed on the root
 * Stack (headers are off app-wide, so it renders its own back
 * chevron). The tab bar is covered while this screen is visible —
 * standard iOS drill-in UX.
 *
 * Hard scope (matches the approved plan):
 *   - read-only fields only
 *   - no edit / delete / resolve / approve / reject / retry-parser
 *   - no receipt / photo upload
 *   - no offline queue
 *
 * ``review_reasons`` semantics (mirrors ADR-equivalent semantics in
 * the backend service): the array reflects the *current*
 * expense_review_queue row's reasons (open / resolved / rejected).
 * Empty when no queue row exists. NOT a historical audit trail —
 * the heading is deliberately "Why this needs review" rather than
 * anything implying a permanent record.
 */

type ReasonColor = { bg: string; fg: string };

// UI-kit v2: the status pill is now src/ui/kit.tsx StatusBadge (which
// keeps the C-04 unknown-enum grey fallback). REASON chips keep their
// local palette below.
const REASON_COLORS: Record<ReviewReasonCode, ReasonColor> = {
  amount_uncertain: { bg: '#fef3c7', fg: '#92400e' },
  unsupported_currency: { bg: '#ffe4e6', fg: '#9f1239' },
  job_uncertain: { bg: '#e0f2fe', fg: '#075985' },
  supplier_uncertain: { bg: '#ede9fe', fg: '#5b21b6' },
  category_uncertain: { bg: '#ccfbf1', fg: '#115e59' },
  duplicate_suspected: { bg: '#fee2e2', fg: '#991b1b' },
};

// Audit C-04: neutral fallback for enum values THIS build doesn't
// know. A newer backend adding a status/reason must degrade to a
// grey chip, not crash the screen on `color.bg` of undefined.
const FALLBACK_COLOR: ReasonColor = { bg: '#f1f5f9', fg: '#475569' };

function isMissing(error: unknown): boolean {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    return status === 404 || status === 403;
  }
  return false;
}

export default function ExpenseDetailScreen() {
  const s = useScaledStyles(base);
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { t } = useTranslation();
  const expense = useExpense(id);
  const jobs = useJobs();
  const me = useMe();
  const deleteMutation = useDeleteExpense(id ?? '');
  const reviewId = expense.data?.pending_review_queue_id ?? '';
  const rejectMutation = useRejectQueueItem(reviewId);
  const [correctOpen, setCorrectOpen] = useState(false);

  const jobName = useMemo(() => {
    if (!expense.data) return undefined;
    return jobs.data?.find((j) => j.job_id === expense.data!.job_id)?.job_name;
  }, [expense.data, jobs.data]);

  // One-shot back guard: unlike the old idempotent replace(), a
  // second router.back() after this screen already popped is handled
  // by the TAB router (backBehavior firstRoute) and yanks the user to
  // the Expenses tab. Two real double-fire windows: confirmThenBack's
  // 900ms timer racing a manual back-chevron tap, and a slow-network
  // delete resolving after the user backed out. First call wins.
  const backFiredRef = useRef(false);
  const onBack = () => {
    if (backFiredRef.current) return;
    backFiredRef.current = true;
    // B4-1: job details is a pushed PAGE, so back() from here lands
    // on whatever pushed us (job page, review queue, list, Home) with
    // no special-casing — the old from=job/selectedJob return path is
    // retired. Fallback covers deep-link entry.
    if (router.canGoBack()) router.back();
    else router.replace('/(tabs)/home' as unknown as Href);
  };

  // O2-B polish #10: brief visible confirmation after Approve / Reject
  // before navigating back — on slow field networks the instant
  // navigation read as "did that save?" and caused double-actions.
  // Non-blocking: a short transient banner, then the normal onBack().
  const [actionDone, setActionDone] = useState<'approved' | 'rejected' | null>(
    null,
  );
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const confirmThenBack = (kind: 'approved' | 'rejected') => {
    setActionDone(kind);
    confirmTimerRef.current = setTimeout(() => onBack(), 900);
  };
  // Clear a pending confirm timer on unmount (the one-shot guard
  // already makes a late fire harmless; this is hygiene).
  useEffect(
    () => () => {
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
    },
    [],
  );

  const onEdit = () => {
    if (!id) return;
    // The typed-routes manifest is regenerated by Metro on file changes,
    // so a static tsc run before the next dev-server start would otherwise
    // reject the new /expenses/[id]/edit path. The cast bridges that gap
    // (same pattern as the row link in RecentCapturesList). Runtime
    // behaviour is unchanged.
    const editHref = `/expenses/${id}/edit` as unknown as Href;
    router.push(editHref);
  };

  // M1: role-aware visibility. `/auth/me` (cached under ['auth','me'])
  // drives VISIBILITY ONLY — defence in depth on top of the backend's
  // require_admin checks, which remain the authority. While the role
  // is still loading, admin-only controls stay hidden until the cached
  // role arrives (failing closed beats flashing admin affordances at
  // contributors).
  const isAdmin = me.data?.role === 'admin';

  // P4: Edit button visibility — only enabled once the row has loaded
  // AND the row is not rejected. Edit is NOT admin-only: contributors
  // may edit their own pending rows; the backend service enforces the
  // real rules (contributor = own row + pending only; admin = any row,
  // any status — `reason` is OPTIONAL audit metadata, never required.
  // M1 corrected an earlier claim here that reviewed-row edits 403
  // without a reason; no such backend check exists).
  // Hide the button entirely while loading/erroring to keep header
  // affordances clean and predictable.
  const editEnabled =
    !!expense.data && expense.data.review_status !== 'rejected';
  // Delete visibility (M1): admin-only — DELETE /expenses/{id} sits
  // behind require_admin, so non-admins no longer see a button that
  // can only produce a 403. Rejected rows hide it too (already
  // soft-deleted; the backend treats a repeat delete as an idempotent
  // 204 no-op, but there's nothing useful for the user to do).
  const deleteEnabled =
    !!expense.data && expense.data.review_status !== 'rejected' && isAdmin;
  // Approve / Reject visibility: gated STRICTLY on the presence of
  // pending_review_queue_id (the slice-1A.1 backend field) AND the
  // admin role (M1 — both queue routes are require_admin). Never
  // gate on review_status alone — a historical resolved/rejected
  // queue row would otherwise leak as a callable workflow action,
  // and the backend would return 4xx when mobile tried to POST
  // /review-queue/{id}/resolve on a non-open row.
  const approveRejectEnabled =
    !!expense.data?.pending_review_queue_id && isAdmin;

  const handleReviewError = (err: unknown, fallbackKey: string) => {
    const status = axios.isAxiosError(err) ? err.response?.status : undefined;
    let message: string;
    if (status === 403) {
      message = t('expense.review_forbidden');
    } else {
      const detail = axios.isAxiosError(err)
        ? err.response?.data?.detail
        : undefined;
      message = typeof detail === 'string' ? detail : t(fallbackKey);
    }
    Alert.alert(t('common.error'), message);
  };

  // A3: approving a review item opens the resolve-with-corrections sheet
  // (admin fixes job/supplier/category, then patch + resolve happen
  // atomically in one backend call). Replaces the old empty-patch approve.
  const onApprove = () => {
    if (!approveRejectEnabled) return;
    setCorrectOpen(true);
  };

  const onReject = () => {
    if (!approveRejectEnabled) return;
    Alert.alert(
      t('expense.reject_confirm_title'),
      t('expense.reject_confirm_message'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('expense.reject_cta'),
          style: 'destructive',
          onPress: async () => {
            try {
              await rejectMutation.mutateAsync();
              confirmThenBack('rejected');
            } catch (err) {
              handleReviewError(err, 'expense.reject_error');
            }
          },
        },
      ],
    );
  };

  // M1: shared delete executor. `reasonText` comes from the iOS
  // Alert.prompt input; it is OPTIONAL — the backend's `reason` query
  // param is audit-only metadata, so a blank value simply omits the
  // param and the delete proceeds exactly as before M1.
  const performDelete = async (reasonText?: string) => {
    try {
      const reason = reasonText?.trim();
      await deleteMutation.mutateAsync(reason ? { reason } : {});
      // Cache invalidation in onSuccess refreshes all
      // affected lists; navigate back to whichever screen
      // brought the user here.
      onBack();
    } catch (err) {
      const status = axios.isAxiosError(err)
        ? err.response?.status
        : undefined;
      let message: string;
      if (status === 403) {
        message = t('expense.delete_forbidden');
      } else {
        const detail = axios.isAxiosError(err)
          ? err.response?.data?.detail
          : undefined;
        message =
          typeof detail === 'string' ? detail : t('expense.delete_error');
      }
      Alert.alert(t('common.error'), message);
    }
  };

  const onDelete = () => {
    if (!id) return;
    if (Platform.OS === 'ios') {
      // M1: Alert.prompt is iOS-only (RN). The free-text field captures
      // an optional audit reason; submitting it empty deletes without
      // a reason — same contract as before M1. iOS-first product: this
      // is the primary path.
      Alert.prompt(
        t('expense.delete_confirm_title'),
        `${t('expense.delete_confirm_message')}\n\n${t('expense.delete_reason_hint')}`,
        [
          { text: t('common.cancel'), style: 'cancel' },
          {
            text: t('expense.delete_cta'),
            style: 'destructive',
            onPress: (text?: string) => void performDelete(text),
          },
        ],
        'plain-text',
      );
      return;
    }
    // Non-iOS fallback: keep the pre-M1 two-button confirm without a
    // reason input. Contract-compliant because reason is optional;
    // react-native-web's Alert is effectively a no-op anyway (existing
    // precedent on this screen).
    Alert.alert(
      t('expense.delete_confirm_title'),
      t('expense.delete_confirm_message'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('expense.delete_cta'),
          style: 'destructive',
          onPress: () => void performDelete(),
        },
      ],
    );
  };

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <View style={s.header}>
        <Pressable
          onPress={onBack}
          hitSlop={12}
          testID="detail-back"
          accessibilityRole="button"
          accessibilityLabel={t('expense.back')}
          style={({ pressed }) => [s.backBtn, pressed && s.backBtnPressed]}
        >
          <Text style={s.backChevron}>{'‹'}</Text>
          <Text style={s.backLabel}>{t('expense.back')}</Text>
        </Pressable>
        <Text style={s.headerTitle} numberOfLines={1}>
          {t('expense.title')}
        </Text>
        {editEnabled ? (
          <Pressable
            onPress={onEdit}
            hitSlop={12}
            testID="detail-edit"
            accessibilityRole="button"
            accessibilityLabel={t('expense.edit')}
            style={({ pressed }) => [s.editBtn, pressed && s.editBtnPressed]}
          >
            <Text style={s.editLabel}>{t('expense.edit')}</Text>
          </Pressable>
        ) : (
          <View style={s.headerSpacer} />
        )}
      </View>

      {expense.isLoading ? (
        <View style={s.state} testID="detail-loading">
          <ActivityIndicator color="#1e293b" />
          <Text style={s.stateText}>{t('common.loading')}</Text>
        </View>
      ) : expense.isError && isMissing(expense.error) ? (
        <View style={s.state} testID="detail-notfound">
          <Text style={s.stateText}>{t('expense.not_found')}</Text>
          <Pressable
            onPress={onBack}
            style={({ pressed }) => [s.linkBtn, pressed && s.linkBtnPressed]}
            accessibilityRole="button"
          >
            <Text style={s.linkBtnText}>{t('expense.back')}</Text>
          </Pressable>
        </View>
      ) : expense.isError ? (
        <View style={s.state} testID="detail-error">
          <Text style={[s.stateText, s.errorText]}>{t('expense.detail_error')}</Text>
          <Pressable
            onPress={() => void expense.refetch()}
            style={({ pressed }) => [s.linkBtn, pressed && s.linkBtnPressed]}
            accessibilityRole="button"
            testID="detail-retry"
          >
            <Text style={s.linkBtnText}>{t('common.retry')}</Text>
          </Pressable>
        </View>
      ) : expense.data ? (
        <ScrollView contentContainerStyle={s.scroll} testID="detail-content">
          <DetailBody
            data={expense.data}
            jobName={jobName}
            onEdit={onEdit}
            editEnabled={editEnabled}
            onDelete={onDelete}
            deleteEnabled={deleteEnabled}
            onApprove={onApprove}
            onReject={onReject}
            approveRejectEnabled={approveRejectEnabled}
            isAdmin={isAdmin}
          />
        </ScrollView>
      ) : null}
      {expense.data ? (
        <ReviewCorrectionsSheet
          visible={correctOpen}
          onClose={() => setCorrectOpen(false)}
          reviewId={reviewId}
          expense={expense.data}
          onResolved={() => {
            setCorrectOpen(false);
            confirmThenBack('approved');
          }}
        />
      ) : null}
      {/* O2-B polish #10: transient success banner. */}
      {actionDone ? (
        <View style={s.actionToast} pointerEvents="none" testID="review-action-toast">
          <Text style={s.actionToastText}>
            {t(
              actionDone === 'approved'
                ? 'expense.approve_success'
                : 'expense.reject_success',
            )}
          </Text>
        </View>
      ) : null}
    </SafeAreaView>
  );
}

function DetailBody({
  data,
  jobName,
  onEdit,
  editEnabled,
  onDelete,
  deleteEnabled,
  onApprove,
  onReject,
  approveRejectEnabled,
  isAdmin,
}: {
  data: ExpenseDetailPublic;
  jobName: string | undefined;
  onEdit: () => void;
  editEnabled: boolean;
  onDelete: () => void;
  deleteEnabled: boolean;
  onApprove: () => void;
  onReject: () => void;
  approveRejectEnabled: boolean;
  isAdmin: boolean;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const reasons = data.review_reasons ?? [];
  const showReasons =
    (data.review_status === 'pending' || data.review_status === 'rejected') &&
    reasons.length > 0;

  const paymentLabel =
    data.payment_method === 'cash'
      ? t('capture.payment_cash')
      : data.payment_method === 'transfer'
        ? t('capture.payment_transfer')
        : t('capture.payment_unknown');

  const receiptLabel =
    data.receipt_status === 'expected_later'
      ? t('expense.receipt_expected_later')
      : t('expense.receipt_no_receipt');

  const supplierName = data.supplier?.supplier_name ?? '—';
  const categoryName = localizeCategoryName(data.category?.category_name, t);
  const jobDisplay = jobName ?? data.job_id.slice(0, 8);

  return (
    <>
      <View style={s.hero}>
        <View style={s.heroLeft}>
          <Text style={s.heroAmount} testID="detail-amount">
            {formatMoney(data.amount_inc_gst)}
          </Text>
          {/* P3: expense_date is promoted out of the grid into the
              hero so the date the expense applies to is the second
              thing the eye lands on, after the amount. Uses the AU
              DD/MM/YYYY display form per the i18n contract. */}
          <Text style={s.heroDate} testID="detail-date">
            {formatDateAU(data.expense_date)}
          </Text>
        </View>
        <StatusBadge
          status={data.review_status}
          label={t(`expense.status_${data.review_status}`, {
            defaultValue: data.review_status,
          })}
          testID="detail-status"
        />
      </View>

      {/* Edit-discoverability slice: dogfooding showed the header
          'Edit' button wasn't naturally found at the moment users
          notice something wrong. For a correction-centric workflow,
          the edit affordance must be obvious — not just present.
          This body-level CTA sits immediately under the hero so the
          eye lands on it right after reading the status pill.
          Same visibility rule as the header button (hidden on
          rejected); both routes navigate to the same edit screen. */}
      {editEnabled ? (
        <Pressable
          onPress={onEdit}
          testID="detail-edit-cta"
          accessibilityRole="button"
          accessibilityLabel={t('expense.edit_cta')}
          style={({ pressed }) => [s.editCTA, pressed && s.editCTAPressed]}
        >
          <Text style={s.editCTAText}>{t('expense.edit_cta')}</Text>
        </Pressable>
      ) : null}

      {/* Slice 1A.2: Approve / Reject workflow actions, visible only
          when the expense has an actionable open review queue row
          (pending_review_queue_id present). Side-by-side layout
          places Approve as the positive primary-ish action (green
          fill) and Reject as the destructive outlined action (red
          border, red text). Delete remains visually separate at the
          bottom of the screen — these workflow actions don't replace
          the destructive escape valve. */}
      {approveRejectEnabled ? (
        <View style={s.reviewActions}>
          <Pressable
            onPress={onApprove}
            testID="detail-approve-cta"
            accessibilityRole="button"
            accessibilityLabel={t('expense.approve_cta')}
            style={({ pressed }) => [
              s.approveBtn,
              pressed && s.approveBtnPressed,
            ]}
          >
            <Text style={s.approveBtnText}>{t('expense.approve_cta')}</Text>
          </Pressable>
          <Pressable
            onPress={onReject}
            testID="detail-reject-cta"
            accessibilityRole="button"
            accessibilityLabel={t('expense.reject_cta')}
            style={({ pressed }) => [
              s.rejectBtn,
              pressed && s.rejectBtnPressed,
            ]}
          >
            <Text style={s.rejectBtnText}>{t('expense.reject_cta')}</Text>
          </Pressable>
        </View>
      ) : null}

      <View style={s.grid}>
        {/* O1-S1: ex-GST / GST are admin-only. Contributors never render
            them (the backend also server-strips them to null). */}
        {isAdmin ? (
          <>
            <Field label={t('expense.amount_ex_gst')} value={formatMoney(data.amount_ex_gst)} />
            <Field label={t('expense.gst')} value={formatMoney(data.gst_amount)} />
          </>
        ) : null}
        <Field label={t('expense.payment')} value={paymentLabel} />
        <Field label={t('expense.supplier')} value={supplierName} />
        <Field label={t('expense.category')} value={categoryName} />
        <Field label={t('expense.job')} value={jobDisplay} />
        <Field label={t('expense.receipt_status')} value={receiptLabel} />
      </View>

      {showReasons && (
        <View style={s.section} testID="detail-reasons">
          <Text style={s.sectionHeading}>{t('expense.review_reasons_heading')}</Text>
          {/* Mobile Polish slice: one-line plain-language nudge below
              the heading so a contributor scanning the detail screen
              gets an explanation of what's expected to happen next.
              Pending = waiting on admin; rejected = already-rejected.
              The reasons chips below still carry the parser's
              uncertainty signal. */}
          <Text style={s.reasonHelp}>
            {data.review_status === 'rejected'
              ? t('expense.review_status_help_rejected')
              : t('expense.review_status_help_pending')}
          </Text>
          <View style={s.chipsRow}>
            {reasons.map((code) => {
              const color = REASON_COLORS[code] ?? FALLBACK_COLOR;
              return (
                <View
                  key={code}
                  style={[s.chip, { backgroundColor: color.bg }]}
                  testID={`detail-reason-${code}`}
                >
                  <Text style={[s.chipText, { color: color.fg }]}>
                    {t(`review_reason.${code}`, { defaultValue: code })}
                  </Text>
                </View>
              );
            })}
          </View>
        </View>
      )}

      {(data.duplicate_flag || data.duplicate_of_expense_id) && (
        <View style={[s.section, s.dupBanner]} testID="detail-duplicate">
          {data.duplicate_flag && (
            <Text style={s.dupLine}>{t('capture.recent.duplicate_flag')}</Text>
          )}
          {data.duplicate_of_expense_id && (
            <Text style={s.dupRef}>
              {t('expense.duplicate_of')}: {data.duplicate_of_expense_id.slice(0, 8)}…
            </Text>
          )}
        </View>
      )}

      {data.description ? (
        <View style={s.section}>
          <Text style={s.sectionHeading}>{t('expense.description')}</Text>
          <Text style={s.longText}>{data.description}</Text>
        </View>
      ) : null}

      {data.notes ? (
        <View style={s.section}>
          <Text style={s.sectionHeading}>{t('expense.notes')}</Text>
          <Text style={s.longText}>{data.notes}</Text>
        </View>
      ) : null}

      {data.raw_input_text ? (
        <View style={s.section}>
          <Text style={s.sectionHeading}>{t('expense.raw_input')}</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            <Text style={s.rawText}>{data.raw_input_text}</Text>
          </ScrollView>
        </View>
      ) : null}

      {/* Delete (soft-delete via DELETE /expenses/{id}; backend sets
          review_status='rejected' + writes audit). Visually
          subordinate to the prominent Edit CTA at the top: red text
          link at the very end of the detail body, far from primary
          actions to prevent fat-fingering. Confirmation dialog
          explicitly explains the soft-delete + audit-retention
          semantic so the user understands they're not destroying
          historical data. */}
      {deleteEnabled ? (
        <Pressable
          onPress={onDelete}
          testID="detail-delete"
          accessibilityRole="button"
          accessibilityLabel={t('expense.delete_cta')}
          style={({ pressed }) => [s.deleteBtn, pressed && s.deleteBtnPressed]}
        >
          <Text style={s.deleteBtnText}>{t('expense.delete_cta')}</Text>
        </Pressable>
      ) : null}
    </>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  const s = useScaledStyles(base);
  return (
    <View style={s.field}>
      <Text style={s.fieldLabel}>{label}</Text>
      <Text style={s.fieldValue} numberOfLines={2}>
        {value}
      </Text>
    </View>
  );
}

const base = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  // O2-B polish #10: transient approve/reject confirmation pill.
  actionToast: {
    position: 'absolute',
    bottom: 32,
    alignSelf: 'center',
    backgroundColor: '#1e293b',
    borderRadius: 20,
    paddingHorizontal: 18,
    paddingVertical: 10,
  },
  actionToastText: { color: '#ffffff', fontSize: 15, fontWeight: '600' },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
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
  backChevron: { fontSize: 28, color: '#1e293b', marginRight: 4, lineHeight: 28 },
  backLabel: { fontSize: 16, color: '#1e293b' },
  headerTitle: {
    flex: 1,
    textAlign: 'center',
    fontSize: 17,
    fontWeight: '600',
    color: '#0f172a',
  },
  headerSpacer: { width: 72 },
  editBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'flex-end',
    paddingHorizontal: 8,
    paddingVertical: 4,
    minWidth: 72,
  },
  editBtnPressed: { opacity: 0.5 },
  editLabel: { fontSize: 16, color: '#1e293b', fontWeight: '600' },
  // Body-level Edit CTA: prominent primary button right under the hero.
  // Matches the dark-slate visual weight of the Save button on the edit
  // screen so the path from "I notice a wrong value" -> "edit it" is
  // visually consistent.
  editCTA: {
    backgroundColor: '#1e293b',
    paddingVertical: 12,
    borderRadius: 6,
    alignItems: 'center',
    marginTop: 4,
  },
  editCTAPressed: { opacity: 0.6 },
  editCTAText: { color: '#ffffff', fontSize: 16, fontWeight: '600' },
  // Slice 1A.2: Approve / Reject workflow actions row.
  reviewActions: {
    flexDirection: 'row',
    gap: 8,
    marginTop: 4,
  },
  approveBtn: {
    flex: 1,
    backgroundColor: '#15803d',
    paddingVertical: 12,
    borderRadius: 6,
    alignItems: 'center',
  },
  approveBtnPressed: { opacity: 0.6 },
  approveBtnText: { color: '#ffffff', fontSize: 16, fontWeight: '600' },
  rejectBtn: {
    flex: 1,
    borderWidth: 1,
    borderColor: '#dc2626',
    backgroundColor: '#ffffff',
    paddingVertical: 12,
    borderRadius: 6,
    alignItems: 'center',
  },
  rejectBtnPressed: { opacity: 0.5, backgroundColor: '#fef2f2' },
  rejectBtnText: { color: '#b91c1c', fontSize: 16, fontWeight: '600' },
  // Delete (soft-delete) — visually subordinate to Edit CTA. Red text
  // on a white background with a subtle red border so it reads as
  // destructive but doesn't shout. Sits at the bottom of the scroll
  // content, well below all data fields.
  deleteBtn: {
    borderWidth: 1,
    borderColor: '#fecaca',
    backgroundColor: '#ffffff',
    paddingVertical: 12,
    borderRadius: 6,
    alignItems: 'center',
    marginTop: 24,
  },
  deleteBtnPressed: { opacity: 0.5, backgroundColor: '#fef2f2' },
  deleteBtnText: { color: '#b91c1c', fontSize: 15, fontWeight: '600' },
  state: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, gap: 12 },
  stateText: { color: '#64748b', fontSize: 15 },
  errorText: { color: '#b91c1c' },
  linkBtn: { paddingHorizontal: 12, paddingVertical: 8 },
  linkBtnPressed: { opacity: 0.5 },
  linkBtnText: { color: '#1e293b', fontSize: 15, fontWeight: '600' },
  scroll: { padding: 16, gap: 20, backgroundColor: tokens.surface },
  hero: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  heroLeft: { flex: 1, gap: 4, paddingRight: 12 },
  heroAmount: {
    fontSize: 32,
    fontWeight: '700',
    color: '#0f172a',
    fontVariant: ['tabular-nums'],
  },
  heroDate: {
    fontSize: 15,
    color: '#475569',
    fontVariant: ['tabular-nums'],
  },
  grid: { flexDirection: 'row', flexWrap: 'wrap', marginHorizontal: -8 },
  field: { width: '50%', paddingHorizontal: 8, paddingVertical: 8 },
  fieldLabel: {
    fontSize: 11,
    color: '#64748b',
    textTransform: 'uppercase',
    marginBottom: 4,
    fontWeight: '600',
  },
  fieldValue: { fontSize: 15, color: '#0f172a' },
  section: { gap: 8 },
  sectionHeading: {
    fontSize: 13,
    color: '#475569',
    fontWeight: '600',
    textTransform: 'uppercase',
  },
  chipsRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  chip: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
  chipText: { fontSize: 12, fontWeight: '600' },
  reasonHelp: { color: '#475569', fontSize: 14, lineHeight: 20 },
  dupBanner: {
    backgroundColor: '#fef3c7',
    borderColor: '#fde68a',
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
  },
  dupLine: { color: '#92400e', fontSize: 14, fontWeight: '600' },
  dupRef: { color: '#92400e', fontSize: 13, marginTop: 4, fontVariant: ['tabular-nums'] },
  longText: { color: '#0f172a', fontSize: 15, lineHeight: 21 },
  rawText: {
    color: '#1e293b',
    fontSize: 12,
    fontFamily: 'Menlo',
    backgroundColor: '#f8fafc',
    borderColor: '#e2e8f0',
    borderWidth: 1,
    borderRadius: 6,
    padding: 8,
  },
});
