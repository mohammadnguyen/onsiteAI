import { useEffect, useMemo, useState } from 'react';
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
  Pressable,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import axios from 'axios';

import {
  useJob,
  useUpdateJob,
  useCreateJobAlias,
  useCreateJobCategoryBudget,
  useUpdateJobCategoryBudget,
  useDeleteJobCategoryBudget,
  type JobUpdateInput,
  type JobStatus,
  type JobCategoryBudgetPublic,
} from '../../../src/api/hooks/useJobs';
import {
  useCategories,
  type CategoryPublic,
} from '../../../src/api/hooks/useCategories';

/**
 * Tier 1B: Mobile Job Edit screen.
 *
 * Route: ``/jobs/[id]/edit``. Reachable from the Edit button in the
 * JobDetailModal header. On return via router.back(), the modal
 * re-opens at the same job (selectedJobId persisted in the store
 * from when the modal was first opened).
 *
 * Editable fields (operator-approved Tier 1B scope):
 *   - name, code, status (active / completed), address
 *   - contract_value_ex_gst, total_budget_ex_gst (numeric)
 *   - existing aliases shown read-only (backend has no
 *     delete-alias endpoint)
 *   - add new alias inline (separate POST per alias, immediate
 *     feedback)
 *
 * Numeric handling (operator guardrail): blank numeric input is
 * treated as null (explicit clear), NEVER as 0. 0 is a real value
 * meaning "the contract is worth $0", which is a different intent
 * from "no contract value set" (null). Untouched fields are
 * omitted from the PATCH body entirely (conditional-spread).
 *
 * Deliberately NOT in v1:
 *   - category budgets editing (complex multi-row; admin web canonical)
 *   - profit ratio / warning percent fields (management surface)
 *   - delete-alias (backend doesn't support)
 *   - delete-job (backend allows only on EMPTY jobs; admin web canonical)
 */

const MAX_AMOUNT = 10_000_000;
type StatusSel = 'active' | 'completed';

function extractErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      return detail
        .map((d: { msg?: string; loc?: (string | number)[] }) => {
          const loc = Array.isArray(d.loc) ? d.loc.join('.') : '';
          return loc ? `${loc}: ${d.msg ?? ''}` : (d.msg ?? '');
        })
        .join('; ');
    }
    if (error.message) return error.message;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

function isMissing(error: unknown): boolean {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    return status === 404 || status === 403;
  }
  return false;
}

/** Parse a numeric text-input field to either a `number` value, a
 * `null` (intentional clear), or `undefined` (don't include in body).
 *
 * Compares `current` (user input string) to `initial` (the seeded
 * string from server data) to decide:
 *   - same string → undefined (no diff; omit from PATCH)
 *   - cleared (blank) → null (explicit clear per operator guardrail)
 *   - parseable number in range → number
 *   - unparseable or out-of-range → undefined + caller's validation
 *     surfaces an inline error
 */
function diffNumeric(
  current: string,
  initial: string,
): number | null | undefined {
  if (current === initial) return undefined;
  const trimmed = current.trim();
  if (trimmed.length === 0) return null;
  const n = Number(trimmed);
  if (!Number.isFinite(n)) return undefined;
  if (n < 0 || n > MAX_AMOUNT) return undefined;
  return n;
}

function diffText(current: string, initial: string): string | null | undefined {
  if (current === initial) return undefined;
  const trimmed = current.trim();
  return trimmed.length === 0 ? null : trimmed;
}

function moneyToText(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === '') return '';
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n)) return '';
  return n.toFixed(2);
}

function isMoneyValid(current: string): boolean {
  const trimmed = current.trim();
  if (trimmed.length === 0) return true; // blank is a valid "clear"
  const n = Number(trimmed);
  if (!Number.isFinite(n)) return false;
  if (n < 0 || n > MAX_AMOUNT) return false;
  return true;
}

/**
 * F1 margin %: like diffNumeric but bounded 0 <= n < 100 to match the DB
 * CHECK ck_jobs_target_profit_ratio_pct_range (and Pydantic ge=0,lt=100).
 * Exactly 100 is rejected. Blank = explicit clear (null).
 */
function diffPercent(
  current: string,
  initial: string,
): number | null | undefined {
  if (current === initial) return undefined;
  const trimmed = current.trim();
  if (trimmed.length === 0) return null;
  const n = Number(trimmed);
  if (!Number.isFinite(n)) return undefined;
  if (n < 0 || n >= 100) return undefined;
  return n;
}

