import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
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

import { useMe } from '../../src/api/hooks/useAuth';
import { useJobs } from '../../src/api/hooks/useJobs';
import { getEvent } from '../../src/api/siteLog';
import { BackLink } from '../../src/siteLog/BackLink';
import { newCaptureId } from '../../src/siteLog/ids';
import { useScreenActive } from '../../src/siteLog/useScreenActive';
import { useAuthStore } from '../../src/store/auth';
import {
  usePendingCreatesHydrated,
  useTaskPendingCreates,
  type PendingTaskCreate,
} from '../../src/store/taskPendingCreates';
import { AssigneeField } from '../../src/tasks/AssigneeField';
import { DueDateField } from '../../src/tasks/DueDateField';
import { PendingCreatePanel } from '../../src/tasks/PendingCreatePanel';
import { sendCreate, type SendOutcome } from '../../src/tasks/createFlow';
import {
  TITLE_MAX_CHARS,
  isUsableTitle,
  proposedTitleFrom,
  titleLength,
} from '../../src/tasks/proposedTitle';
import { PrimaryButton } from '../../src/ui/kit';
import { tokens } from '../../src/ui/tokens';

/**
 * Create a task from one Site Log entry (plan v2 §6.2).
 *
 * The title starts as the entry's first line and the user confirms or edits
 * it before saving; the job comes from the entry and is shown, not chosen.
 * Saving writes a pending record to the phone first, then sends - so a lost
 * answer is recovered by asking the server, never by sending a second
 * create under a new key (src/tasks/createFlow.ts). If this entry already
 * has an unresolved create, that is dealt with before a new one can start.
 */
