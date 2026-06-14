import { useEffect, useState } from 'react';
import {
  Modal,
  View,
  Text,
  TextInput,
  Pressable,
  StyleSheet,
  ActivityIndicator,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import axios from 'axios';

import { useJobs } from '../api/hooks/useJobs';
import {
  useSuppliers,
  useCreateSupplier,
  useAddSupplierAlias,
  type LanguageCode,
} from '../api/hooks/useSuppliers';
import { useCategories } from '../api/hooks/useCategories';
import {
  useResolveQueueItem,
  type ExpenseDetailPublic,
  type ResolvePatch,
} from '../api/hooks/useExpenses';
import { OptionPickerModal, type PickerOption } from './OptionPickerModal';
import { localizeCategoryName } from '../util/category';

/**
 * A3: review-item resolve-with-corrections sheet (admin-only — only
 * reachable from the detail screen's admin-gated Approve action).
 *
 * The operator-chosen model: fixing a flagged review item is ATOMIC —
 * the admin corrects job / supplier / category here and a single
 * POST /review-queue/{id}/resolve carries the expense_patch AND resolves
 * the queue row. This replaces the old empty-patch approve, which could
 * only accept-as-is. Job reassignment is admin-only + active-job-only on
 * the backend (A1); a wrong choice surfaces as a mapped 403/422 and the
 * sheet stays open with the user's selections preserved.
 *
 * Supplier quick-create is the minimum needed to resolve an item whose
 * supplier is unknown: create the supplier + (best-effort) an alias so
 * the same shop stops re-queuing — NOT a full supplier manager.
 */

const CREATE_SUPPLIER = '__create_supplier__';

function extractDetail(error: unknown): string | null {
  if (axios.isAxiosError(error)) {
    const d = error.response?.data?.detail;
    if (typeof d === 'string') return d;
  }
  return null;
}

export function ReviewCorrectionsSheet({
  visible,
  onClose,
  reviewId,
  expense,
  onResolved,
}: {
  visible: boolean;
  onClose: () => void;
  reviewId: string;
  expense: ExpenseDetailPublic;
  onResolved: () => void;
}) {
  const { t, i18n } = useTranslation();
  const jobs = useJobs();
  const suppliers = useSuppliers();
  const categories = useCategories();
  const createSupplier = useCreateSupplier();
  const addAlias = useAddSupplierAlias();
  const resolve = useResolveQueueItem(reviewId);

  const [jobId, setJobId] = useState<string>(expense.job_id);
  const [supplierId, setSupplierId] = useState<string | null>(expense.supplier_id);
  const [categoryId, setCategoryId] = useState<string | null>(expense.category_id);
  const [picker, setPicker] = useState<'job' | 'supplier' | 'category' | null>(null);
  const [createMode, setCreateMode] = useState(false);
  const [newSupplier, setNewSupplier] = useState('');
  const [error, setError] = useState<string | null>(null);

  // Re-seed from the current expense each time the sheet opens.
  useEffect(() => {
    if (visible) {
      setJobId(expense.job_id);
      setSupplierId(expense.supplier_id);
      setCategoryId(expense.category_id);
      setPicker(null);
      setCreateMode(false);
      setNewSupplier('');
      setError(null);
    }
  }, [visible, expense.job_id, expense.supplier_id, expense.category_id]);

  const lang: LanguageCode = i18n.language?.startsWith('zh') ? 'zh' : 'en';
  const busy = resolve.isPending || createSupplier.isPending || addAlias.isPending;

  const activeJobs = (jobs.data ?? []).filter((j) => j.status === 'active');
  const jobLabel =
    activeJobs.find((j) => j.job_id === jobId)?.job_name ??
    jobs.data?.find((j) => j.job_id === jobId)?.job_name ??
    jobId.slice(0, 8);
  const supplierLabel =
    suppliers.data?.find((s) => s.supplier_id === supplierId)?.supplier_name ??
    t('expense.correct_none');
  const categoryLabel = categoryId
    ? localizeCategoryName(
        categories.data?.find((c) => c.category_id === categoryId)?.category_name,
        t,
      )
    : t('expense.correct_none');

  const jobOptions: PickerOption[] = activeJobs.map((j) => ({
    value: j.job_id,
    label: j.job_name,
  }));
  const supplierOptions: PickerOption[] = [
    { value: CREATE_SUPPLIER, label: t('expense.create_supplier') },
    ...(suppliers.data ?? []).map((sp) => ({
      value: sp.supplier_id,
      label: sp.supplier_name,
    })),
  ];
  const categoryOptions: PickerOption[] = (categories.data ?? []).map((c) => ({
    value: c.category_id,
    label: localizeCategoryName(c.category_name, t),
  }));

  const onPickSupplier = (value: string | null) => {
    if (value === CREATE_SUPPLIER) {
      setCreateMode(true);
      return;
    }
    setSupplierId(value);
  };

  const onCreateSupplier = async () => {
    const name = newSupplier.trim();
    if (!name) return;
    setError(null);
    try {
      const created = await createSupplier.mutateAsync({ supplier_name: name });
      // Best-effort alias so the same shop stops re-queuing; an alias
      // failure (e.g. a duplicate normalised form) must not block the
      // correction — the supplier is already created and selected.
      try {
        await addAlias.mutateAsync({
          supplierId: created.supplier_id,
          alias_text: name,
          language_code: lang,
        });
      } catch {
        /* non-fatal */
      }
      setSupplierId(created.supplier_id);
      setCreateMode(false);
      setNewSupplier('');
    } catch (err) {
      setError(extractDetail(err) ?? t('expense.correct_error_generic'));
    }
  };

  const mapError = (err: unknown): string => {
    const d = extractDetail(err);
    if (d) {
      if (d.includes('Only admins')) return t('expense.correct_error_contributor');
      if (d.includes('archived or completed')) return t('expense.correct_error_archived');
      if (d.includes('cannot be cleared')) return t('expense.correct_error_no_job');
      return d;
    }
    return t('expense.correct_error_generic');
  };

  const onResolve = async () => {
    setError(null);
    const patch: ResolvePatch = {};
    if (jobId && jobId !== expense.job_id) patch.job_id = jobId;
    if (supplierId && supplierId !== expense.supplier_id) patch.supplier_id = supplierId;
    if (categoryId && categoryId !== expense.category_id) patch.category_id = categoryId;
    try {
      await resolve.mutateAsync(patch);
      onResolved();
    } catch (err) {
      setError(mapError(err));
    }
  };

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={s.backdrop}>
        <View style={s.sheet} testID="review-corrections-sheet">
          <Text style={s.title}>{t('expense.correct_title')}</Text>
          <Text style={s.hint}>{t('expense.correct_hint')}</Text>

          <CorrectionRow
            label={t('expense.job')}
            value={jobLabel}
            onPress={() => setPicker('job')}
            disabled={busy}
            testID="correct-job"
          />
          <CorrectionRow
            label={t('expense.supplier')}
            value={supplierLabel}
            onPress={() => setPicker('supplier')}
            disabled={busy}
            testID="correct-supplier"
          />
          <CorrectionRow
            label={t('expense.category')}
            value={categoryLabel}
            onPress={() => setPicker('category')}
            disabled={busy}
            testID="correct-category"
          />

          {createMode ? (
            <View style={s.createBox}>
              <TextInput
                value={newSupplier}
                onChangeText={setNewSupplier}
                editable={!busy}
                placeholder={t('expense.new_supplier_placeholder')}
                placeholderTextColor="#94a3b8"
                style={s.createInput}
                autoCapitalize="words"
                testID="correct-new-supplier"
              />
              <Pressable
                onPress={() => void onCreateSupplier()}
                disabled={busy || newSupplier.trim().length === 0}
                style={({ pressed }) => [
                  s.createBtn,
                  (busy || newSupplier.trim().length === 0) && s.disabled,
                  pressed && s.pressed,
                ]}
                testID="correct-create-supplier"
              >
                <Text style={s.createBtnText}>{t('expense.create_supplier_cta')}</Text>
              </Pressable>
            </View>
          ) : null}

          {error ? (
            <Text style={s.error} testID="correct-error">
              {error}
            </Text>
          ) : null}

          <Pressable
            onPress={() => void onResolve()}
            disabled={busy}
            style={({ pressed }) => [s.resolveBtn, busy && s.disabled, pressed && s.pressed]}
            testID="correct-resolve"
          >
            {resolve.isPending ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={s.resolveBtnText}>{t('expense.approve_cta')}</Text>
            )}
          </Pressable>
          <Pressable
            onPress={onClose}
            disabled={busy}
            style={({ pressed }) => [s.cancel, pressed && s.pressed]}
            testID="correct-cancel"
          >
            <Text style={s.cancelText}>{t('common.cancel')}</Text>
          </Pressable>
        </View>
      </View>

      <OptionPickerModal
        visible={picker === 'job'}
        title={t('expense.job')}
        options={jobOptions}
        selected={jobId}
        onSelect={(v) => v && setJobId(v)}
        onClose={() => setPicker(null)}
        cancelLabel={t('common.cancel')}
      />
      <OptionPickerModal
        visible={picker === 'supplier'}
        title={t('expense.supplier')}
        options={supplierOptions}
        selected={supplierId}
        onSelect={onPickSupplier}
        onClose={() => setPicker(null)}
        cancelLabel={t('common.cancel')}
      />
      <OptionPickerModal
        visible={picker === 'category'}
        title={t('expense.category')}
        options={categoryOptions}
        selected={categoryId}
        onSelect={(v) => setCategoryId(v)}
        onClose={() => setPicker(null)}
        cancelLabel={t('common.cancel')}
      />
    </Modal>
  );
}