function isPercentValid(current: string): boolean {
  const trimmed = current.trim();
  if (trimmed.length === 0) return true;
  const n = Number(trimmed);
  if (!Number.isFinite(n)) return false;
  if (n < 0 || n >= 100) return false;
  return true;
}

export default function JobEditScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { t } = useTranslation();
  const job = useJob(id ?? null);
  const update = useUpdateJob(id ?? '');
  const addAlias = useCreateJobAlias();
  // Slice B (Tier 1C) — category budgets editing.
  const categories = useCategories();
  const createBudget = useCreateJobCategoryBudget(id ?? '');
  const updateBudget = useUpdateJobCategoryBudget(id ?? '');
  const deleteBudget = useDeleteJobCategoryBudget(id ?? '');

  // Form state. Seeded once from job.data via the initialized flag so
  // a background refetch doesn't stomp user edits.
  const [name, setName] = useState<string>('');
  const [code, setCode] = useState<string>('');
  const [statusSel, setStatusSel] = useState<StatusSel>('active');
  const [address, setAddress] = useState<string>('');
  const [contractText, setContractText] = useState<string>('');
  const [budgetText, setBudgetText] = useState<string>('');
  const [marginText, setMarginText] = useState<string>('');
  const [newAliasText, setNewAliasText] = useState<string>('');
  const [formError, setFormError] = useState<string | null>(null);
  const [initialized, setInitialized] = useState<boolean>(false);

  // Capture the seeded strings so the diff comparison stays stable
  // even if the source changes underneath us (e.g. another tab edits).
  const [initialSeed, setInitialSeed] = useState<{
    name: string;
    code: string;
    status: StatusSel;
    address: string;
    contractText: string;
    budgetText: string;
    marginText: string;
  } | null>(null);

  // Slice B (Tier 1C) — category budgets editing state.
  // budgetEdits[budgetId] = text the user has typed; absent ⇒ row is
  // unchanged from the server value. Survives data refetches so the
  // user's in-progress typing isn't lost when ['jobs'] invalidates.
  const [budgetEdits, setBudgetEdits] = useState<Record<string, string>>({});
  // Per-row "in flight" flags so we can disable buttons on the
  // specific row whose mutation is running (the React Query hook's
  // ``isPending`` is shared across all rows using the same hook).
  const [savingBudgetId, setSavingBudgetId] = useState<string | null>(null);
  const [deletingBudgetId, setDeletingBudgetId] = useState<string | null>(
    null,
  );
  // Add-row state: which category the user selected from the chips +
  // their typed amount text. Reset after a successful POST.
  const [newBudgetCategoryId, setNewBudgetCategoryId] = useState<
    string | null
  >(null);
  const [newBudgetAmount, setNewBudgetAmount] = useState<string>('');

  useEffect(() => {
    if (!job.data || initialized) return;
    const j = job.data;
    const seed = {
      name: j.job_name ?? '',
      code: j.job_code ?? '',
      status: (j.status ?? 'active') as StatusSel,
      address: j.site_address ?? '',
      contractText: moneyToText(j.contract_value_ex_gst),
      budgetText: moneyToText(j.total_budget_ex_gst),
      marginText: moneyToText(j.target_profit_ratio_pct),
    };
    setName(seed.name);
    setCode(seed.code);
    setStatusSel(seed.status);
    setAddress(seed.address);
    setContractText(seed.contractText);
    setBudgetText(seed.budgetText);
    setMarginText(seed.marginText);
    setInitialSeed(seed);
    setInitialized(true);
  }, [job.data, initialized]);

  const contractValid = useMemo(() => isMoneyValid(contractText), [contractText]);
  const budgetValid = useMemo(() => isMoneyValid(budgetText), [budgetText]);
  const marginValid = useMemo(() => isPercentValid(marginText), [marginText]);

  // Conditional-spread PATCH body. Each helper returns undefined when
  // the field is unchanged, null when the user explicitly cleared it,
  // or the parsed value otherwise. Empty `out` => nothing to save.
  const diff = useMemo<JobUpdateInput | null>(() => {
    if (!initialSeed) return null;
    const out: JobUpdateInput = {};
    const nameDiff = diffText(name, initialSeed.name);
    if (nameDiff !== undefined) out.job_name = nameDiff;
    const codeDiff = diffText(code, initialSeed.code);
    if (codeDiff !== undefined) out.job_code = codeDiff;
    if (statusSel !== initialSeed.status) out.status = statusSel as JobStatus;
    const addressDiff = diffText(address, initialSeed.address);
    if (addressDiff !== undefined) out.site_address = addressDiff;
    if (contractValid) {
      const contractDiff = diffNumeric(contractText, initialSeed.contractText);
      if (contractDiff !== undefined) out.contract_value_ex_gst = contractDiff;
    }
    if (budgetValid) {
      const budgetDiff = diffNumeric(budgetText, initialSeed.budgetText);
      if (budgetDiff !== undefined) out.total_budget_ex_gst = budgetDiff;
    }
    if (marginValid) {
      const marginDiff = diffPercent(marginText, initialSeed.marginText);
      if (marginDiff !== undefined) out.target_profit_ratio_pct = marginDiff;
    }
    return Object.keys(out).length === 0 ? null : out;
  }, [
    initialSeed,
    name,
    code,
    statusSel,
    address,
    contractText,
    budgetText,
    marginText,
    contractValid,
    budgetValid,
    marginValid,
  ]);

  const onBack = () => {
    // Always land back on the Jobs tab explicitly. router.back() pops
    // the root stack and lands on whichever tab expo-router treats as
    // the default (currently /(tabs)/expenses per app/_layout.tsx's
    // post-login redirect), losing the user's job context. Replacing
    // to /(tabs)/jobs guarantees they return to the right tab; the
    // useFocusEffect in JobsScreen then re-presents the native Modal
    // at the same selectedJobId (preserved in zustand store).
    router.replace('/(tabs)/jobs');
  };

  const onSave = async () => {
    if (update.isPending) return;
    if (!contractValid || !budgetValid) return;
    if (diff === null) {
      // Nothing to save — just go back.
      onBack();
      return;
    }
    setFormError(null);
    Keyboard.dismiss();
    try {
      await update.mutateAsync(diff);
      onBack();
    } catch (err) {
      setFormError(extractErrorMessage(err, t('jobs_edit.error_network')));
    }
  };

  const onAddAlias = async () => {
    const aliasText = newAliasText.trim();
    if (aliasText.length === 0) return;
    if (!id) return;
    if (addAlias.isPending) return;
    setFormError(null);
    try {
      await addAlias.mutateAsync({ jobId: id, alias_text: aliasText });
      setNewAliasText('');
    } catch (err) {
      // Duplicate alias is a 409 from the backend — surface as an
      // alert so the user knows specifically what happened.
      const status = axios.isAxiosError(err) ? err.response?.status : undefined;
      const message =
        status === 409
          ? t('jobs_edit.alias_duplicate')
          : extractErrorMessage(err, t('jobs_edit.alias_error'));
      Alert.alert(t('common.error'), message);
    }
  };

  // Categories available for ADD: active AND not already budgeted on
  // this job. Client-side de-dup so the user can't accidentally hit
  // the backend's 409 on duplicate (job_id, category_id). Backend
  // remains the source of truth — race-window duplicates still
  // surface a 409 alert from the catch branch.
  const availableCategories = useMemo<CategoryPublic[]>(() => {
    if (!categories.data || !job.data) return [];
    const existingCategoryIds = new Set(
      job.data.category_budgets.map((b) => b.category_id),
    );
    return categories.data.filter(
      (c) => c.is_active && !existingCategoryIds.has(c.category_id),
    );
  }, [categories.data, job.data]);

  // Helper: current text for a budget row (edited or server value).
  const getBudgetText = (budget: JobCategoryBudgetPublic): string =>
    budgetEdits[budget.budget_id] ??
    moneyToText(budget.budget_amount_ex_gst);

  // Helper: is this row's edit valid + different from the server value?
  // Drives the visibility of the per-row Save button.
  const budgetRowDirty = (budget: JobCategoryBudgetPublic): boolean => {
    const edit = budgetEdits[budget.budget_id];
    if (edit === undefined) return false;
    if (!isMoneyValid(edit) || edit.trim().length === 0) return false;
    return Number(edit.trim()) !== Number(budget.budget_amount_ex_gst);
  };

  // Per-row save: validates blank/negative, no-ops if unchanged, calls
  // PATCH, clears edit on success, alerts on error.
  const onSaveBudget = async (budget: JobCategoryBudgetPublic) => {
    const text = budgetEdits[budget.budget_id];
    if (text === undefined) return;
    if (!isMoneyValid(text) || text.trim().length === 0) {
      Alert.alert(
        t('common.error'),
        t('jobs_edit.budget_amount_invalid'),
      );
      return;
    }
    const amount = Number(text.trim());
    if (amount === Number(budget.budget_amount_ex_gst)) {
      setBudgetEdits((prev) => {
        const next = { ...prev };
        delete next[budget.budget_id];
        return next;
      });
      return;
    }
    setSavingBudgetId(budget.budget_id);
    try {
      await updateBudget.mutateAsync({
        budgetId: budget.budget_id,
        budget_amount_ex_gst: amount,
      });
      setBudgetEdits((prev) => {
        const next = { ...prev };
        delete next[budget.budget_id];
        return next;
      });
    } catch (err) {
      Alert.alert(
        t('common.error'),
        extractErrorMessage(err, t('jobs_edit.budget_save_error')),
      );
    } finally {
      setSavingBudgetId(null);
    }
  };

  // Per-row delete: Alert.alert confirm (destructive style on iOS),
  // calls DELETE on confirm, alerts on error.
  const onDeleteBudget = (budget: JobCategoryBudgetPublic) => {
    Alert.alert(
      t('jobs_edit.budget_delete_confirm_title'),
      t('jobs_edit.budget_delete_confirm_message'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('jobs_edit.budget_delete_confirm_action'),
          style: 'destructive',
          onPress: async () => {
            setDeletingBudgetId(budget.budget_id);
            try {
              await deleteBudget.mutateAsync({
                budgetId: budget.budget_id,
              });
              // Drop any in-progress edit for the just-deleted row.
              setBudgetEdits((prev) => {
                const next = { ...prev };
                delete next[budget.budget_id];
                return next;
              });
            } catch (err) {
              Alert.alert(
                t('common.error'),
                extractErrorMessage(
                  err,
                  t('jobs_edit.budget_delete_error'),
                ),
              );
            } finally {
              setDeletingBudgetId(null);
            }
          },
        },
      ],
    );
  };

  // Add-row submit: requires both a selected category AND a valid
  // (non-blank, non-negative, numeric) amount. POSTs and resets state.
  const onAddBudget = async () => {
    if (!newBudgetCategoryId) return;
    if (
      !isMoneyValid(newBudgetAmount) ||
      newBudgetAmount.trim().length === 0
    ) {
      Alert.alert(
        t('common.error'),
        t('jobs_edit.budget_amount_invalid'),
      );
      return;
    }
    if (createBudget.isPending) return;
    const amount = Number(newBudgetAmount.trim());
    try {
      await createBudget.mutateAsync({
        category_id: newBudgetCategoryId,
        budget_amount_ex_gst: amount,
      });
      setNewBudgetCategoryId(null);
      setNewBudgetAmount('');
    } catch (err) {
      Alert.alert(
        t('common.error'),
        extractErrorMessage(err, t('jobs_edit.budget_add_error')),
      );
    }
  };

  const addBudgetDisabled =
    createBudget.isPending ||
    !newBudgetCategoryId ||
    newBudgetAmount.trim().length === 0 ||
    !isMoneyValid(newBudgetAmount);

  const saveDisabled =
    update.isPending || !contractValid || !budgetValid || !marginValid;

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <View style={s.header}>
        <Pressable
          onPress={onBack}
          hitSlop={12}
          testID="job-edit-back"
          accessibilityRole="button"
          accessibilityLabel={t('jobs_edit.cancel')}
          style={({ pressed }) => [s.backBtn, pressed && s.backBtnPressed]}
        >
          <Text style={s.backChevron}>{'‹'}</Text>
          <Text style={s.backLabel}>{t('jobs_edit.cancel')}</Text>
        </Pressable>
        <Text style={s.headerTitle} numberOfLines={1}>
          {t('jobs_edit.title')}
        </Text>
        <View style={s.headerSpacer} />
      </View>

      {job.isLoading ? (
        <View style={s.state} testID="job-edit-loading">
          <ActivityIndicator color="#1e293b" />
          <Text style={s.stateText}>{t('common.loading')}</Text>
        </View>
      ) : job.isError && isMissing(job.error) ? (
        <View style={s.state} testID="job-edit-notfound">
          <Text style={s.stateText}>{t('jobs_edit.not_found')}</Text>
          <Pressable
            onPress={onBack}
            style={({ pressed }) => [s.linkBtn, pressed && s.linkBtnPressed]}
            accessibilityRole="button"
          >
            <Text style={s.linkBtnText}>{t('expense.back')}</Text>
          </Pressable>
        </View>
      ) : job.isError ? (
        <View style={s.state} testID="job-edit-error">
          <Text style={[s.stateText, s.errorText]}>{t('jobs_edit.load_error')}</Text>
          <Pressable
            onPress={() => void job.refetch()}
            style={({ pressed }) => [s.linkBtn, pressed && s.linkBtnPressed]}
            accessibilityRole="button"
            testID="job-edit-retry"
          >
            <Text style={s.linkBtnText}>{t('common.retry')}</Text>
          </Pressable>
        </View>
      ) : job.data ? (
        <KeyboardAvoidingView
          style={s.flex}
          behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        >
          <ScrollView
            contentContainerStyle={s.scroll}
            keyboardShouldPersistTaps="handled"
            testID="job-edit-form"
          >
            <Text style={s.label}>{t('jobs.field_name')}</Text>
            <TextInput
              value={name}
              onChangeText={setName}
              placeholder=""
              editable={!update.isPending}
              style={s.input}
              testID="job-edit-name"
            />

            <Text style={s.label}>{t('jobs.field_code')}</Text>
            <TextInput
              value={code}
              onChangeText={setCode}
              autoCapitalize="characters"
              editable={!update.isPending}
              style={s.input}
              testID="job-edit-code"
            />

            <Text style={s.label}>{t('job.status')}</Text>
            <View style={s.statusRow}>
              <StatusOption
                label={t('job.status_active')}
                active={statusSel === 'active'}
                disabled={update.isPending}
                onPress={() => setStatusSel('active')}
                testID="job-edit-status-active"
              />
              <StatusOption
                label={t('job.status_completed')}
                active={statusSel === 'completed'}
                disabled={update.isPending}
                onPress={() => setStatusSel('completed')}
                testID="job-edit-status-completed"
              />
            </View>

            <Text style={s.label}>{t('jobs.field_address')}</Text>
            <TextInput
              value={address}
              onChangeText={setAddress}
              editable={!update.isPending}
              style={s.input}
              testID="job-edit-address"
            />

            <Text style={s.label}>{t('job.contract')}</Text>
            <TextInput
              value={contractText}
              onChangeText={setContractText}
              keyboardType="decimal-pad"
              placeholder={t('jobs_edit.numeric_blank_hint')}
              placeholderTextColor="#94a3b8"
              editable={!update.isPending}
              style={[s.input, !contractValid ? s.inputError : null]}
              testID="job-edit-contract"
            />
            {!contractValid ? (
              <Text style={s.fieldError}>
                {t('jobs_edit.amount_invalid')}
              </Text>
            ) : null}

            <Text style={s.label}>{t('job.budget')}</Text>
            <TextInput
              value={budgetText}
              onChangeText={setBudgetText}
              keyboardType="decimal-pad"
              placeholder={t('jobs_edit.numeric_blank_hint')}
              placeholderTextColor="#94a3b8"
              editable={!update.isPending}
              style={[s.input, !budgetValid ? s.inputError : null]}
              testID="job-edit-budget"
            />
            {!budgetValid ? (
              <Text style={s.fieldError}>
                {t('jobs_edit.amount_invalid')}
              </Text>
            ) : null}

            <Text style={s.label}>{t('job.target_margin_pct')}</Text>
            <TextInput
              value={marginText}
              onChangeText={setMarginText}
              keyboardType="decimal-pad"
              placeholder={t('job.margin_percent_hint')}
              placeholderTextColor="#94a3b8"
              editable={!update.isPending}
              style={[s.input, !marginValid ? s.inputError : null]}
              testID="job-edit-margin-percent"
            />
            {!marginValid ? (
              <Text style={s.fieldError}>{t('jobs_edit.amount_invalid')}</Text>
            ) : null}

            <Text style={s.sectionHeader}>{t('job.aliases')}</Text>
            <Text style={s.aliasHint}>{t('jobs_edit.aliases_read_only_hint')}</Text>
            {job.data.aliases.length === 0 ? (
              <Text style={s.muted}>{t('jobs.empty')}</Text>
            ) : (
              <View style={s.aliasList}>
                {job.data.aliases.map((a) => (
                  <View
                    key={a.alias_id}
                    style={s.aliasChip}
                    testID={`job-edit-alias-${a.alias_id}`}
                  >
                    <Text style={s.aliasChipText}>{a.alias_text}</Text>
                  </View>
                ))}
              </View>
            )}
            <View style={s.aliasAddRow}>
              <TextInput
                value={newAliasText}
                onChangeText={setNewAliasText}
                placeholder={t('jobs_edit.alias_add_placeholder')}
                placeholderTextColor="#94a3b8"
                autoCapitalize="none"
                autoCorrect={false}
                editable={!addAlias.isPending && !update.isPending}
                style={[s.input, s.aliasAddInput]}
                testID="job-edit-alias-add-input"
              />
              <TouchableOpacity
                onPress={onAddAlias}
                disabled={
                  addAlias.isPending ||
                  update.isPending ||
                  newAliasText.trim().length === 0
                }
                style={[
                  s.aliasAddBtn,
                  (addAlias.isPending ||
                    update.isPending ||
                    newAliasText.trim().length === 0) &&
                    s.aliasAddBtnDisabled,
                ]}
                testID="job-edit-alias-add-btn"
                accessibilityRole="button"
              >
                {addAlias.isPending ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={s.aliasAddBtnText}>{t('jobs_edit.alias_add')}</Text>
                )}
              </TouchableOpacity>
            </View>

            {/* Slice B (Tier 1C) — Category budgets editing. Existing
                budgets render as one row each (read-only category
                name, editable amount, per-row Save + Delete). Add row
                lets the admin attach a budget to a category that
                doesn't yet have one (filtered client-side to avoid
                the backend's 409 on duplicate). All mutations
                invalidate ['jobs'] so the modal + budget summary
                refresh. */}
            <Text style={s.sectionHeader}>{t('job.budgets')}</Text>
            {job.data.category_budgets.length === 0 ? (
              <Text style={s.muted}>{t('jobs_edit.budgets_empty')}</Text>
            ) : (
              <View style={s.budgetList}>
                {job.data.category_budgets.map((b) => (
                  <View
                    key={b.budget_id}
                    style={s.budgetRow}
                    testID={`job-edit-budget-${b.budget_id}`}
                  >
                    <Text
                      style={s.budgetCategoryName}
                      numberOfLines={1}
                    >
                      {b.category.category_name}
                    </Text>
                    <TextInput
                      value={getBudgetText(b)}
                      onChangeText={(text) =>
                        setBudgetEdits((prev) => ({
                          ...prev,
                          [b.budget_id]: text,
                        }))
                      }
                      keyboardType="decimal-pad"
                      editable={
                        savingBudgetId !== b.budget_id &&
                        deletingBudgetId !== b.budget_id
                      }
                      style={[s.input, s.budgetAmountInput]}
                      testID={`job-edit-budget-amount-${b.budget_id}`}
                    />
                    {budgetRowDirty(b) ? (
                      <TouchableOpacity
                        onPress={() => onSaveBudget(b)}
                        disabled={savingBudgetId !== null}
                        style={[
                          s.budgetSaveBtn,
                          savingBudgetId !== null &&
                            s.budgetSaveBtnDisabled,
                        ]}
                        testID={`job-edit-budget-save-${b.budget_id}`}
                        accessibilityRole="button"
                      >
                        {savingBudgetId === b.budget_id ? (
                          <ActivityIndicator color="#fff" size="small" />
                        ) : (
                          <Text style={s.budgetSaveBtnText}>
                            {t('common.save')}
                          </Text>
                        )}
                      </TouchableOpacity>
                    ) : null}
                    <TouchableOpacity
                      onPress={() => onDeleteBudget(b)}
                      disabled={deletingBudgetId !== null}
                      style={[
                        s.budgetDeleteBtn,
                        deletingBudgetId === b.budget_id &&
                          s.budgetDeleteBtnDisabled,
                      ]}
                      testID={`job-edit-budget-delete-${b.budget_id}`}
                      accessibilityRole="button"
                      accessibilityLabel={t(
                        'jobs_edit.budget_delete_confirm_action',
                      )}
                    >
                      {deletingBudgetId === b.budget_id ? (
                        <ActivityIndicator color="#dc2626" size="small" />
                      ) : (
                        <Text style={s.budgetDeleteBtnText}>{'×'}</Text>
                      )}
                    </TouchableOpacity>
                  </View>
                ))}
              </View>
            )}

            {/* Add a new category budget. Hidden / informational when
                every category already has a budget. */}
            <Text style={s.subSectionHeader}>
              {t('jobs_edit.budget_add_heading')}
            </Text>
            {categories.isLoading ? (
              <View style={s.spendingLoadingRow}>
                <ActivityIndicator size="small" color="#64748b" />
                <Text style={s.aliasHint}>{t('common.loading')}</Text>
              </View>
            ) : availableCategories.length === 0 ? (
              <Text style={s.muted}>
                {t('jobs_edit.budget_all_categories_used')}
              </Text>
            ) : (
              <>
                <Text style={s.aliasHint}>
                  {t('jobs_edit.budget_pick_category')}
                </Text>
                <ScrollView
                  horizontal
                  showsHorizontalScrollIndicator={false}
                  contentContainerStyle={s.budgetCategoryChipsRow}
                  testID="job-edit-budget-add-categories"
                  keyboardShouldPersistTaps="handled"
                >
                  {availableCategories.map((c) => (
                    <TouchableOpacity
                      key={c.category_id}
                      onPress={() => setNewBudgetCategoryId(c.category_id)}
                      style={[
                        s.budgetCategoryChip,
                        c.category_id === newBudgetCategoryId &&
                          s.budgetCategoryChipActive,
                      ]}
                      testID={`job-edit-budget-add-category-${c.category_id}`}
                      accessibilityRole="radio"
                      accessibilityState={{
                        selected: c.category_id === newBudgetCategoryId,
                      }}
                    >
                      <Text
                        style={[
                          s.budgetCategoryChipText,
                          c.category_id === newBudgetCategoryId &&
                            s.budgetCategoryChipTextActive,
                        ]}
                      >
                        {c.category_name}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </ScrollView>
                <View style={s.budgetAddInputRow}>
                  <TextInput
                    value={newBudgetAmount}
                    onChangeText={setNewBudgetAmount}
                    placeholder={t('jobs_edit.budget_amount_placeholder')}
                    placeholderTextColor="#94a3b8"
                    keyboardType="decimal-pad"
                    editable={!createBudget.isPending}
                    style={[s.input, s.budgetAddAmountInput]}
                    testID="job-edit-budget-add-amount"
                  />
                  <TouchableOpacity
                    onPress={onAddBudget}
                    disabled={addBudgetDisabled}
                    style={[
                      s.budgetAddBtn,
                      addBudgetDisabled && s.budgetAddBtnDisabled,
                    ]}
                    testID="job-edit-budget-add-btn"
                    accessibilityRole="button"
                  >
                    {createBudget.isPending ? (
                      <ActivityIndicator color="#fff" />
                    ) : (
                      <Text style={s.budgetAddBtnText}>
                        {t('jobs_edit.alias_add')}
                      </Text>
                    )}
                  </TouchableOpacity>
                </View>
              </>
            )}

            {formError ? (
              <View style={s.errorBanner} testID="job-edit-error-banner">
                <Text style={s.errorBannerText}>{formError}</Text>
              </View>
            ) : null}

            <TouchableOpacity
              onPress={onSave}
              disabled={saveDisabled}
              style={[s.saveBtn, saveDisabled && s.saveBtnDisabled]}
              testID="job-edit-save"
              accessibilityRole="button"
              accessibilityState={{ disabled: saveDisabled }}
            >
              {update.isPending ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={s.saveBtnText}>{t('jobs_edit.save')}</Text>
              )}
            </TouchableOpacity>
          </ScrollView>
        </KeyboardAvoidingView>
      ) : null}
    </SafeAreaView>
  );
}

