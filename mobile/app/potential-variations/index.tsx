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
import { router, useLocalSearchParams } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { useInfiniteQuery } from '@tanstack/react-query';
import axios from 'axios';

import { useJobs } from '../../src/api/hooks/useJobs';
import {
  PV_PAGE_SIZE,
  PV_REVIEW_STATES,
  listPotentialVariations,
  type PvOut,
  type PvReviewState,
} from '../../src/api/potentialVariations';
import { pvStateBadgeKey, visibleTitle } from '../../src/potentialVariations/status';
import { BackLink } from '../../src/siteLog/BackLink';
import { Chip, StatusBadge } from '../../src/ui/kit';
import { tokens } from '../../src/ui/tokens';

type Filter = PvReviewState | 'all';
const FILTERS: readonly Filter[] = [...PV_REVIEW_STATES, 'all'];

function asFilter(value: string | undefined): Filter {
  return FILTERS.includes(value as Filter) ? (value as Filter) : 'to_check';
}

/**
 * Potential variations this user may see (DEC-PV-001): the builder sees
 * every flag, anyone else their own. Opens on To check - the state that
 * asks for attention - and can be narrowed to one job (`?jobId=`), which
 * is how a flag is found again by job. Each row shows the title (or, when
 * the entry is not readable, a neutral placeholder - never entry text), the
 * job, the state, and who flagged it when.
 */
export default function PotentialVariationsList() {
  const { t } = useTranslation();
  const params = useLocalSearchParams<{ jobId?: string; state?: string }>();
  const [state, setState] = useState<Filter>(() => asFilter(params.state));
  const [jobId, setJobId] = useState<string | null>(params.jobId || null);
  const jobs = useJobs();

  const q = useInfiniteQuery({
    queryKey: ['potential-variations', 'list', state, jobId],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      listPotentialVariations({
        review_state: state === 'all' ? undefined : state,
        job_id: jobId ?? undefined,
        offset: pageParam as number,
      }),
    getNextPageParam: (last, pages) =>
      last.length < PV_PAGE_SIZE ? undefined : pages.length * PV_PAGE_SIZE,
  });
  const rows = q.data?.pages.flat() ?? [];
  const unavailable =
    q.isError && axios.isAxiosError(q.error) && q.error.response?.status === 404;
  const jobName = jobId
    ? (jobs.data?.find((j) => j.job_id === jobId)?.job_name ??
      rows.find((r) => r.job?.job_id === jobId)?.job?.job_name ??
      t('potentialVariations.list.this_job'))
    : null;

  return (
    <SafeAreaView style={s.safe} edges={['top']}>
      <View style={s.backRow}>
        <BackLink fallback="/(tabs)/home" />
      </View>
      <Text style={s.h1}>{t('potentialVariations.list.title')}</Text>
      {jobName ? (
        <View style={s.jobRow} testID="pv-list-job">
          <Text style={s.jobText} numberOfLines={1}>
            {t('potentialVariations.list.job_filter', { job: jobName })}
          </Text>
          <Pressable
            onPress={() => setJobId(null)}
            accessibilityRole="button"
            hitSlop={8}
            testID="pv-list-clear-job"
          >
            <Text style={s.link}>{t('potentialVariations.list.clear_job')}</Text>
          </Pressable>
        </View>
      ) : null}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={s.filters}
        style={s.filterBar}
      >
        {FILTERS.map((f) => (
          <Chip
            key={f}
            label={f === 'all' ? t('potentialVariations.list.all') : t(`potentialVariations.state.${f}`)}
            selected={state === f}
            accessibilityRole="radio"
            onPress={() => setState(f)}
            testID={`pv-filter:${f}`}
          />
        ))}
      </ScrollView>

      <FlatList
        data={rows}
        keyExtractor={(pv) => pv.potential_variation_id}
        refreshing={q.isRefetching}
        onRefresh={() => q.refetch()}
        onEndReached={() => {
          if (q.hasNextPage && !q.isFetchingNextPage) q.fetchNextPage();
        }}
        ListEmptyComponent={
          q.isLoading ? (
            <ActivityIndicator style={s.spinner} />
          ) : unavailable ? (
            <Text style={s.note}>{t('potentialVariations.unavailable')}</Text>
          ) : q.isError ? (
            // "Could not ask" is not "there are none".
            <Text style={s.error} testID="pv-list-error">
              {t('potentialVariations.list.load_failed')}
            </Text>
          ) : (
            <Text style={s.note} testID="pv-list-empty">
              {t('potentialVariations.list.empty')}
            </Text>
          )
        }
        ListFooterComponent={q.isFetchingNextPage ? <ActivityIndicator style={s.spinner} /> : null}
        renderItem={({ item }) => <PvRow pv={item} />}
      />
    </SafeAreaView>
  );
}

function PvRow({ pv }: { pv: PvOut }) {
  const { t } = useTranslation();
  return (
    <Pressable
      style={s.row}
      onPress={() => router.push(`/potential-variations/${pv.potential_variation_id}` as never)}
      accessibilityRole="button"
      testID={`pv-row:${pv.potential_variation_id}`}
    >
      <View style={s.rowMain}>
        <Text style={s.rowTitle} numberOfLines={2}>
          {visibleTitle(pv) ?? t('potentialVariations.placeholder_title')}
        </Text>
        <Text style={s.rowMeta} numberOfLines={1}>
          {pv.job?.job_name ?? t('potentialVariations.list.no_job')}
        </Text>
        <Text style={s.rowMeta} numberOfLines={1}>
          {t('potentialVariations.flagged_by', {
            name: pv.created_by?.full_name ?? '—',
            date: new Date(pv.created_at).toLocaleDateString(),
          })}
        </Text>
      </View>
      <StatusBadge
        status={pvStateBadgeKey(pv.review_state)}
        label={t(`potentialVariations.state.${pv.review_state}`)}
      />
    </Pressable>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  backRow: { paddingHorizontal: 8, paddingTop: 4 },
  h1: { fontSize: 22, fontWeight: '700', color: tokens.ink, paddingHorizontal: 16, paddingTop: 8 },
  jobRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 16,
    paddingTop: 6,
  },
  jobText: { flexShrink: 1, color: tokens.ink2, fontWeight: '600' },
  link: { color: tokens.primary, fontWeight: '600' },
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
  note: { color: tokens.muted, textAlign: 'center', marginTop: 32, paddingHorizontal: 24 },
  error: { color: tokens.warnMid, textAlign: 'center', marginTop: 32, paddingHorizontal: 24 },
  spinner: { marginVertical: 16 },
});