export default function NewTaskScreen() {
  const { t } = useTranslation();
  const { eventId } = useLocalSearchParams<{ eventId: string }>();
  const qc = useQueryClient();
  const { data: me } = useMe();
  const tokenUserId = useAuthStore((st) => st.userId);
  const userId = me?.user_id ?? tokenUserId;
  const isAdmin = me?.role === 'admin';
  const activeRef = useScreenActive();
  const hydrated = usePendingCreatesHydrated();
  const pending = useTaskPendingCreates((st) =>
    userId && eventId ? st.forEvent(userId, eventId) : undefined,
  );

  const entry = useQuery({
    queryKey: ['site-log', 'event', eventId],
    queryFn: () => getEvent(String(eventId)),
    enabled: Boolean(eventId),
  });
  const jobs = useJobs();
  const jobName = useMemo(() => {
    const id = entry.data?.job_id;
    if (!id) return null;
    return jobs.data?.find((j) => j.job_id === id)?.job_name ?? t('siteLog.detail.assigned');
  }, [entry.data?.job_id, jobs.data, t]);

  const [title, setTitle] = useState<string | null>(null);
  const [assignee, setAssignee] = useState<{ id: string | null; name: string | null }>({
    id: null,
    name: null,
  });
  const [due, setDue] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One key per attempt, kept across double taps. A refusal the server gave
  // on purpose starts a new one; an unknown result keeps it in the store.
  const keyRef = useRef<string>(newCaptureId());

  // The title starts as the entry's first line, once, when the entry loads.
  useEffect(() => {
    if (title === null && entry.data) {
      setTitle(proposedTitleFrom(entry.data.revision.body_text));
    }
  }, [entry.data, title]);

  const finish = (outcome: SendOutcome) => {
    if (outcome.kind === 'created') {
      void qc.invalidateQueries({ queryKey: ['tasks'] });
      router.replace(`/tasks/${outcome.task.task_id}` as never);
      return;
    }
    if (outcome.kind === 'refused') {
      keyRef.current = newCaptureId();
      setError(outcome.detail ?? t('tasks.new.save_failed'));
      return;
    }
    // Unknown: the pending record stays and the panel below takes over.
    setError(null);
  };

  const onSave = async () => {
    if (sending || !userId || !eventId || title === null || !isUsableTitle(title)) return;
    // Read before any await: this is the session the user tapped Save in.
    const startedUnder = useAuthStore.getState().sessionNonce;
    setSending(true);
    setError(null);
    const record: PendingTaskCreate = {
      create_client_id: keyRef.current,
      user_id: userId,
      site_log_event_id: eventId,
      title: title.trim(),
      assignee_user_id: assignee.id,
      assignee_name: assignee.name,
      due_date: due,
      created_at: Date.now(),
    };
    try {
      const outcome = await sendCreate(record);
      if (useAuthStore.getState().sessionNonce !== startedUnder || !activeRef.current) return;
      finish(outcome);
    } finally {
      setSending(false);
    }
  };

  if (!eventId) return null;

  let body: ReactNode;
  if (!hydrated || entry.isLoading) {
    body = <ActivityIndicator style={s.spinner} />;
  } else if (pending && !sending) {
    // Only a create that is NOT being sent from this screen right now: while
    // this screen's own request is out, its record exists too, and asking
    // the server about it then would race the request it is waiting for.
    body = (
      <PendingCreatePanel
        pending={pending}
        screenActive={activeRef}
        onRefused={(detail, values) => {
          // Nothing was created under that key; the next save gets a new one,
          // starting from the values the user had chosen.
          keyRef.current = newCaptureId();
          setTitle(values.title);
          setAssignee({ id: values.assignee_user_id, name: values.assignee_name });
          setDue(values.due_date);
          setError(detail ?? t('tasks.new.save_failed'));
        }}
      />
    );
  } else if (!entry.data) {
    body = <Text style={s.note}>{t('tasks.new.load_failed')}</Text>;
  } else if (entry.data.revision.withdrawn) {
    body = <Text style={s.note}>{t('tasks.new.entry_withdrawn')}</Text>;
  } else {
    const current = title ?? '';
    const tooLong = titleLength(current) > TITLE_MAX_CHARS;
    body = (
      <View style={s.form}>
        <Text style={s.label}>{t('tasks.new.title_label')}</Text>
        <TextInput
          value={current}
          onChangeText={setTitle}
          editable={!sending}
          placeholder={t('tasks.new.title_placeholder')}
          placeholderTextColor={tokens.muted}
          multiline
          style={[s.input, tooLong ? s.inputError : null]}
          testID="task-title"
        />
        <Text style={tooLong ? s.error : s.hint}>
          {tooLong ? t('tasks.new.title_too_long', { max: TITLE_MAX_CHARS }) : t('tasks.new.title_hint')}
        </Text>

        <Text style={s.label}>{t('tasks.new.assignee')}</Text>
        <AssigneeField
          value={assignee.id}
          valueName={assignee.name}
          me={me ? { user_id: me.user_id, full_name: me.full_name } : null}
          assignOthers={isAdmin}
          disabled={sending}
          onChange={(id, name) => setAssignee({ id, name })}
        />

        <Text style={s.label}>{t('tasks.new.due')}</Text>
        <DueDateField value={due} disabled={sending} onChange={setDue} />

        <Text style={s.label}>{t('tasks.new.job')}</Text>
        <Text style={s.value} testID="task-job">
          {jobName ? t('tasks.new.job_from_entry', { job: jobName }) : t('tasks.new.no_job')}
        </Text>

        {error ? <Text style={s.error}>{error}</Text> : null}
        <PrimaryButton
          label={sending ? t('tasks.new.saving') : t('tasks.new.save')}
          onPress={() => void onSave()}
          disabled={sending || !isUsableTitle(current)}
          testID="task-save"
        />
      </View>
    );
  }

  return (
    <SafeAreaView style={s.safe} edges={['top', 'bottom']}>
      <ScrollView contentContainerStyle={s.body} keyboardShouldPersistTaps="handled">
        <BackLink fallback={`/site-log/${eventId}`} />
        <Text style={s.h1}>{t('tasks.new.title')}</Text>
        {body}
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  body: { padding: 16, gap: 12 },
  h1: { fontSize: 22, fontWeight: '700', color: tokens.ink },
  form: { gap: 10 },
  label: { color: tokens.ink2, fontWeight: '600', marginTop: 6 },
  value: { color: tokens.ink, fontSize: 15 },
  input: {
    minHeight: 64,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 12,
    padding: 12,
    backgroundColor: tokens.surface,
    color: tokens.ink,
    fontSize: 16,
    textAlignVertical: 'top',
  },
  inputError: { borderColor: tokens.bad },
  hint: { color: tokens.ink3, fontSize: 12 },
  error: { color: tokens.bad, fontSize: 13 },
  note: { color: tokens.ink3, marginTop: 16 },
  spinner: { marginTop: 32 },
});
