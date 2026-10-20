import { useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { useTranslation } from 'react-i18next';

import { useJobs } from '../api/hooks/useJobs';
import type { TaskOut } from '../api/tasks';
import { JobPickerSheet } from '../components/JobPickerSheet';
import { tokens } from '../ui/tokens';

/**
 * A task's job (plan v2 §3a). The task keeps the job it was created with;
 * a later correction of its source entry never moves it. Changing it is a
 * deliberate act: set it when empty (creator or admin), or move it (admin,
 * with a reason). The server says which applies in `permissions`.
 */
export function TaskJobSection({
  task,
  disabled,
  onSetJob,
  onChangeJob,
}: {
  task: TaskOut;
  disabled: boolean;
  onSetJob: (jobId: string) => void;
  onChangeJob: (jobId: string, reason: string) => void;
}) {
  const { t } = useTranslation();
  const jobs = useJobs();
  const active = useMemo(
    () => (jobs.data ?? []).filter((j) => j.status === 'active'),
    [jobs.data],
  );
  const nameOf = (jobId: string) => jobs.data?.find((j) => j.job_id === jobId)?.job_name;
  const [picking, setPicking] = useState(false);
  const [moveTo, setMoveTo] = useState<string | null>(null);
  const [reason, setReason] = useState('');

  const canPick = task.permissions.set_job || task.permissions.change_job;
  // Shown only when the server says this user may read the entry.
  const entryMoved =
    task.source.readable &&
    task.source.job_id !== null &&
    task.source.job_id !== (task.job?.job_id ?? null);

  return (
    <View style={s.wrap}>
      <View style={s.row}>
        <Text style={s.value} testID="task-job-name">
          {task.job?.job_name ?? t('tasks.detail.no_job')}
        </Text>
        {canPick ? (
          <Pressable
            onPress={() => !disabled && setPicking(true)}
            disabled={disabled}
            accessibilityRole="button"
            hitSlop={8}
            testID="task-job-pick"
          >
            <Text style={[s.link, disabled ? s.dim : null]}>
              {task.permissions.set_job ? t('tasks.detail.set_job') : t('tasks.detail.change_job')}
            </Text>
          </Pressable>
        ) : null}
      </View>
      {entryMoved ? (
        <Text style={s.note} testID="task-entry-moved">
          {t('tasks.detail.job_moved_note', {
            job: nameOf(task.source.job_id as string) ?? t('tasks.detail.job_moved_unknown'),
          })}
        </Text>
      ) : null}
      {moveTo ? (
        // Moving a task that already has a job needs a reason, as the
        // Site Log's own re-link does.
        <View style={s.reasonBox}>
          <Text style={s.reasonTitle}>
            {t('tasks.detail.job_reason_title', { job: nameOf(moveTo) ?? '' })}
          </Text>
          <TextInput
            value={reason}
            onChangeText={setReason}
            placeholder={t('tasks.detail.job_reason_placeholder')}
            placeholderTextColor={tokens.muted}
            style={s.input}
            testID="task-job-reason"
          />
          <View style={s.row}>
            <Pressable
              onPress={() => {
                if (!reason.trim()) return;
                onChangeJob(moveTo, reason.trim());
                setMoveTo(null);
                setReason('');
              }}
              disabled={disabled || !reason.trim()}
              accessibilityRole="button"
              testID="task-job-confirm"
            >
              <Text style={[s.link, disabled || !reason.trim() ? s.dim : null]}>
                {t('tasks.detail.job_confirm')}
              </Text>
            </Pressable>
            <Pressable
              onPress={() => {
                setMoveTo(null);
                setReason('');
              }}
              accessibilityRole="button"
            >
              <Text style={s.cancel}>{t('common.cancel')}</Text>
            </Pressable>
          </View>
        </View>
      ) : null}
      <JobPickerSheet
        visible={picking}
        jobs={active}
        selectedJobId={task.job?.job_id ?? null}
        labelFor={(j) => j.job_name}
        onClose={() => setPicking(false)}
        onSelect={(jobId) => {
          setPicking(false);
          if (jobId === task.job?.job_id) return;
          if (task.permissions.set_job) onSetJob(jobId);
          else setMoveTo(jobId);
        }}
      />
    </View>
  );
}

const s = StyleSheet.create({
  wrap: { gap: 8 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 16 },
  value: { color: tokens.ink, fontSize: 15, flexShrink: 1 },
  link: { color: tokens.primary, fontWeight: '600', fontSize: 15 },
  cancel: { color: tokens.ink3, fontWeight: '600', fontSize: 15 },
  dim: { opacity: 0.4 },
  note: { color: tokens.warnMid, fontSize: 13 },
  reasonBox: {
    gap: 8,
    padding: 12,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: tokens.line,
    backgroundColor: tokens.surface,
  },
  reasonTitle: { color: tokens.ink, fontWeight: '600' },
  input: {
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    color: tokens.ink,
    backgroundColor: tokens.surfaceSub,
  },
});
