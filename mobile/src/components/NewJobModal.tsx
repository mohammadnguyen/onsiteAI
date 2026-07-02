import { useState } from 'react';
import {
  Modal,
  View,
  Text,
  TextInput,
  TouchableOpacity,
  ActivityIndicator,
  StyleSheet,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import axios from 'axios';
import { resolveApiErrorMessage } from '../api/errors';
import {
  useCreateJob,
  useCreateJobAlias,
  type JobCreateInput,
  type GstMode,
} from '../api/hooks/useJobs';
import {
  formatMoney,
  contractExGstFromEntered,
  contractGstFromEntered,
} from '../util/format';

/**
 * Mobile Job Management Lite — admin-only modal that creates a job
 * (and optionally a small batch of aliases) from the phone.
 *
 * Submission is sequential, NOT atomic:
 *   1. POST /jobs → must succeed before aliases are attempted.
 *   2. For each non-empty alias line: POST /jobs/{id}/aliases.
 *      Failures are aggregated and surfaced as a partial-success
 *      banner. The job is NOT rolled back when an alias fails — the
 *      backend has no atomic "create job + aliases" endpoint and a
 *      duplicate alias is a normal user-correctable case.
 *
 * Body construction follows the conditional-spread pattern from
 * capture v0 (`mobile/app/(tabs)/expenses.tsx`) to avoid the Pydantic
 * `model_fields_set` 422 trap on optional nullable fields.
 */
export function NewJobModal({
  visible,
  onClose,
}: {
  visible: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const createJob = useCreateJob();
  const createAlias = useCreateJobAlias();
  const insets = useSafeAreaInsets();

  const [jobName, setJobName] = useState('');
  const [jobCode, setJobCode] = useState('');
  const [siteAddress, setSiteAddress] = useState('');
  const [aliasesText, setAliasesText] = useState('');
  const [marginText, setMarginText] = useState('');
  const [contractText, setContractText] = useState('');
  const [gstMode, setGstMode] = useState<GstMode>('exclusive');
  const [formError, setFormError] = useState<string | null>(null);
  const [partialMessage, setPartialMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // F1: target margin % is optional at create; when present it must be
  // 0 <= n < 100 (matches the backend CHECK). Blank = omit.
  const marginValid = (() => {
    const tr = marginText.trim();
    if (tr.length === 0) return true;
    const n = Number(tr);
    return Number.isFinite(n) && n >= 0 && n < 100;
  })();

  // F2: contract value is optional at create. The entered amount is the
  // GROSS for "Including GST" and the revenue directly for "No GST (Cash)";
  // the live recalc + on-save conversion (entered -> ex-GST) handle it.
  const contractEntered =
    contractText.trim() === '' ? null : Number(contractText.trim());
  const contractValid =
    contractEntered === null ||
    (Number.isFinite(contractEntered) && contractEntered >= 0);
  const inclusive = gstMode === 'inclusive';
  const showRecalc = contractEntered !== null && contractValid;

  const reset = () => {
    setJobName('');
    setJobCode('');
    setSiteAddress('');
    setAliasesText('');
    setMarginText('');
    setContractText('');
    setGstMode('exclusive');
    setFormError(null);
    setPartialMessage(null);
    setSubmitting(false);
  };

  const handleClose = () => {
    if (submitting) return;
    reset();
    onClose();
  };

  const onSubmit = async () => {
    if (submitting) return;
    const trimmedName = jobName.trim();
    if (!trimmedName) return;

    setFormError(null);
    setPartialMessage(null);
    setSubmitting(true);

    // Conditional-spread body builder — only fields the user actually
    // set are sent. Sending explicit `null` would mark them as
    // caller-set in `model_fields_set` and trip a 422.
    //
    // `status` is included as 'active' explicitly: the generated
    // openapi schema marks it required (Pydantic's default doesn't
    // collapse to optional in the typescript surface), and 'active' is
    // the only sane value mobile creation can produce in Lite scope.
    const body: JobCreateInput = {
      job_name: trimmedName,
      status: 'active',
      gst_mode: gstMode,
    };
    if (jobCode.trim()) body.job_code = jobCode.trim();
    if (siteAddress.trim()) body.site_address = siteAddress.trim();
    if (marginText.trim()) {
      body.target_profit_ratio_pct = Number(marginText.trim());
    }
    // F2: store the canonical ex-GST value (inclusive -> entered / 1.1).
    // gst_mode is set in the body literal above (required on JobCreate).
    if (contractEntered !== null) {
      body.contract_value_ex_gst = contractExGstFromEntered(
        contractEntered,
        inclusive,
      );
    }

    let newJobId: string;
    try {
      const created = await createJob.mutateAsync(body);
      newJobId = created.job_id;
    } catch (err) {
      setFormError(extractCreateError(err, t));
      setSubmitting(false);
      return;
    }

    // Aliases — best-effort, sequential. The backend's global
    // uniqueness on alias_text_normalized makes duplicate aliases the
    // most likely partial-failure case (HTTP 409).
    const aliasLines = aliasesText
      .split('\n')
      .map((s) => s.trim())
      .filter((s) => s.length > 0);

    if (aliasLines.length === 0) {
      setSubmitting(false);
      reset();
      onClose();
      return;
    }

    const failed: string[] = [];
    for (const alias_text of aliasLines) {
      try {
        await createAlias.mutateAsync({ jobId: newJobId, alias_text });
      } catch {
        failed.push(alias_text);
      }
    }

    setSubmitting(false);
    if (failed.length === 0) {
      reset();
      onClose();
    } else {
      // Job is already saved; surface which aliases failed and let
      // the user dismiss when ready.
      setPartialMessage(
        t('jobs.create_alias_partial', { names: failed.join(', ') }),
      );
    }
  };

  const submitDisabled =
    submitting || jobName.trim().length === 0 || !marginValid || !contractValid;

  return (
    <Modal
      visible={visible}
      animationType="slide"
      onRequestClose={handleClose}
      transparent={false}
    >
      <SafeAreaView style={s.safe} edges={['left', 'right', 'bottom']}>
        <View style={[s.header, { paddingTop: insets.top + 8 }]}>
          <Text style={s.title}>{t('jobs.new_modal_title')}</Text>
          <TouchableOpacity
            onPress={handleClose}
            style={s.closeBtn}
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            accessibilityRole="button"
            accessibilityLabel={t('common.close')}
            testID="newjob-close"
            disabled={submitting}
          >
            <Text style={s.closeBtnText}>{'×'}</Text>
          </TouchableOpacity>
        </View>
        <KeyboardAvoidingView
          style={s.flex}
          behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        >
          <ScrollView
            contentContainerStyle={s.scroll}
            keyboardShouldPersistTaps="handled"
          >
            <Field label={t('jobs.field_name')} required>
              <TextInput
                value={jobName}
                onChangeText={setJobName}
                style={s.input}
                autoFocus
                editable={!submitting}
                testID="newjob-name"
                returnKeyType="next"
              />
            </Field>

            <Field label={t('jobs.field_code')}>
              <TextInput
                value={jobCode}
                onChangeText={setJobCode}
                style={s.input}
                editable={!submitting}
                autoCapitalize="characters"
                testID="newjob-code"
                returnKeyType="next"
              />
            </Field>

            <Field label={t('jobs.field_address')}>
              <TextInput
                value={siteAddress}
                onChangeText={setSiteAddress}
                style={s.input}
                editable={!submitting}
                testID="newjob-address"
                returnKeyType="next"
              />
            </Field>

            <Field label={t('job.contract_value')}>
              <TextInput
                value={contractText}
                onChangeText={setContractText}
                style={[s.input, !contractValid ? s.inputError : null]}
                keyboardType="decimal-pad"
                editable={!submitting}
                testID="newjob-contract"
                returnKeyType="next"
              />
              {!contractValid ? (
                <Text style={s.fieldErrorText}>
                  {t('jobs_edit.amount_invalid')}
                </Text>
              ) : null}
            </Field>

            <Field label={t('job.gst_mode_label')}>
              <View style={s.gstRow}>
                <TouchableOpacity
                  onPress={() => setGstMode('inclusive')}
                  disabled={submitting}
                  style={[s.gstChip, inclusive && s.gstChipActive]}
                  accessibilityRole="button"
                  testID="newjob-gst-inclusive"
                >
                  <Text
                    style={[s.gstChipText, inclusive && s.gstChipTextActive]}
                  >
                    {t('job.gst_including')}
                  </Text>
                </TouchableOpacity>
                <TouchableOpacity
                  onPress={() => setGstMode('exclusive')}
                  disabled={submitting}
                  style={[s.gstChip, !inclusive && s.gstChipActive]}
                  accessibilityRole="button"
                  testID="newjob-gst-none"
                >
                  <Text
                    style={[s.gstChipText, !inclusive && s.gstChipTextActive]}
                  >
                    {t('job.gst_none_cash')}
                  </Text>
                </TouchableOpacity>
              </View>
              {/* O2-B polish #5: standing reinforcement of the chosen
                  mode near the contract field — the chip highlight
                  alone was missed once focus moved on. */}
              <Text style={s.gstStandingText} testID="newjob-gst-standing">
                {t('job.gst_standing', {
                  mode: t(inclusive ? 'job.gst_including' : 'job.gst_none_cash'),
                })}
              </Text>
              {showRecalc ? (
                <View style={s.gstRecalc} testID="newjob-gst-recalc">
                  <Text style={s.gstRecalcText}>
                    {t('job.ex_gst_revenue')}:{' '}
                    {formatMoney(
                      contractExGstFromEntered(contractEntered, inclusive),
                    )}
                  </Text>
                  <Text style={s.gstRecalcText}>
                    {t('job.gst_amount')}:{' '}
                    {formatMoney(
                      contractGstFromEntered(contractEntered, inclusive),
                    )}
                  </Text>
                </View>
              ) : null}
            </Field>

            <Field label={t('job.target_margin_pct')}>
              <TextInput
                value={marginText}
                onChangeText={setMarginText}
                style={[s.input, !marginValid ? s.inputError : null]}
                keyboardType="decimal-pad"
                editable={!submitting}
                placeholder={t('job.margin_percent_hint')}
                placeholderTextColor="#94a3b8"
                testID="newjob-margin"
                returnKeyType="next"
              />
              {!marginValid ? (
                <Text style={s.fieldErrorText}>
                  {t('jobs_edit.amount_invalid')}
                </Text>
              ) : null}
            </Field>

            <Field label={t('jobs.field_aliases')}>
              <TextInput
                value={aliasesText}
                onChangeText={setAliasesText}
                multiline
                style={s.textarea}
                editable={!submitting}
                testID="newjob-aliases"
              />
              <Text style={s.hint}>{t('jobs.field_aliases_hint')}</Text>
            </Field>

            {formError ? (
              <View style={s.errorBanner} testID="newjob-error">
                <Text style={s.errorText}>{formError}</Text>
              </View>
            ) : null}

            {partialMessage ? (
              <View style={s.warnBanner} testID="newjob-partial">
                <Text style={s.warnText}>{partialMessage}</Text>
              </View>
            ) : null}

            <View style={s.buttonRow}>
              <TouchableOpacity
                onPress={handleClose}
                style={s.btnSecondary}
                disabled={submitting}
                testID="newjob-cancel"
                accessibilityRole="button"
              >
                <Text style={s.btnSecondaryText}>{t('common.cancel')}</Text>
              </TouchableOpacity>
              <TouchableOpacity
                onPress={onSubmit}
                disabled={submitDisabled}
                style={[s.btnPrimary, submitDisabled && s.btnDisabled]}
                testID="newjob-submit"
                accessibilityRole="button"
              >
                {submitting ? (
                  <ActivityIndicator color="#ffffff" />
                ) : (
                  <Text style={s.btnPrimaryText}>{t('common.save')}</Text>
                )}
              </TouchableOpacity>
            </View>
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </Modal>
  );
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <View style={s.field}>
      <Text style={s.label}>
        {label}
        {required ? <Text style={s.required}>{' *'}</Text> : null}
      </Text>
      {children}
    </View>
  );
}

/** Translate a POST /jobs error into a user-readable string.
 *
 * The backend hardening returns 409 + "Job code already exists" for
 * the duplicate-code case. We map that to a localised key so EN/ZH
 * users see a translated message. Other shapes (validation array,
 * generic axios error) fall through with their best-available text.
 */
function extractCreateError(
  error: unknown,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    const detail = error.response?.data?.detail;
    if (
      status === 409 &&
      typeof detail === 'string' &&
      detail.toLowerCase().includes('job code')
    ) {
      return t('jobs.create_error_duplicate_code');
    }
  }
  // O2-B polish #8: everything else routes through the shared
  // classifier — timeout/offline render localized copy, HTTP errors
  // surface the server detail, and the raw axios `error.message`
  // ("Network Error", "timeout of 15000ms exceeded") can never leak
  // into the UI again.
  return resolveApiErrorMessage(error, t, t('jobs.create_error_generic'));
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  flex: { flex: 1 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingBottom: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  title: { fontSize: 18, fontWeight: '600', color: '#0f172a' },
  closeBtn: {
    minWidth: 44,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 12,
  },
  closeBtnText: { fontSize: 28, lineHeight: 30, color: '#0f172a', fontWeight: '300' },
  scroll: { padding: 16, gap: 12 },
  field: { gap: 6 },
  label: { fontSize: 14, fontWeight: '500', color: '#0f172a' },
  required: { color: '#dc2626' },
  input: {
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
    color: '#0f172a',
    backgroundColor: '#ffffff',
  },
  textarea: {
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
    color: '#0f172a',
    backgroundColor: '#ffffff',
    minHeight: 88,
    textAlignVertical: 'top',
  },
  hint: { fontSize: 12, color: '#64748b' },
  inputError: { borderColor: '#dc2626' },
  fieldErrorText: { color: '#dc2626', fontSize: 12 },
  gstRow: { flexDirection: 'row', gap: 8 },
  gstChip: {
    flex: 1,
    paddingVertical: 10,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 8,
    backgroundColor: '#ffffff',
  },
  gstChipActive: { backgroundColor: '#1e293b', borderColor: '#1e293b' },
  gstChipText: { color: '#334155', fontSize: 14, fontWeight: '600' },
  gstChipTextActive: { color: '#ffffff' },
  gstRecalc: { marginTop: 6, gap: 2 },
  gstStandingText: { fontSize: 12, color: '#64748b', marginTop: 6 },
  gstRecalcText: {
    fontSize: 13,
    color: '#475569',
    fontVariant: ['tabular-nums'],
  },
  errorBanner: {
    backgroundColor: '#fef2f2',
    borderWidth: 1,
    borderColor: '#fecaca',
    borderRadius: 6,
    padding: 12,
  },
  errorText: { color: '#991b1b', fontSize: 14 },
  warnBanner: {
    backgroundColor: '#fffbeb',
    borderWidth: 1,
    borderColor: '#fde68a',
    borderRadius: 6,
    padding: 12,
  },
  warnText: { color: '#92400e', fontSize: 14 },
  buttonRow: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: 8,
    marginTop: 8,
  },
  btnSecondary: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 6,
    backgroundColor: '#f1f5f9',
  },
  btnSecondaryText: { color: '#0f172a', fontWeight: '500', fontSize: 15 },
  btnPrimary: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 6,
    backgroundColor: '#1e293b',
    minWidth: 96,
    alignItems: 'center',
  },
  btnPrimaryText: { color: '#ffffff', fontWeight: '600', fontSize: 15 },
  btnDisabled: { opacity: 0.4 },
});
