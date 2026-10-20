import { useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { useInfiniteQuery } from '@tanstack/react-query';
import axios from 'axios';

import { TASK_PAGE_SIZE, TASK_STATUSES, listTasks, type TaskOut, type TaskStatus } from '../../src/api/tasks';
import { BackLink } from '../../src/siteLog/BackLink';
import { isOverdue, taskStatusBadgeKey } from '../../src/tasks/status';
import { formatDateAU } from '../../src/util/dates';
import { Chip, StatusBadge } from '../../src/ui/kit';
import { tokens } from '../../src/ui/tokens';

type Filter = TaskStatus | 'all';

/**
 * Every task this user may see (plan v2 §6.3): filter by status, or narrow
 * to the ones assigned to me. Each row shows title, status, assignee, due
 * date (overdue in amber) and job, and opens the task.
 */
export default function TasksList() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<Filter>('all');
  const [mine, setMine] = useState(false);

  const q = useInfiniteQuery({
    queryKey: ['tasks', 'list', status, mine],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      listTasks({
        status: status === 'all' ? undefined : status,
        mine: mine ? 'assigned' : undefined,
        offset: pageParam as number,
      }),
    getNextPageParam: (last, pages) =>
      last.length < TASK_PAGE_SIZE ? undefined : pages.length * TASK_PAGE_SIZE,
  });
  const tasks = q.data?.pages.flat() ?? [];
  const unavailable =
    q.isError && axios.isAxiosError(q.error) && q.error.response?.status === 404;

  const filters: { value: Filter; label: string }[] = [
    { value: 'all', label: t('tasks.list.all') },
    ...TASK_STATUSES.map((v) => ({ value: v, label: t(`tasks.status.${v}`) })),
  ];

  return (
    <SafeAreaView style={s.safe} edges={['top']}>
      <View style={s.backRow}>
        <BackLink fallback="/(tabs)/home" />
      </View>
      <Text style={s.h1}>{t('tasks.list.title')}</Text>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={s.filters}
        style={s.filterBar}
      >
        {filters.map((f) => (
          <Chip
            key={f.value}
            label={f.label}
            selected={status === f.value}
            accessibilityRole="radio"
            onPress={() => setStatus(f.value)}
            testID={`tasks-filter:${f.value}`}
          />
        ))}
        <Chip
          label={t('tasks.list.mine')}
          selected={mine}
          onPress={() => setMine((m) => !m)}
          testID="tasks-filter-mine"
        />
      </ScrollView>

      <FlatList
        data={tasks}
        keyExtractor={(task) => task.task_id}
        refreshing={q.isRefetching}
        onRefresh={() => q.refetch()}
        onEndReached={() => {
          if (q.hasNextPage && !q.isFetchingNextPage) q.fetchNextPage();
        }}
        ListEmptyComponent={
          q.isLoading ? (
            <ActivityIndicator style={s.spinner} />
          ) : unavailable ? (
            <Text style={s.note}>{t('tasks.unavailable')}</Text>
          ) : q.isError ? (
            // "Could not ask" is not "there are none".
            <Text style={s.error}>{t('tasks.list.load_failed')}</Text>
          ) : (
            <Text style={s.note}>{t('tasks.list.empty')}</Text>
          )
        }
        ListFooterComponent={
          q.isFetchingNextPage ? <ActivityIndicator style={s.spinner} /> : null
        }
        renderItem={({ item }) => <TaskRow task={item} />}
      />
    </SafeAreaView>
  );
}

function TaskRow({ task }: { task: TaskOut }) {
  const { t } = useTranslation();
  const overdue = isOverdue(task);
  const due = task.due_date
    ? overdue
      ? t('tasks.list.overdue', { date: formatDateAU(task.due_date) })
      : t('tasks.list.due', { date: formatDateAU(task.due_date) })
    : null;
  const meta = [
    task.assignee?.full_name ?? t('tasks.assignee.unassigned'),
    task.job?.job_name ?? t('tasks.list.no_job'),
  ].join(' · ');
  return (
    <Pressable
      style={s.row}
      onPress={() => router.push(`/tasks/${task.task_id}` as never)}
      accessibilityRole="button"
      testID={`task-row:${task.task_id}`}
    >
      <View style={s.rowMain}>
        <Text style={s.rowTitle} numberOfLines={2}>
          {task.title}
        </Text>
        <Text style={s.rowMeta} numberOfLines={1}>
          {meta}
        </Text>
        {due ? <Text style={overdue ? s.overdue : s.rowMeta}>{due}</Text> : null}
      </View>
      <StatusBadge
        status={taskStatusBadgeKey(task.status)}
        label={t(`tasks.status.${task.status}`)}
      />
    </Pressable>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  backRow: { paddingHorizontal: 8, paddingTop: 4 },
  h1: { fontSize: 22, fontWeight: '700', color: tokens.ink, paddingHorizontal: 16, paddingTop: 8 },
  filterBar: { flexGrow: 0 },
  filters: { gap: 8, paddingHorizontal: 16, paddingVertical: 12 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: tokens.surface,
    marginHorizontal: 16,
    marginBottom: 8,
    padding: 12,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: tokens.line,
  },
  rowMain: { flex: 1, gap: 3 },
  rowTitle: { color: tokens.ink, fontWeight: '600' },
  rowMeta: { color: tokens.ink3, fontSize: 12 },
  overdue: { color: tokens.warnMid, fontSize: 12, fontWeight: '600' },
  note: { color: tokens.muted, textAlign: 'center', marginTop: 32, paddingHorizontal: 24 },
  error: { color: tokens.warnMid, textAlign: 'center', marginTop: 32, paddingHorizontal: 24 },
  spinner: { marginVertical: 16 },
});
