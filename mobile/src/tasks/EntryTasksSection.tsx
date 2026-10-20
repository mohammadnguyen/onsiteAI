import axios from 'axios';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';

import { useMe } from '../api/hooks/useAuth';
import { listEventTasks } from '../api/tasks';
import { useAuthStore } from '../store/auth';
import { useTaskPendingCreates } from '../store/taskPendingCreates';
import { StatusBadge } from '../ui/kit';
import { tokens } from '../ui/tokens';
import { taskStatusBadgeKey } from './status';

/**
 * "Tasks from this entry", on a Site Log entry's screen (plan v2 §6.1).
 *
 * Lists the tasks made from this entry that this user may see, offers
 * "Create task" (not on a withdrawn entry), and flags a create from here
 * whose result is still unknown. The entry itself is never changed: a task
 * carries its own copy of what it needs.
 */
export function EntryTasksSection({
  eventId,
  withdrawn,
}: {
  eventId: string;
  withdrawn: boolean;
}) {
  const { t } = useTranslation();
  const { data: me } = useMe();
  const tokenUserId = useAuthStore((s) => s.userId);
  const userId = me?.user_id ?? tokenUserId;
  const pending = useTaskPendingCreates((s) =>
    userId ? s.forEvent(userId, eventId) : undefined,
  );
  const q = useQuery({
    queryKey: ['tasks', 'event', eventId],
    queryFn: () => listEventTasks(eventId),
  });

  // A server without tasks answers 404 on this route; the entry itself was
  // just read, so that means "no task feature here", not "no entry". A create
  // from here still in doubt stays on the phone, and the screen says so.
  if (q.isError && axios.isAxiosError(q.error) && q.error.response?.status === 404) {
    return (
      <View style={s.section} testID="entry-tasks-unavailable">
        <Text style={s.note}>{t('tasks.unavailable')}</Text>
        {pending ? (
          <Text style={s.note} testID="entry-task-pending-kept">
            {t('tasks.pending.kept_unavailable')}
          </Text>
        ) : null}
      </View>
    );
  }

  const openCreate = () => router.push(`/tasks/new?eventId=${eventId}` as never);

  return (
    <View style={s.section} testID="entry-tasks">
      <Text style={s.title}>{t('tasks.entry_section.title')}</Text>
      {pending ? (
        <Pressable
          style={s.pending}
          onPress={openCreate}
          accessibilityRole="button"
          testID="entry-task-pending"
        >
          <Text style={s.pendingText}>{t('tasks.pending.banner')}</Text>
        </Pressable>
      ) : null}
      {q.isLoading ? <ActivityIndicator /> : null}
      {q.isError ? <Text style={s.note}>{t('tasks.entry_section.load_failed')}</Text> : null}
      {q.data && q.data.length === 0 ? (
        <Text style={s.note}>{t('tasks.entry_section.none')}</Text>
      ) : null}
      {(q.data ?? []).map((task) => (
        <Pressable
          key={task.task_id}
          style={s.row}
          onPress={() => router.push(`/tasks/${task.task_id}` as never)}
          accessibilityRole="button"
          testID={`entry-task:${task.task_id}`}
        >
          <Text style={s.rowText} numberOfLines={2}>
            {task.title}
          </Text>
          <StatusBadge
            status={taskStatusBadgeKey(task.status)}
            label={t(`tasks.status.${task.status}`)}
          />
        </Pressable>
      ))}
      {!withdrawn && !pending ? (
        <Pressable
          style={s.create}
          onPress={openCreate}
          accessibilityRole="button"
          testID="entry-task-create"
        >
          <Text style={s.createText}>{t('tasks.entry_section.create')}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const s = StyleSheet.create({
  section: { marginTop: 12, gap: 8 },
  title: { color: tokens.ink, fontWeight: '700', fontSize: 16 },
  note: { color: tokens.ink3, fontSize: 13 },
  pending: {
    backgroundColor: tokens.warnBg,
    borderColor: tokens.warnBorder,
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
  },
  pendingText: { color: tokens.warnMid, fontWeight: '600' },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 12,
    padding: 12,
  },
  rowText: { flex: 1, color: tokens.ink },
  create: {
    alignSelf: 'flex-start',
    borderWidth: 1,
    borderColor: tokens.selBorder,
    backgroundColor: tokens.sel,
    borderRadius: 999,
    paddingVertical: 8,
    paddingHorizontal: 14,
  },
  createText: { color: tokens.selText, fontWeight: '600' },
});
