import { useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  ActivityIndicator,
  StyleSheet,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  Keyboard,
  RefreshControl,
  Switch,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import {
  useCreateExpense,
  useMyRecentExpenses,
  type ExpenseCreateInput,
  type ExpenseCreateResponse,
  type PaymentMethod,
  type ReceiptStatus,
} from '../src/api/hooks/useExpenses';
import { resolveApiErrorMessage } from '../src/api/errors';
import { useMe } from '../src/api/hooks/useAuth';
import {
  useJobs,
  useJobZhAliasMap,
  type JobPublic,
} from '../src/api/hooks/useJobs';
import { CaptureResultCard } from '../src/components/CaptureResultCard';
import { JobPickerSheet } from '../src/components/JobPickerSheet';
import { RecentCapturesList } from '../src/components/RecentCapturesList';
import { RecentFailuresList } from '../src/components/RecentFailuresList';
import { DatePills } from '../src/components/DatePills';
import { useFailuresStore } from '../src/store/failures';
import { todayISO } from '../src/util/dates';
import { hasCJK } from '../src/util/text';
import { useScaledStyles } from '../src/ui/type';
import { Chip } from '../src/ui/kit';
import { tokens } from '../src/ui/tokens';
import { useOneShotBack } from '../src/util/navigation';
import type { Href } from 'expo-router';

/**
 * Mobile Capture v0: natural-language expense capture screen.
 *
 * State machine is driven by `useMutation` flags + a single `result`
 * value. No reducer.
 *
 * Mirrors the shape of `admin/src/pages/Capture.tsx` (also v0-scoped to
 * raw_input_text + payment + receipt-later) but in React Native. The
 * conditional-spread body builder is the documented workaround for
 * the Pydantic `model_fields_set` 422 trap — sending explicit `null`
 * for unset optional fields marks them as "caller-set" and overrides
 * the parser's value, causing spurious "Amount is required" 422s.
 */

// F4: payment selection. `null` = nothing picked -> payment_method is
// OMITTED from the POST so the backend parser infers it (fallback
// 'unknown'). We deliberately never default to 'cash' (cash forces
// gst=0). "Auto" is gone from the UI; the parser still infers under
// the hood when the user leaves it unset.
type PaymentSel = 'cash' | 'transfer' | null;

/**
 * Aggregated result of a multi-item capture submission.
 *
 * Path A (mobile-only) approach: mobile splits raw_input_text on
 * newlines, treats the first line as a shared preamble when it has
 * no `$` (e.g. just a job ref like `003`), prepends it to each item
 * line, and fires N POST /expenses calls in parallel. Each item's
 * settled state (saved or error) is captured here so the result card
 * can render per-row status. Backend untouched — every item goes
 * through the existing single-expense pipeline.
 */
type MultiCaptureItem = {
  text: string;
  success: boolean;
  expense?: ExpenseCreateResponse['expense'];
  reviewPending?: boolean;
  error?: string;
};

type MultiCaptureResult = {
  items: MultiCaptureItem[];
  preamble: string | null;
};

export default function CaptureScreen() {
  const s = useScaledStyles(base);

  const { t, i18n } = useTranslation();
  // B2 (IA rework): capture is a PUSHED screen now (central ➕), so it
  // renders its own back header. One-shot back; deep-link fallback to
  // the Home tab.
  const onBackHome = useOneShotBack('/(tabs)/home' as unknown as Href);
  const screenHeader = (
    <View style={s.headerRow}>
      <TouchableOpacity
        onPress={onBackHome}
        hitSlop={12}
        testID="capture-back"
        accessibilityRole="button"
        style={s.backBtn}
      >
        <Text style={s.backChevron}>{'‹'}</Text>
      </TouchableOpacity>
      <Text style={s.title}>{t('capture.title')}</Text>
      <View style={s.backSpacer} />
    </View>
  );
  const createExpense = useCreateExpense();
  // Mobile Capture v1 Sub-batch A: "My Captures" list query lives on
  // the parent screen so the same RefreshControl can drive pull-to-
  // refresh from anywhere in the scrollable area (form region or
  // list region). `useCreateExpense` already invalidates the
  // ['expenses'] root, so a successful capture auto-refetches this
  // query without extra wiring.
  //
  // Limit 5 (was 20): operator dogfood signal — on the Capture screen,
  // "My Captures" functions as a quick-access shortcut to recently
  // captured items for correction, not a comprehensive list. 5 is
  // enough for "what did I just enter". Per-job expense list in the
  // Job detail modal stays at 20 (different context: comprehensive
  // per-job view).
  const recentExpenses = useMyRecentExpenses(5);
  // M3: admin-only triage entry. /auth/me drives VISIBILITY ONLY —
  // the review-queue backend routes stay authoritative (403 for
  // contributors). Hidden while the role is loading (fails closed).
  const me = useMe();
  const isAdmin = me.data?.role === 'admin';
  // M0: persisted failed-capture store — failures recorded here stay
  // visible after form reset and app restart (see src/store/failures).
  const recordFailure = useFailuresStore((st) => st.recordFailure);
  const textareaRef = useRef<TextInput>(null);

  const [rawInputText, setRawInputText] = useState('');
  const [paymentSel, setPaymentSel] = useState<PaymentSel>(null);
  // O2-A (feedback #1): explicit job selection. `null` = nothing picked
  // -> job_id is OMITTED from the POST so the backend parser matches the
  // job from the text exactly as before (job_uncertain review still
  // fires on ambiguity). A picked job is sent as an explicit job_id and
  // wins over the parser via the existing structured-wins merge.
  const [jobSel, setJobSel] = useState<string | null>(null);
  const [jobPickerOpen, setJobPickerOpen] = useState(false);
  const [receiptLater, setReceiptLater] = useState(false);
  // P3: expense_date is always set client-side (defaults to today's
  // local ISO) and always sent in the body, so the backend never has
  // to fall back to its own date.today() default for mobile captures.
  // DatePills enforces that this only holds a valid ISO YYYY-MM-DD.
  const [expenseDate, setExpenseDate] = useState<string>(() => todayISO());
  const [formError, setFormError] = useState<string | null>(null);
  const [result, setResult] = useState<ExpenseCreateResponse | null>(null);
  // Multi-item capture (Path A — mobile-only, N parallel API calls).
  // When the user types multi-line input, mobile splits on newlines,
  // treats the first line as a shared preamble if it has no $, then
  // POSTs one expense per item line in parallel. Aggregated result
  // replaces the single-item result card. Backend untouched.
  const [multiResult, setMultiResult] = useState<MultiCaptureResult | null>(
    null,
  );
  const [multiPending, setMultiPending] = useState(false);

  // O2-A job chips: recent-first active jobs, capped small so the row
  // stays scannable (recent-N + "More…", never a wall of chips).
  const jobsQuery = useJobs();
  const activeJobs = useMemo(
    () => (jobsQuery.data ?? []).filter((j) => j.status === 'active'),
    [jobsQuery.data],
  );
  const chipJobs = useMemo(() => {
    const byId = new Map(activeJobs.map((j) => [j.job_id, j]));
    const ordered: JobPublic[] = [];
    // Most-recently-captured jobs first (from the My Captures query).
    for (const e of recentExpenses.data?.items ?? []) {
      const j = byId.get(e.job_id);
      if (j && !ordered.includes(j)) ordered.push(j);
    }
    for (const j of activeJobs) {
      if (!ordered.includes(j)) ordered.push(j);
    }
    return ordered.slice(0, 4);
  }, [activeJobs, recentExpenses.data]);
  // zh alias labels (e.g. "工地1") for the chips + the selected job.
  // Only fetched when the app language is Chinese — English users see
  // job names, which the list response already carries.
  const aliasIds = useMemo(() => {
    const ids = chipJobs.map((j) => j.job_id);
    if (jobSel && !ids.includes(jobSel)) ids.push(jobSel);
    return ids;
  }, [chipJobs, jobSel]);
  const zhAliasMap = useJobZhAliasMap(aliasIds, i18n.language === 'zh');
  // O2-C (U1): the operator's real Chinese identity often lives in the
  // job CODE ("晶晶"). Label chain for zh users: zh alias → CJK job
  // code → English name. English users keep job_name (the alias fetch
  // is zh-gated, so the map is empty for them).
  const jobLabelFor = (job: JobPublic): string => {
    const alias = zhAliasMap[job.job_id];
    if (alias) return alias;
    if (i18n.language === 'zh' && job.job_code && hasCJK(job.job_code)) {
      return job.job_code;
    }
    return job.job_name;
  };
  // Chips actually rendered: if the sheet picked a job outside the
  // recent-N, surface it as an (active) chip so the selection is visible.
  const displayChips = useMemo(() => {
    if (jobSel === null || chipJobs.some((j) => j.job_id === jobSel)) {
      return chipJobs;
    }
    const selected = activeJobs.find((j) => j.job_id === jobSel);
    return selected ? [selected, ...chipJobs.slice(0, 3)] : chipJobs;
  }, [chipJobs, activeJobs, jobSel]);
  // Result-card job echo: same label chain as the chips (U1) so what
  // the user tapped is what the card echoes back.
  const jobNameFor = (jobId: string): string => {
    const j = jobsQuery.data?.find((x) => x.job_id === jobId);
    if (j) return jobLabelFor(j);
    return zhAliasMap[jobId] ?? jobId.slice(0, 8);
  };

  // X-2 follow-up: explicit "user pulled" flag (house pattern).
  // isRefetching also goes true on focus refetches now that
  // refetchOnWindowFocus is on — driving the spinner from it would
  // pin a phantom pull-spinner on every app resume.
  const [userRefreshing, setUserRefreshing] = useState(false);
  const refreshControl = (
    <RefreshControl
      refreshing={userRefreshing}
      onRefresh={() => {
        setUserRefreshing(true);
        void recentExpenses
          .refetch()
          .finally(() => setUserRefreshing(false));
      }}
      tintColor="#1e293b"
    />
  );

  // Conditional-spread body builder: only fields the user actually
  // set are included. Sending explicit `null` would mark them as
  // caller-set in the backend's `model_fields_set` and cause a 422.
  // See `admin/src/pages/Capture.tsx:98-106` for the canonical
  // version. Shared by single + multi paths.
  type CaptureBody = Omit<ExpenseCreateInput, 'payment_method'> & {
    payment_method?: PaymentMethod;
  };
  const buildBody = (rawText: string): CaptureBody => {
    const body: CaptureBody = {
      raw_input_text: rawText,
      expense_type: 'supplier_expense',
      receipt_status: (receiptLater
        ? 'expected_later'
        : 'no_receipt') as ReceiptStatus,
      expense_date: expenseDate,
    };
    // Unset (null) -> omit payment_method so the parser decides (fallback
    // 'unknown'); never coerce to a default like cash.
    if (paymentSel !== null) body.payment_method = paymentSel;
    // O2-A: explicit job selection wins over the parser (existing
    // structured-wins merge server-side). Unset -> omit so the parser
    // matches from text exactly as before.
    if (jobSel !== null) body.job_id = jobSel;
    return body;
  };

  // Synchronous double-tap guard (audit X-3, mirrors the Labour tab's
  // onSave): isPending/multiPending only flip after a re-render, so
  // two rapid taps could both enter onSubmit and double-POST the same
  // capture — creating two stored rows that evade the backend's
  // duplicate flag on the second insert's race window.
  const savingRef = useRef(false);

  const onSubmit = async () => {
    if (createExpense.isPending || multiPending || savingRef.current) return;
    const trimmed = rawInputText.trim();
    if (trimmed.length === 0) return;
    savingRef.current = true;
    try {
      setFormError(null);
      Keyboard.dismiss();

      // Multi-item detection: split on newlines, filter empty lines.
      const lines = trimmed
        .split('\n')
        .map((l) => l.trim())
        .filter((l) => l.length > 0);

      if (lines.length <= 1) {
        // Single-item path — unchanged behaviour.
        const body = buildBody(trimmed);
        try {
          const resp = await createExpense.mutateAsync(body as ExpenseCreateInput);
          setResult(resp);
        } catch (err) {
          const msg = resolveApiErrorMessage(err, t, t('capture.error_network'));
          setFormError(msg);
          // M0: persist the failed capture (typed text + error message)
          // so it survives reset/restart and can be refilled for retry.
          recordFailure({ inputText: trimmed, errorMessage: msg, context: 'single' });
        }
        return;
      }

      // Multi-item path. First-line preamble detection: a line is a
      // preamble if it has no `$` (operator's pattern is a bare job
      // ref like `003` on line 1). The preamble is prepended to each
      // subsequent item line so the backend parser still receives a
      // complete single-item string with the job context attached.
      // If the first line itself contains `$`, every line is treated
      // as an independent complete item.
      const firstLine = lines[0];
      const hasPreamble = !firstLine.includes('$');
      const itemLines = hasPreamble ? lines.slice(1) : lines;
      const preamble = hasPreamble ? firstLine : null;

      if (itemLines.length === 0) {
        // Preamble-only input — nothing to submit.
        setFormError(t('capture.multi_no_items'));
        return;
      }

      const itemTexts = itemLines.map((line) =>
        preamble ? `${preamble} ${line}` : line,
      );

      setMultiPending(true);
      try {
        // Parallel POSTs. Promise.allSettled-equivalent via per-item
        // try/catch so one failure doesn't drop the entire batch.
        const results = await Promise.all(
          itemTexts.map(async (text): Promise<MultiCaptureItem> => {
            try {
              const resp = await createExpense.mutateAsync(
                buildBody(text) as ExpenseCreateInput,
              );
              return {
                text,
                success: true,
                expense: resp.expense,
                reviewPending: resp.expense.review_status === 'pending',
              };
            } catch (err) {
              const msg = resolveApiErrorMessage(err, t, t('capture.error_network'));
              // M0: persist each failed item for visibility/retry after
              // the result card is dismissed or the app restarts.
              recordFailure({ inputText: text, errorMessage: msg, context: 'multi' });
              return {
                text,
                success: false,
                error: msg,
              };
            }
          }),
        );
        setMultiResult({ items: results, preamble });
      } finally {
        setMultiPending(false);
      }
    } finally {
      savingRef.current = false;
    }
  };

  const onReset = () => {
    setRawInputText('');
    setPaymentSel(null);
    // O2-A: job selection resets with the rest of the form (mirrors
    // payment) — a stale job silently attached to the NEXT capture is
    // exactly the wrong-job risk the chips must not amplify.
    setJobSel(null);
    setReceiptLater(false);
    // P3: reset the date back to today on a fresh capture — anchoring
    // the form on "now" matches the iOS-first on-site flow.
    setExpenseDate(todayISO());
    setFormError(null);
    setResult(null);
    setMultiResult(null);
    setTimeout(() => textareaRef.current?.focus(), 0);
  };

  // M0: put a failed capture's original text back into the form for a
  // retry. Clears any stale error banner; deliberately keeps the
  // payment/date/receipt selections as the user last set them.
  const onRefillFailure = (text: string) => {
    setRawInputText(text);
    setFormError(null);
    setTimeout(() => textareaRef.current?.focus(), 0);
  };

  if (multiResult) {
    return (
      <SafeAreaView style={s.safe} edges={['top', 'bottom', 'left', 'right']}>
        <ScrollView
          contentContainerStyle={s.scroll}
          keyboardShouldPersistTaps="handled"
          refreshControl={refreshControl}
        >
          {screenHeader}
          <MultiCaptureResultCard
            result={multiResult}
            onReset={onReset}
            jobNameFor={jobNameFor}
          />
          <RecentCapturesList
            query={recentExpenses}
            showViewAll
            showPendingTriage={isAdmin}
          />
        </ScrollView>
      </SafeAreaView>
    );
  }

  if (result) {
    return (
      <SafeAreaView style={s.safe} edges={['top', 'bottom', 'left', 'right']}>
        <ScrollView
          contentContainerStyle={s.scroll}
          keyboardShouldPersistTaps="handled"
          refreshControl={refreshControl}
        >
          {screenHeader}
          <CaptureResultCard
            result={result}
            onReset={onReset}
            isAdmin={isAdmin}
            jobName={jobNameFor(result.expense.job_id)}
          />
          <RecentCapturesList
            query={recentExpenses}
            showViewAll
            showPendingTriage={isAdmin}
          />
        </ScrollView>
      </SafeAreaView>
    );
  }

  // Unified in-flight flag: blocks form interaction during BOTH the
  // single-item mutation (createExpense.isPending) and the multi-item
  // parallel batch (multiPending). Used by the submit button + every
  // form input below.
  const inFlight = createExpense.isPending || multiPending;
  const submitDisabled = inFlight || rawInputText.trim().length === 0;

  return (
    <SafeAreaView style={s.safe} edges={['top', 'bottom', 'left', 'right']}>
      <KeyboardAvoidingView
        style={s.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          contentContainerStyle={s.scroll}
          keyboardShouldPersistTaps="handled"
          refreshControl={refreshControl}
        >
          {screenHeader}

          {/* O2-A (feedback #1): tap a job instead of spelling it. Chips
              are recent-first active jobs, labelled with the zh alias
              when one exists. Tapping the active chip deselects (back to
              parser matching). "More…" opens the searchable full list.
              Identity only — no money ever renders here. */}
          {activeJobs.length > 0 ? (
            <View style={s.jobRow}>
              <Text style={s.paymentLabel}>{t('capture.job_label')}</Text>
              {displayChips.map((job) => (
                <PaymentOption
                  key={job.job_id}
                  label={jobLabelFor(job)}
                  active={jobSel === job.job_id}
                  disabled={inFlight}
                  onPress={() =>
                    setJobSel((prev) =>
                      prev === job.job_id ? null : job.job_id,
                    )
                  }
                  testID={`job-chip-${job.job_id}`}
                />
              ))}
              <PaymentOption
                label={
                  activeJobs.length > displayChips.length
                    ? `+${activeJobs.length - displayChips.length}`
                    : t('capture.job_more')
                }
                active={false}
                disabled={inFlight}
                onPress={() => setJobPickerOpen(true)}
                testID="job-more"
              />
            </View>
          ) : null}
          {activeJobs.length > 0 && jobSel === null ? (
            <Text style={s.paymentHint} testID="job-default-hint">
              {t('capture.job_hint')}
            </Text>
          ) : null}

          <TextInput
            ref={textareaRef}
            value={rawInputText}
            onChangeText={setRawInputText}
            placeholder={t('capture.textarea_placeholder')}
            placeholderTextColor="#94a3b8"
            multiline
            // F3 legacy: no autoFocus. The original rationale (capture was
            // the post-login landing tab; keyboard popped on every launch)
            // no longer applies — B2 made this a pushed screen behind the
            // tab-bar ➕. Kept as-is for now; whether ➕ should auto-focus
            // the textarea is an operator UX call (B2 follow-up). The
            // deferred focus calls in onReset / onRefillFailure still work.
            editable={!inFlight}
            style={s.textarea}
            testID="capture-textarea"
            accessibilityLabel={t('capture.title')}
          />

          <DatePills
            value={expenseDate}
            onChange={setExpenseDate}
            disabled={inFlight}
          />

          <View style={s.paymentRow}>
            <Text style={s.paymentLabel}>{t('capture.payment_label')}</Text>
            <PaymentOption
              label={t('capture.payment_cash')}
              active={paymentSel === 'cash'}
              disabled={inFlight}
              onPress={() => setPaymentSel('cash')}
              testID="payment-cash"
            />
            <PaymentOption
              label={t('capture.payment_transfer')}
              active={paymentSel === 'transfer'}
              disabled={inFlight}
              onPress={() => setPaymentSel('transfer')}
              testID="payment-transfer"
            />
          </View>

          {/* O1-S1 #2: when no payment method is picked, make the default
              GST treatment explicit. We keep null -> omit (parser infers
              'unknown' = Including-GST split); we never auto-default to
              Cash and the field stays optional. */}
          {paymentSel === null ? (
            <Text style={s.paymentHint} testID="payment-default-hint">
              {t('capture.payment_default_hint')}
            </Text>
          ) : null}

          {/* Preview-parity: switch row (was a checkbox). */}
          <View style={s.switchRow} testID="receipt-later">
            <Text style={s.checkboxLabel}>{t('capture.receipt_later')}</Text>
            <Switch
              testID="receipt-later-switch"
              value={receiptLater}
              onValueChange={(v) => {
                if (!inFlight) setReceiptLater(v);
              }}
              disabled={inFlight}
              trackColor={{ true: '#93c5fd', false: '#e2e8f0' }}
              thumbColor={receiptLater ? '#2563EB' : '#f8fafc'}
              accessibilityLabel={t('capture.receipt_later')}
            />
          </View>

          {formError ? (
            <View style={s.errorBanner} testID="capture-error">
              <Text style={s.errorText}>{formError}</Text>
            </View>
          ) : null}

          <TouchableOpacity
            onPress={onSubmit}
            disabled={submitDisabled}
            style={[s.submitBtn, submitDisabled && s.submitBtnDisabled]}
            testID="capture-submit"
            accessibilityRole="button"
          >
            {inFlight ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={s.submitBtnText}>{t('capture.submit')}</Text>
            )}
          </TouchableOpacity>

          {/* M0: persisted failed captures (if any) — tap a row to put
              the text back into the form and retry it. Renders nothing
              when there are no stored failures. */}
          <RecentFailuresList onRefill={onRefillFailure} />

          {/* Correction-loop fix: render My Captures below the form even
              in the pre-submit (empty form) state. Previously this list
              only appeared after a successful capture, which meant the
              user had no way to navigate to a past expense detail
              without first capturing a new one. With the list always
              visible, the path "I want to fix something I captured
              earlier" -> scroll -> tap row -> detail -> Edit expense
              is one tap from the default state of this screen. */}
          <RecentCapturesList
            query={recentExpenses}
            showViewAll
            showPendingTriage={isAdmin}
          />
        </ScrollView>
      </KeyboardAvoidingView>

      {/* O2-A: searchable full job list behind the "More…" chip. */}
      <JobPickerSheet
        visible={jobPickerOpen}
        jobs={activeJobs}
        labelFor={jobLabelFor}
        onSelect={(jobId) => {
          setJobSel(jobId);
          setJobPickerOpen(false);
        }}
        onClose={() => setJobPickerOpen(false)}
      />
    </SafeAreaView>
  );
}

/** B3: thin adapter over the kit Chip — job chips + payment chips get
 *  the design system's tonal selected state from one place. */
function PaymentOption({
  label,
  active,
  disabled,
  onPress,
  testID,
}: {
  label: string;
  active: boolean;
  disabled: boolean;
  onPress: () => void;
  testID: string;
}) {
  return (
    <Chip
      label={label}
      selected={active}
      disabled={disabled}
      onPress={onPress}
      testID={testID}
      accessibilityRole="radio"
    />
  );
}

/**
 * Result card for a multi-item capture submission.
 *
 * Renders aggregated counts (saved / total / total $) + a per-row
 * list with success / review-pending / failure status. Each row
 * shows the literal input text the parser received (preamble +
 * item line concatenation) so the user can spot what went wrong on
 * a failed item without having to remember what they typed.
 *
 * Reset returns the user to the empty capture form.
 */
function MultiCaptureResultCard({
  result,
  onReset,
  jobNameFor,
}: {
  result: MultiCaptureResult;
  onReset: () => void;
  /** O2-A: resolve a job_id to its display label (zh alias else name)
   * so each saved row echoes WHICH job it landed on. */
  jobNameFor: (jobId: string) => string;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const total = result.items.length;
  const saved = result.items.filter((i) => i.success).length;
  const totalSpend = result.items.reduce((acc, i) => {
    if (i.success && i.expense) return acc + Number(i.expense.amount_inc_gst);
    return acc;
  }, 0);
  const anyFailed = saved < total;
  return (
    <View style={s.multiCard} testID="multi-capture-result-card">
      <View
        style={[
          s.multiBanner,
          anyFailed ? s.multiBannerMixed : s.multiBannerOk,
        ]}
      >
        <Text
          style={[
            s.multiBannerText,
            anyFailed ? s.multiBannerTextMixed : s.multiBannerTextOk,
          ]}
        >
          {t('capture.multi_result_summary', { saved, total })}
        </Text>
        <Text style={s.multiBannerSubtle}>
          {t('capture.multi_result_total', {
            amount: `$${totalSpend.toFixed(2)}`,
          })}
        </Text>
      </View>
      {result.preamble ? (
        <Text style={s.multiPreamble}>
          {t('capture.multi_preamble_label', { preamble: result.preamble })}
        </Text>
      ) : null}
      <View style={s.multiItems}>
        {result.items.map((item, idx) => (
          <View
            key={idx}
            style={s.multiItemRow}
            testID={`multi-item-${idx}`}
          >
            <Text
              style={[
                s.multiItemMark,
                item.success ? s.multiItemMarkOk : s.multiItemMarkFail,
              ]}
            >
              {item.success ? '✓' : '✗'}
            </Text>
            <View style={s.multiItemBody}>
              <Text style={s.multiItemText} numberOfLines={2}>
                {item.text}
              </Text>
              {item.success && item.expense ? (
                <Text style={s.multiItemMeta}>
                  {/* O2-A: echo the assigned job so a mis-match is
                      visible per row, not just per capture. */}
                  {jobNameFor(item.expense.job_id)}
                  {' · '}${Number(item.expense.amount_inc_gst).toFixed(2)}
                  {item.reviewPending
                    ? ` · ${t('capture.result_pending_review')}`
                    : ''}
                </Text>
              ) : null}
              {!item.success && item.error ? (
                <Text style={s.multiItemError}>{item.error}</Text>
              ) : null}
            </View>
          </View>
        ))}
      </View>
      <TouchableOpacity
        onPress={onReset}
        style={s.resetBtn}
        testID="multi-capture-reset"
        accessibilityRole="button"
      >
        <Text style={s.resetBtnText}>{t('capture.continue_capture')}</Text>
      </TouchableOpacity>
    </View>
  );
}

const base = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  flex: { flex: 1 },
  scroll: { padding: 16, gap: 14 },
  headerRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  backBtn: { minWidth: 44, minHeight: 44, justifyContent: 'center' },
  backChevron: { fontSize: 30, lineHeight: 32, color: '#475569' },
  backSpacer: { width: 44 },
  title: {
    fontSize: 24,
    fontWeight: '600',
    color: '#0f172a',
    marginBottom: 4,
  },
  textarea: {
    minHeight: 120,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
    color: '#0f172a',
    backgroundColor: '#ffffff',
    textAlignVertical: 'top',
  },
  paymentRow: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 8,
  },
  paymentLabel: { color: '#475569', fontSize: 14, marginRight: 4 },
  // B3: chip visuals live in src/ui/kit.tsx (Chip).
  paymentHint: { color: '#64748b', fontSize: 12, lineHeight: 16 },
  // O2-A job chips row — mirrors paymentRow so the two selector rows
  // read as one visual family.
  jobRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: 8,
  },
  switchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderWidth: 1,
    borderColor: '#e2e8f0',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 10,
    backgroundColor: tokens.surface,
  },
  // Preview-parity: receipt-later is a Switch row now (switchRow).
  checkboxLabel: { color: '#0f172a', fontSize: 14 },
  errorBanner: {
    backgroundColor: '#fef2f2',
    borderWidth: 1,
    borderColor: '#fecaca',
    borderRadius: 6,
    padding: 12,
  },
  errorText: { color: '#991b1b', fontSize: 14 },
  submitBtn: {
    backgroundColor: '#1e293b',
    paddingVertical: 14,
    borderRadius: 6,
    alignItems: 'center',
  },
  submitBtnDisabled: { opacity: 0.4 },
  submitBtnText: { color: '#ffffff', fontWeight: '600', fontSize: 16 },
  // Multi-item capture result card
  multiCard: {
    backgroundColor: '#ffffff',
    borderRadius: 8,
    padding: 16,
    gap: 12,
    borderWidth: 1,
    borderColor: '#e2e8f0',
  },
  multiBanner: {
    borderRadius: 6,
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderWidth: 1,
  },
  multiBannerOk: {
    backgroundColor: '#ecfdf5',
    borderColor: '#a7f3d0',
  },
  multiBannerMixed: {
    backgroundColor: '#fffbeb',
    borderColor: '#fde68a',
  },
  multiBannerText: { fontSize: 16, fontWeight: '600' },
  multiBannerTextOk: { color: '#065f46' },
  multiBannerTextMixed: { color: '#92400e' },
  multiBannerSubtle: { color: '#475569', fontSize: 13, marginTop: 4 },
  multiPreamble: { color: '#475569', fontSize: 13 },
  multiItems: {
    borderTopWidth: 1,
    borderTopColor: '#e2e8f0',
    paddingTop: 8,
    gap: 10,
  },
  multiItemRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
  },
  multiItemMark: { fontSize: 16, fontWeight: '700', width: 16 },
  multiItemMarkOk: { color: '#15803d' },
  multiItemMarkFail: { color: '#b91c1c' },
  multiItemBody: { flex: 1 },
  multiItemText: { color: '#0f172a', fontSize: 14 },
  multiItemMeta: {
    color: '#64748b',
    fontSize: 13,
    marginTop: 2,
    fontVariant: ['tabular-nums'],
  },
  multiItemError: { color: '#b91c1c', fontSize: 13, marginTop: 2 },
  resetBtn: {
    marginTop: 4,
    backgroundColor: '#1e293b',
    paddingVertical: 12,
    borderRadius: 6,
    alignItems: 'center',
  },
  resetBtnText: { color: '#ffffff', fontWeight: '600', fontSize: 16 },
});