function StatusOption({
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
    <TouchableOpacity
      onPress={onPress}
      disabled={disabled}
      style={[
        s.statusOption,
        active && s.statusOptionActive,
        disabled && s.statusOptionDisabled,
      ]}
      testID={testID}
      accessibilityRole="radio"
      accessibilityState={{ selected: active, disabled }}
    >
      <Text
        style={[s.statusOptionText, active && s.statusOptionTextActive]}
      >
        {label}
      </Text>
    </TouchableOpacity>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  flex: { flex: 1 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  backBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 4,
    minWidth: 88,
  },
  backBtnPressed: { opacity: 0.5 },
  backChevron: {
    fontSize: 28,
    color: '#1e293b',
    marginRight: 4,
    lineHeight: 28,
  },
  backLabel: { fontSize: 16, color: '#1e293b' },
  headerTitle: {
    flex: 1,
    textAlign: 'center',
    fontSize: 17,
    fontWeight: '600',
    color: '#0f172a',
  },
  headerSpacer: { width: 88 },
  state: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
    gap: 12,
  },
  stateText: { color: '#64748b', fontSize: 15 },
  errorText: { color: '#b91c1c' },
  linkBtn: { paddingHorizontal: 12, paddingVertical: 8 },
  linkBtnPressed: { opacity: 0.5 },
  linkBtnText: { color: '#1e293b', fontSize: 15, fontWeight: '600' },
  scroll: { padding: 16, gap: 12 },
  label: { color: '#475569', fontSize: 14, marginTop: 4 },
  input: {
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
    color: '#0f172a',
    backgroundColor: '#ffffff',
  },
  inputError: { borderColor: '#dc2626' },
  fieldError: { color: '#b91c1c', fontSize: 13 },
  statusRow: { flexDirection: 'row', gap: 8 },
  statusOption: {
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    backgroundColor: '#f8fafc',
  },
  statusOptionActive: {
    backgroundColor: '#1e293b',
    borderColor: '#1e293b',
  },
  statusOptionDisabled: { opacity: 0.5 },
  statusOptionText: { color: '#0f172a', fontSize: 14, fontWeight: '500' },
  statusOptionTextActive: { color: '#ffffff' },
  sectionHeader: {
    fontSize: 13,
    fontWeight: '600',
    color: '#475569',
    marginTop: 16,
    marginBottom: 4,
    textTransform: 'uppercase',
  },
  aliasHint: { color: '#64748b', fontSize: 13 },
  muted: { color: '#94a3b8' },
  aliasList: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
    marginTop: 4,
  },
  aliasChip: {
    backgroundColor: '#e2e8f0',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  aliasChipText: { color: '#0f172a', fontSize: 13 },
  aliasAddRow: {
    flexDirection: 'row',
    gap: 8,
    alignItems: 'stretch',
    marginTop: 8,
  },
  aliasAddInput: { flex: 1 },
  aliasAddBtn: {
    backgroundColor: '#1e293b',
    paddingHorizontal: 14,
    borderRadius: 6,
    alignItems: 'center',
    justifyContent: 'center',
    minWidth: 72,
  },
  aliasAddBtnDisabled: { opacity: 0.4 },
  aliasAddBtnText: { color: '#ffffff', fontWeight: '600', fontSize: 14 },
  errorBanner: {
    backgroundColor: '#fef2f2',
    borderWidth: 1,
    borderColor: '#fecaca',
    borderRadius: 6,
    padding: 12,
  },
  errorBannerText: { color: '#991b1b', fontSize: 14 },
  saveBtn: {
    backgroundColor: '#1e293b',
    paddingVertical: 14,
    borderRadius: 6,
    alignItems: 'center',
    marginTop: 12,
  },
  saveBtnDisabled: { opacity: 0.4 },
  saveBtnText: { color: '#ffffff', fontWeight: '600', fontSize: 16 },
  // Slice B (Tier 1C) — Category budgets editing styles.
  subSectionHeader: {
    fontSize: 13,
    fontWeight: '600',
    color: '#475569',
    marginTop: 12,
    marginBottom: 4,
  },
  budgetList: { marginTop: 4, gap: 6 },
  budgetRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  budgetCategoryName: {
    flex: 1,
    color: '#0f172a',
    fontSize: 14,
  },
  budgetAmountInput: {
    width: 110,
    paddingVertical: 8,
    fontVariant: ['tabular-nums'],
  },
  budgetSaveBtn: {
    backgroundColor: '#1e293b',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 6,
    alignItems: 'center',
    justifyContent: 'center',
    minWidth: 60,
  },
  budgetSaveBtnDisabled: { opacity: 0.4 },
  budgetSaveBtnText: { color: '#ffffff', fontWeight: '600', fontSize: 13 },
  budgetDeleteBtn: {
    paddingHorizontal: 8,
    paddingVertical: 6,
    alignItems: 'center',
    justifyContent: 'center',
    minWidth: 32,
    minHeight: 32,
  },
  budgetDeleteBtnDisabled: { opacity: 0.4 },
  budgetDeleteBtnText: {
    color: '#dc2626',
    fontSize: 22,
    lineHeight: 24,
    fontWeight: '300',
  },
  budgetCategoryChipsRow: {
    flexDirection: 'row',
    gap: 6,
    paddingVertical: 4,
  },
  budgetCategoryChip: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 14,
    backgroundColor: '#f8fafc',
  },
  budgetCategoryChipActive: {
    backgroundColor: '#1e293b',
    borderColor: '#1e293b',
  },
  budgetCategoryChipText: { color: '#0f172a', fontSize: 13 },
  budgetCategoryChipTextActive: { color: '#ffffff' },
  budgetAddInputRow: {
    flexDirection: 'row',
    gap: 8,
    alignItems: 'stretch',
    marginTop: 8,
  },
  budgetAddAmountInput: { flex: 1 },
  budgetAddBtn: {
    backgroundColor: '#1e293b',
    paddingHorizontal: 14,
    borderRadius: 6,
    alignItems: 'center',
    justifyContent: 'center',
    minWidth: 72,
  },
  budgetAddBtnDisabled: { opacity: 0.4 },
  budgetAddBtnText: { color: '#ffffff', fontWeight: '600', fontSize: 14 },
  spendingLoadingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 4,
  },
});
