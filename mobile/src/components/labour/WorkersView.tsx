import { useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  StyleSheet,
  FlatList,
  ActivityIndicator,
  Pressable,
  TouchableOpacity,
  RefreshControl,
  Alert,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import axios from 'axios';

import {
  useWorkers,
  useCreateWorker,
  useUpdateWorker,
  type WorkerPublic,
  type WorkerUpdateInput,
} from '../../api/hooks/useLabour';
import { useMe } from '../../api/hooks/useAuth';
import { formatMoney } from '../../util/format';
import { tokens } from '../../ui/tokens';

/**
 * L-B2: worker roster management (admin-only).
 *
 * B4-2: embedded as the Workers tab of the Labour screen (formerly
 * route ``/labour/workers``). GET /workers is any-auth on the
 * backend, so unlike /users the forbidden state cannot come from the
 * list call — the view gates on /auth/me (fails closed) and the
 * WRITE routes' require_admin remains the authority.
 *
 * One inline card serves both ADD and EDIT (name + note + save);
 * native Alert.prompt is iOS-only, so free-text editing cannot live
 * in an action menu. Deactivate/reactivate ride on the edit card with
 * confirms (users-screen precedent). No delete exists by design —
 * workers with history deactivate.
 *
 * Duplicate display names are ALLOWED server-side (labels, not
 * identity); a case-insensitive soft confirm fires before submitting
 * one (checked against the FULL roster, including deactivated).
 */

type Editing =
  | { mode: 'add' }
  | { mode: 'edit'; worker: WorkerPublic }
  | null;

/** Parse the hourly-rate input. Empty = no rate (valid, null). Otherwise
 * a finite number >= 0 (mirrors the backend hourly_rate CHECK). */
function parseRate(text: string): { value: number | null; valid: boolean } {
  const trimmed = text.trim();
  if (trimmed.length === 0) return { value: null, valid: true };
  const n = Number(trimmed);
  if (!Number.isFinite(n) || n < 0) return { value: null, valid: false };
  return { value: n, valid: true };
}

export function WorkersView() {
  const { t } = useTranslation();
  const me = useMe();
  // Always fetch the full roster; "show deactivated" filters DISPLAY
  // only, and the duplicate-name check must see inactive names too.
  const workers = useWorkers(true);
  const createWorker = useCreateWorker();
  const updateWorker = useUpdateWorker();

  const [showInactive, setShowInactive] = useState(false);
  const [editing, setEditing] = useState<Editing>(null);
  const [name, setName] = useState('');
  const [note, setNote] = useState('');
  // Admin-only hourly rate (this whole view is admin-gated; the
  // backend returns hourly_rate only to admins). Raw input text.
  const [rate, setRate] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const isAdmin = me.data?.role === 'admin';
  const busy = createWorker.isPending || updateWorker.isPending;

  const visible = useMemo(
    () => (workers.data ?? []).filter((w) => showInactive || w.is_active),
    [workers.data, showInactive],
  );

  const onRefresh = () => {
    setRefreshing(true);
    void Promise.all([workers.refetch(), me.refetch()]).finally(() =>
      setRefreshing(false),
    );
  };

  const openAdd = () => {
    setEditing({ mode: 'add' });
    setName('');
    setNote('');
    setRate('');
    setFormError(null);
  };

  const openEdit = (w: WorkerPublic) => {
    setEditing({ mode: 'edit', worker: w });
    setName(w.display_name);
    setNote(w.note ?? '');
    setRate(w.hourly_rate != null ? String(Number(w.hourly_rate)) : '');
    setFormError(null);
  };

  const closeForm = () => {
    setEditing(null);
    setFormError(null);
  };

  const surfaceError = (err: unknown) => {
    const detail = axios.isAxiosError(err)
      ? err.response?.data?.detail
      : undefined;
    setFormError(
      typeof detail === 'string' ? detail : t('labour.worker_save_error'),
    );
  };

  // Synchronous double-submit guard (L-B1 savingRef precedent):
  // isPending only flips after a re-render, so two rapid taps could
  // both reach the mutation — on the ADD path that would create two
  // identical workers (duplicates are legal server-side). The guard
  // lives in run() so the Alert-confirmed duplicate path is covered.
  const submittingRef = useRef(false);

  const submit = async () => {
    if (!editing || busy) return;
    const trimmed = name.trim();
    if (trimmed.length === 0) return;
    setFormError(null);

    const parsedRate = parseRate(rate);
    if (!parsedRate.valid) {
      setFormError(t('labour.rate_invalid'));
      return;
    }

    // Soft duplicate warning: server allows duplicates by design; the
    // confirm only fires when the (changed) name case-insensitively
    // matches another roster row, including deactivated ones.
    const collides = (workers.data ?? []).some(
      (w) =>
        w.display_name.trim().toLowerCase() === trimmed.toLowerCase() &&
        (editing.mode === 'add' || w.worker_id !== editing.worker.worker_id),
    );
    const nameChanged =
      editing.mode === 'add' || trimmed !== editing.worker.display_name;

    const run = async () => {
      if (submittingRef.current) return;
      submittingRef.current = true;
      try {
        if (editing.mode === 'add') {
          await createWorker.mutateAsync({
            display_name: trimmed,
            note: note.trim().length > 0 ? note.trim() : null,
            hourly_rate: parsedRate.value,
          });
        } else {
          // Changed-fields-only PATCH: the backend applies only the
          // fields present (exclude_unset), so omitting untouched
          // fields shrinks the concurrent-admin clobber window.
          // note: explicit null CLEARS a previously set note — an
          // emptied field is an intentional clear.
          const normNote = note.trim().length > 0 ? note.trim() : null;
          const normRate = parsedRate.value;
          const currentRate =
            editing.worker.hourly_rate != null
              ? Number(editing.worker.hourly_rate)
              : null;
          const patch: WorkerUpdateInput = {};
          if (trimmed !== editing.worker.display_name) {
            patch.display_name = trimmed;
          }
          if (normNote !== (editing.worker.note ?? null)) {
            patch.note = normNote;
          }
          if (normRate !== currentRate) {
            patch.hourly_rate = normRate;
          }
          if (Object.keys(patch).length === 0) {
            closeForm();
            return;
          }
          await updateWorker.mutateAsync({
            workerId: editing.worker.worker_id,
            patch,
          });
        }
        closeForm();
      } catch (err) {
        surfaceError(err);
      } finally {
        submittingRef.current = false;
      }
    };

    if (collides && nameChanged) {
      Alert.alert(
        t('labour.duplicate_title'),
        t('labour.duplicate_message', { name: trimmed }),
        [
          { text: t('common.cancel'), style: 'cancel' },
          { text: t('common.yes'), onPress: () => void run() },
        ],
      );
      return;
    }
    await run();
  };

  const confirmActiveToggle = (w: WorkerPublic) => {
    const deactivating = w.is_active;
    Alert.alert(
      t(
        deactivating
          ? 'labour.deactivate_confirm_title'
          : 'labour.reactivate_confirm_title',
      ),
      t(
        deactivating
          ? 'labour.deactivate_confirm_message'
          : 'labour.reactivate_confirm_message',
        { name: w.display_name },
      ),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t(deactivating ? 'labour.deactivate' : 'labour.reactivate'),
          style: deactivating ? 'destructive' : 'default',
          onPress: () => {
            void (async () => {
              try {
                await updateWorker.mutateAsync({
                  workerId: w.worker_id,
                  patch: { is_active: !w.is_active },
                });
                closeForm();
              } catch (err) {
                surfaceError(err);
              }
            })();
          },
        },
      ],
    );
  };

  const form =
    editing !== null ? (
      <View style={s.formCard} testID="worker-form">
        <Text style={s.formTitle}>
          {editing.mode === 'add'
            ? t('labour.add_worker')
            : editing.worker.display_name}
        </Text>
        <TextInput
          value={name}
          onChangeText={setName}
          placeholder={t('labour.field_name')}
          placeholderTextColor="#94a3b8"
          editable={!busy}
          maxLength={120}
          style={s.input}
          testID="worker-name-input"
          accessibilityLabel={t('labour.field_name')}
        />
        <TextInput
          value={note}
          onChangeText={setNote}
          placeholder={t('labour.field_note')}
          placeholderTextColor="#94a3b8"
          editable={!busy}
          maxLength={500}
          style={s.input}
          testID="worker-note-input"
          accessibilityLabel={t('labour.field_note')}
        />
        <TextInput
          value={rate}
          onChangeText={setRate}
          placeholder={t('labour.field_rate')}
          placeholderTextColor="#94a3b8"
          editable={!busy}
          keyboardType="decimal-pad"
          maxLength={10}
          style={s.input}
          testID="worker-rate-input"
          accessibilityLabel={t('labour.field_rate')}
        />
        {formError ? (
          <View style={s.errorBanner} testID="worker-form-error">
            <Text style={s.errorBannerText}>{formError}</Text>
          </View>
        ) : null}
        <View style={s.formButtons}>
          <TouchableOpacity
            onPress={closeForm}
            disabled={busy}
            style={s.cancelBtn}
            accessibilityRole="button"
            testID="worker-form-cancel"
          >
            <Text style={s.cancelBtnText}>{t('common.cancel')}</Text>
          </TouchableOpacity>
          <TouchableOpacity
            onPress={() => void submit()}
            disabled={busy || name.trim().length === 0}
            style={[
              s.saveBtn,
              (busy || name.trim().length === 0) && s.btnDisabled,
            ]}
            accessibilityRole="button"
            testID="worker-form-save"
          >
            {busy ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={s.saveBtnText}>
                {editing.mode === 'add' ? t('labour.add_cta') : t('common.save')}
              </Text>
            )}
          </TouchableOpacity>
        </View>
        {editing.mode === 'edit' ? (
          <TouchableOpacity
            onPress={() => confirmActiveToggle(editing.worker)}
            disabled={busy}
            style={[
              editing.worker.is_active ? s.deactivateBtn : s.reactivateBtn,
              busy && s.btnDisabled,
            ]}
            accessibilityRole="button"
            testID="worker-active-toggle"
          >
            <Text
              style={
                editing.worker.is_active
                  ? s.deactivateBtnText
                  : s.reactivateBtnText
              }
            >
              {t(
                editing.worker.is_active
                  ? 'labour.deactivate'
                  : 'labour.reactivate',
              )}
            </Text>
          </TouchableOpacity>
        ) : null}
      </View>
    ) : null;

  return (
    <View style={s.root}>
      {me.isLoading ? (
        <View style={s.state} testID="workers-me-loading">
          <ActivityIndicator color="#1e293b" />
        </View>
      ) : me.isError ? (
        // Unresolved identity (weak network) is NOT the same as
        // forbidden — offer an in-view retry instead of telling a
        // possible admin they lack permission. Still fails closed.
        <View style={s.state} testID="workers-me-error">
          <Text style={[s.stateText, s.errorText]}>{t('common.error')}</Text>
          <Pressable
            onPress={() => void me.refetch()}
            style={({ pressed }) => [s.linkBtn, pressed && s.pressed]}
            accessibilityRole="button"
            testID="workers-me-retry"
          >
            <Text style={s.linkBtnText}>{t('common.retry')}</Text>
          </Pressable>
        </View>
      ) : !isAdmin ? (
        // Resolved contributor identity. Writes are require_admin
        // server-side regardless.
        <View style={s.state} testID="workers-forbidden">
          <Text style={s.stateText}>{t('labour.workers_forbidden')}</Text>
        </View>
      ) : (
        <>
          {/* B4-2: the ADD entry point moved here from the removed
              screen header (the embedding Labour screen owns the top
              bar and knows nothing about the roster). Same control:
              testID, label and disabled behaviour unchanged. */}
          <View style={s.toolbar}>
            <Pressable
              onPress={openAdd}
              disabled={busy}
              hitSlop={12}
              testID="workers-add"
              accessibilityRole="button"
              accessibilityLabel={t('labour.add_worker')}
              style={({ pressed }) => [
                s.newBtn,
                (pressed || busy) && s.pressed,
              ]}
            >
              <Text style={s.newBtnText}>{'＋'}</Text>
            </Pressable>
          </View>
          <KeyboardAvoidingView
            style={s.flex}
            behavior={Platform.OS === 'ios' ? 'padding' : undefined}
          >
            <FlatList
              data={visible}
              keyExtractor={(w) => w.worker_id}
              keyboardShouldPersistTaps="handled"
              ListHeaderComponent={
                <View>
                  {form}
                  <Pressable
                    onPress={() => setShowInactive((v) => !v)}
                    style={s.toggleRow}
                    accessibilityRole="checkbox"
                    accessibilityState={{ checked: showInactive }}
                    testID="workers-show-inactive"
                  >
                    <View
                      style={[s.checkbox, showInactive && s.checkboxChecked]}
                    >
                      {showInactive ? (
                        <Text style={s.checkmark}>{'✓'}</Text>
                      ) : null}
                    </View>
                    <Text style={s.toggleLabel}>{t('labour.show_inactive')}</Text>
                  </Pressable>
                </View>
              }
              renderItem={({ item }) => (
                <Pressable
                  onPress={() => openEdit(item)}
                  disabled={busy}
                  testID={`roster-row-${item.worker_id}`}
                  accessibilityRole="button"
                  accessibilityLabel={item.display_name}
                  style={({ pressed }) => [s.row, pressed && s.rowPressed]}
                >
                  <View style={s.rowMain}>
                    <Text style={s.rowName} numberOfLines={1}>
                      {item.display_name}
                    </Text>
                    {item.note ? (
                      <Text style={s.rowNote} numberOfLines={1}>
                        {item.note}
                      </Text>
                    ) : null}
                  </View>
                  <View style={s.rowRight}>
                    {item.hourly_rate != null ? (
                      <Text style={s.rowRate} testID={`rate-${item.worker_id}`}>
                        {t('labour.rate_per_hour', {
                          amount: formatMoney(item.hourly_rate),
                        })}
                      </Text>
                    ) : null}
                    {!item.is_active ? (
                      <View style={s.inactivePill}>
                        <Text style={s.inactiveText}>
                          {t('labour.deactivated_badge')}
                        </Text>
                      </View>
                    ) : null}
                  </View>
                </Pressable>
              )}
              style={s.list}
              contentContainerStyle={
                visible.length === 0 ? s.listEmptyContainer : s.listContainer
              }
              refreshControl={
                <RefreshControl
                  refreshing={refreshing}
                  onRefresh={onRefresh}
                  tintColor="#1e293b"
                />
              }
              testID="workers-list"
              ListEmptyComponent={
                workers.isLoading ? (
                  <View style={s.state} testID="workers-loading">
                    <ActivityIndicator color="#1e293b" />
                    <Text style={s.stateText}>{t('common.loading')}</Text>
                  </View>
                ) : workers.isError ? (
                  <View style={s.state} testID="workers-error">
                    <Text style={[s.stateText, s.errorText]}>
                      {t('labour.workers_error')}
                    </Text>
                    <Pressable
                      onPress={() => void workers.refetch()}
                      style={({ pressed }) => [s.linkBtn, pressed && s.pressed]}
                      accessibilityRole="button"
                      testID="workers-retry"
                    >
                      <Text style={s.linkBtnText}>{t('common.retry')}</Text>
                    </Pressable>
                  </View>
                ) : (
                  <View style={s.state} testID="workers-empty">
                    <Text style={s.stateText}>{t('labour.roster_empty')}</Text>
                  </View>
                )
              }
            />
          </KeyboardAvoidingView>
        </>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: tokens.bg },
  flex: { flex: 1 },
  pressed: { opacity: 0.5 },
  toolbar: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    paddingHorizontal: 8,
    paddingTop: 4,
  },
  newBtn: {
    minWidth: 72,
    alignItems: 'flex-end',
    paddingHorizontal: 12,
    paddingVertical: 4,
  },
  newBtnText: { fontSize: 22, color: '#1e293b', fontWeight: '600' },
  list: { flex: 1 },
  listContainer: { paddingHorizontal: 16, paddingBottom: 24 },
  listEmptyContainer: { flexGrow: 1, justifyContent: 'center', paddingHorizontal: 16 },
  state: { alignItems: 'center', padding: 24, gap: 12 },
  stateText: { color: '#64748b', fontSize: 15, textAlign: 'center' },
  errorText: { color: '#b91c1c' },
  linkBtn: { paddingHorizontal: 12, paddingVertical: 8 },
  linkBtnText: { color: '#1e293b', fontSize: 15, fontWeight: '600' },
  formCard: {
    borderWidth: 1,
    borderColor: '#e2e8f0',
    borderRadius: 8,
    padding: 12,
    marginTop: 12,
    gap: 10,
    backgroundColor: tokens.surface,
  },
  formTitle: { fontSize: 15, fontWeight: '600', color: '#0f172a' },
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
  errorBanner: {
    backgroundColor: '#fef2f2',
    borderWidth: 1,
    borderColor: '#fecaca',
    borderRadius: 6,
    padding: 10,
  },
  errorBannerText: { color: '#991b1b', fontSize: 13 },
  formButtons: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: 8,
  },
  cancelBtn: {
    backgroundColor: '#f1f5f9',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 6,
  },
  cancelBtnText: { color: '#334155', fontWeight: '600', fontSize: 14 },
  saveBtn: {
    backgroundColor: '#1e293b',
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 6,
    alignItems: 'center',
    minWidth: 80,
  },
  saveBtnText: { color: '#ffffff', fontWeight: '600', fontSize: 14 },
  btnDisabled: { opacity: 0.5 },
  deactivateBtn: {
    borderWidth: 1,
    borderColor: '#fecaca',
    borderRadius: 6,
    paddingVertical: 10,
    alignItems: 'center',
  },
  deactivateBtnText: { color: '#b91c1c', fontWeight: '600', fontSize: 14 },
  reactivateBtn: {
    borderWidth: 1,
    borderColor: '#cbd5e1',
    borderRadius: 6,
    paddingVertical: 10,
    alignItems: 'center',
  },
  reactivateBtnText: { color: '#1e293b', fontWeight: '600', fontSize: 14 },
  toggleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
  },
  checkbox: {
    width: 22,
    height: 22,
    borderWidth: 1.5,
    borderColor: '#94a3b8',
    borderRadius: 4,
    marginRight: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkboxChecked: { backgroundColor: '#1e293b', borderColor: '#1e293b' },
  checkmark: { color: '#ffffff', fontSize: 14, fontWeight: '700' },
  toggleLabel: { color: '#0f172a', fontSize: 14 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
    backgroundColor: tokens.surface,
  },
  rowPressed: { backgroundColor: '#f1f5f9' },
  rowMain: { flex: 1 },
  rowName: { fontSize: 16, fontWeight: '500', color: '#0f172a' },
  rowNote: { fontSize: 13, color: '#64748b', marginTop: 2 },
  rowRight: { alignItems: 'flex-end', gap: 4, marginLeft: 8 },
  rowRate: {
    fontSize: 14,
    fontWeight: '600',
    color: '#0f172a',
    fontVariant: ['tabular-nums'],
  },
  inactivePill: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 10,
    backgroundColor: '#fef3c7',
    marginLeft: 8,
  },
  inactiveText: { fontSize: 10, fontWeight: '600', color: '#92400e' },
});