function CorrectionRow({
  label,
  value,
  onPress,
  disabled,
  testID,
}: {
  label: string;
  value: string;
  onPress: () => void;
  disabled: boolean;
  testID: string;
}) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [s.row, pressed && s.pressed]}
      accessibilityRole="button"
      testID={testID}
    >
      <Text style={s.rowLabel}>{label}</Text>
      <Text style={s.rowValue} numberOfLines={1}>
        {value}
      </Text>
      <Text style={s.chevron}>{'›'}</Text>
    </Pressable>
  );
}

const s = StyleSheet.create({
  backdrop: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(15, 23, 42, 0.4)',
  },
  sheet: {
    backgroundColor: '#ffffff',
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingTop: 16,
    paddingBottom: 24,
    paddingHorizontal: 16,
  },
  title: { fontSize: 18, fontWeight: '600', color: '#0f172a' },
  hint: { fontSize: 13, color: '#64748b', marginTop: 4, marginBottom: 8 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: '#f1f5f9',
    gap: 8,
  },
  rowLabel: { fontSize: 14, color: '#475569', width: 88 },
  rowValue: { fontSize: 15, color: '#0f172a', flex: 1, textAlign: 'right' },
  chevron: { fontSize: 18, color: '#94a3b8' },
  createBox: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 12 },
  createInput: {
    flex: 1,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    fontSize: 15,
    color: '#0f172a',
    backgroundColor: '#ffffff',
  },
  createBtn: {
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 8,
    backgroundColor: '#1e293b',
  },
  createBtnText: { color: '#ffffff', fontWeight: '600', fontSize: 14 },
  error: { color: '#b45309', fontSize: 13, marginTop: 12 },
  resolveBtn: {
    marginTop: 16,
    backgroundColor: '#15803d',
    paddingVertical: 14,
    borderRadius: 8,
    alignItems: 'center',
  },
  resolveBtnText: { color: '#ffffff', fontWeight: '700', fontSize: 16 },
  cancel: { paddingVertical: 14, alignItems: 'center', marginTop: 4 },
  cancelText: { fontSize: 15, color: '#64748b', fontWeight: '600' },
  disabled: { opacity: 0.5 },
  pressed: { opacity: 0.7 },
});
