import { useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { router, useLocalSearchParams } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';

import { useMe } from '../../src/api/hooks/useAuth';
import { resolveApiErrorMessage } from '../../src/api/errors';
import { getTask, patchTask, type TaskOut, type TaskPatch } from '../../src/api/tasks';
import { BackLink } from '../../src/siteLog/BackLink';
import { useScreenActive } from '../../src/siteLog/useScreenActive';
import { useAuthStore } from '../../src/store/auth';
import { AssigneeField } from '../../src/tasks/AssigneeField';
import { DueDateField } from '../../src/tasks/DueDateField';
import { TaskJobSection } from '../../src/tasks/TaskJobSection';
import { TaskStatusChips } from '../../src/tasks/TaskStatusChips';
import { isUsableTitle } from '../../src/tasks/proposedTitle';
import { formatDateAU } from '../../src/util/dates';
import { tokens } from '../../src/ui/tokens';

/**
 * One task (plan v2 §6.4): one-tap status, the edits this user may make,
 * its job, and the way back to the Site Log entry it came from.
 *
 * What may be changed comes from the server (`permissions`); the screen only
 * shows or hides controls. The source entry opens under the entry's own
 * rules: when this user may not read it, the screen says so instead of
 * offering a link that would fail.
 */
export default function TaskDetail() {
  const { t } = useTranslation();
  const { id, notice } = useLocalSearchParams<{ id: string; notice?: string }>();
  const qc = useQueryClient();
  const { data: me } = useMe();
  const activeRef = useScreenActive();
  const q = useQuery({
    queryKey: ['tasks', 'detail', id],
    queryFn: () => getTask(String(id)),
    enabled: Boolean(id),
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [titleDraft, setTitleDraft] = useState<string | null>(null);

  const apply = async (patch: TaskPatch) => {
    if (saving) return;
    const startedUnder = useAuthStore.getState().sessionNonce;
    setSaving(true);
    setError(null);
    try {
      const updated = await patchTask(String(id), patch);
      // Same account: the server's answer is true and every list must hear
      // of it, whether or not the user is still on this screen.
      if (useAuthStore.getState().sessionNonce !== startedUnder) return;
      qc.setQueryData<TaskOut>(['tasks', 'detail', id], updated);
      void qc.invalidateQueries({ queryKey: ['tasks', 'list'] });
      void qc.invalidateQueries({ queryKey: ['tasks', 'event'] });
      // A title being edited is put away only when it was the title that was
      // saved; changing the status meanwhile must not throw the typing away.
      if (activeRef.current && 'title' in patch) setTitleDraft(null);
    } catch (err) {
      if (useAuthStore.getState().sessionNonce !== startedUnder || !activeRef.current) return;
      setError(resolveApiErrorMessage(err, t, t('tasks.detail.save_failed')));
    } finally {
      setSaving(false);
    }
  };

  if (q.isLoading) return <ActivityIndicator style={s.spinner} />;
  if (!q.data) {
    const missing = axios.isAxiosError(q.error) && q.error.response?.status === 404;
    return (
      <SafeAreaView style={s.safe} edges={['top']}>
        <View style={s.body}>
          <BackLink fallback="/tasks" />
          <Text style={s.note}>
            {missing ? t('tasks.detail.not_found') : t('tasks.list.load_failed')}
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  const task = q.data;
  const p = task.permissions;
  return (
    <SafeAreaView style={s.safe} edges={['top', 'bottom']}>
      <ScrollView contentContainerStyle={s.body} keyboardShouldPersistTaps="handled">
        <BackLink fallback="/tasks" />
        {notice === 'already_created' ? (
          <Text style={s.notice} testID="task-already-created">
            {t('tasks.pending.found')}
          </Text>
        ) : null}

        {titleDraft === null ? (
          <View style={s.titleRow}>
            <Text style={s.h1} testID="task-title-text">
              {task.title}
            </Text>
            {p.edit ? (
              <Pressable
                onPress={() => setTitleDraft(task.title)}
                accessibilityRole="button"
                hitSlop={8}
                testID="task-title-edit"
              >
                <Text style={s.link}>{t('tasks.detail.edit_title')}</Text>
              </Pressable>
            ) : null}
          </View>
        ) : (
          <View style={s.titleEdit}>
            <TextInput
              value={titleDraft}
              onChangeText={setTitleDraft}
              editable={!saving}
              multiline
              style={s.input}
              testID="task-title-input"
            />
            <View style={s.row}>
              <Pressable
                onPress={() => void apply({ title: titleDraft.trim() })}
                disabled={saving || !isUsableTitle(titleDraft)}
                accessibilityRole="button"
                testID="task-title-save"
              >
                <Text style={[s.link, saving || !isUsableTitle(titleDraft) ? s.dim : null]}>
                  {t('tasks.detail.save_title')}
                </Text>
              </Pressable>
              <Pressable onPress={() => setTitleDraft(null)} accessibilityRole="button">
                <Text style={s.cancel}>{t('common.cancel')}</Text>
              </Pressable>
            </View>
          </View>
        )}

        <Text style={s.label}>{t('tasks.detail.status')}</Text>
        <TaskStatusChips
          value={task.status}
          disabled={!p.change_status || saving}
          onChange={(status) => void apply({ status })}
        />

        <Text style={s.label}>{t('tasks.new.assignee')}</Text>
        {p.edit ? (
          <AssigneeField
            value={task.assignee?.user_id ?? null}
            valueName={task.assignee?.full_name ?? null}
            me={me ? { user_id: me.user_id, full_name: me.full_name } : null}
            assignOthers={p.assign_others}
            disabled={saving}
            onChange={(userId) => void apply({ assignee_user_id: userId })}
          />
        ) : (
          <Text style={s.value}>
            {task.assignee?.full_name ?? t('tasks.assignee.unassigned')}
          </Text>
        )}

        <Text style={s.label}>{t('tasks.new.due')}</Text>
        {p.edit ? (
          <DueDateField
            value={task.due_date}
            disabled={saving}
            onChange={(due) => void apply({ due_date: due })}
          />
        ) : (
          // Read-only for anyone who may not edit: no greyed-out controls.
          <Text style={s.value} testID="task-due-readonly">
            {task.due_date ? formatDateAU(task.due_date) : t('tasks.due.none')}
          </Text>
        )}

        <Text style={s.label}>{t('tasks.detail.job')}</Text>
        <TaskJobSection
          task={task}
          disabled={saving}
          onSetJob={(jobId) => void apply({ job_id: jobId })}
          onChangeJob={(jobId, reason) => void apply({ job_id: jobId, reason })}
        />

        <Text style={s.label}>{t('tasks.detail.source')}</Text>
        {task.source.readable ? (
          <Pressable
            onPress={() => router.push(`/site-log/${task.source.site_log_event_id}` as never)}
            accessibilityRole="button"
            testID="task-open-source"
          >
            <Text style={s.link}>{t('tasks.detail.open_source')}</Text>
          </Pressable>
        ) : (
          <Text style={s.value} testID="task-source-hidden">
            {t('tasks.detail.source_hidden')}
          </Text>
        )}

        {saving ? <ActivityIndicator /> : null}
        {error ? <Text style={s.error}>{error}</Text> : null}
        <Text style={s.meta}>
          {t('tasks.detail.created_by', {
            name: task.created_by?.full_name ?? '—',
            date: new Date(task.created_at).toLocaleDateString(),
          })}
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  body: { padding: 16, gap: 10 },
  spinner: { marginTop: 48 },
  notice: {
    color: tokens.ok,
    backgroundColor: tokens.okBg,
    borderColor: tokens.okBorder,
    borderWidth: 1,
    borderRadius: 10,
    padding: 10,
    fontWeight: '600',
  },
  titleRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  h1: { flex: 1, fontSize: 21, fontWeight: '700', color: tokens.ink },
  titleEdit: { gap: 8 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 16 },
  input: {
    minHeight: 56,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 12,
    padding: 12,
    backgroundColor: tokens.surface,
    color: tokens.ink,
    fontSize: 16,
    textAlignVertical: 'top',
  },
  label: { color: tokens.ink2, fontWeight: '600', marginTop: 8 },
  value: { color: tokens.ink, fontSize: 15 },
  link: { color: tokens.primary, fontWeight: '600', fontSize: 15 },
  cancel: { color: tokens.ink3, fontWeight: '600', fontSize: 15 },
  dim: { opacity: 0.4 },
  note: { color: tokens.ink3, marginTop: 16 },
  error: { color: tokens.bad },
  meta: { color: tokens.ink3, fontSize: 12, marginTop: 12 },
});
