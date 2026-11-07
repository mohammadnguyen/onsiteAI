import { useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useTranslation } from 'react-i18next';

import { useJobs } from '../api/hooks/useJobs';
import type { PvOut } from '../api/potentialVariations';
import { JobPickerSheet } from '../components/JobPickerSheet';
import { tokens } from '../ui/tokens';

/**
 * A flag's job (DEC-PV-001) - the task's job section's idea
 * (src/tasks/TaskJobSection.tsx). The flag keeps the job it was made with;
 * a later correction of its entry never moves it. A flag with no job cannot
 * be found under a job, so it says so and offers to set one. Set it while
 * empty (the flagger or the builder), or move it (the builder, after a
 * confirmation). The server says which applies in `permissions`; a job is
 * never cleared.
 */
export function PvJobSection({
  pv,
  disabled,
  onSetJob,
}: {
  pv: PvOut;
  disabled: boolean;
  onSetJob: (jobId: string) => void;
}) {
  const { t } = useTranslation();
  const jobs = useJobs();
  const active = useMemo(() => (jobs.data ?? []).filter((j) => j.status === 'active'), [jobs.data]);
  const nameOf = (jobId: string) => jobs.data?.find((j) => j.job_id === jobId)?.job_name;
  const [picking, setPicking] = useState(false);
  const [moveTo, setMoveTo] = useState<string | null>(null);

  const p = pv.permissions;
  const canPick = p.set_job || p.change_job;
  // Shown only when the server says this user may read the entry.
  const entryMoved =
    pv.source.readable &&
    pv.source.job_id !== null &&
    pv.source.job_id !== (pv.job?.job_id ?? null);

  return (
    <View style={s.wrap}>
      <View style={s.row}>
        <Text style={s.value} testID="pv-job-name">
          {pv.job?.job_name ?? t('potentialVariations.list.no_job')}
        </Text>
        {canPick ? (
          <Pressable
            onPress={() => !disabled && setPicking(true)}
            disabled={disabled}
            accessibilityRole="button"
            hitSlop={8}
            testID="pv-job-pick"
          >
            <Text style={[s.link, disabled ? s.dim : null]}>
              {p.set_job ? t('potentialVariations.detail.set_job') : t('potentialVariations.detail.change_job')}
            </Text>
          </Pressable>
        ) : null}
      </View>
      {!pv.job && p.set_job ? (
        <Text style={s.prompt} testID="pv-job-prompt">
          {t('potentialVariations.detail.add_job_prompt')}
        </Text>
      ) : null}
      {entryMoved ? (
        <Text style={s.note} testID="pv-entry-moved">
          {t('potentialVariations.detail.job_moved_note', {
            job: nameOf(pv.source.job_id as string) ?? t('potentialVariations.detail.job_moved_unknown'),
          })}
        </Text>
      ) : null}
      {moveTo ? (
        // Moving a flag that already has a job is the builder's deliberate act.
        <View style={s.confirmBox} testID="pv-job-confirm-box">
          <Text style={s.confirmTitle}>
            {t('potentialVariations.detail.job_move_title', { job: nameOf(moveTo) ?? '' })}
          </Text>
          <View style={s.row}>
            <Pressable
              onPress={() => {
                onSetJob(moveTo);
                setMoveTo(null);
              }}
              disabled={disabled}
              accessibilityRole="button"
              testID="pv-job-confirm"
            >
              <Text style={[s.link, disabled ? s.dim : null]}>
                {t('potentialVariations.detail.job_move_confirm')}
              </Text>
            </Pressable>
            <Pressable onPress={() => setMoveTo(null)} accessibilityRole="button">
              <Text style={s.cancel}>{t('common.cancel')}</Text>
            </Pressable>
          </View>
        </View>
      ) : null}
      <JobPickerSheet
        visible={picking}
        jobs={active}
        selectedJobId={pv.job?.job_id ?? null}
        labelFor={(j) => j.job_name}
        onClose={() => setPicking(false)}
        onSelect={(jobId) => {
          setPicking(false);
          if (jobId === pv.job?.job_id) return;
          if (p.set_job) onSetJob(jobId);
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
  prompt: { color: tokens.warnMid, fontSize: 13 },
  note: { color: tokens.warnMid, fontSize: 13 },
  confirmBox: {
    gap: 8,
    padding: 12,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: tokens.line,
    backgroundColor: tokens.surface,
  },
  confirmTitle: { color: tokens.ink, fontWeight: '600' },
});
