import { useCallback } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { useInfiniteQuery } from '@tanstack/react-query';

import { useMe } from '../../src/api/hooks/useAuth';
import { MINE_PAGE_SIZE, listMine } from '../../src/api/siteLog';
import { captureStatusBadgeKey } from '../../src/siteLog/status';
import { useSiteLogDrafts } from '../../src/store/siteLogDrafts';
import { StatusBadge } from '../../src/ui/kit';
import { tokens } from '../../src/ui/tokens';

export default function MySiteLogRecords() {
  const { t } = useTranslation();
  const { data: me } = useMe();
  const drafts = useSiteLogDrafts();
  const mine = me?.user_id ? drafts.forUser(me.user_id) : [];

  const q = useInfiniteQuery({
    queryKey: ['site-log', 'mine'],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => listMine(pageParam as number),
    getNextPageParam: (last, pages) =>
      last.length < MINE_PAGE_SIZE ? undefined : pages.length * MINE_PAGE_SIZE,
  });

  const events = q.data?.pages.flat() ?? [];

  const openRecord = useCallback((id: string) => {
    router.push(`/site-log/${id}` as never);
  }, []);

  return (
    <SafeAreaView style={s.safe} edges={['top']}>
      <View style={s.head}>
        <Text style={s.h1}>{t('siteLog.list.title')}</Text>
        <Pressable
          style={s.newButton}
          onPress={() => router.push('/site-log/new' as never)}
          accessibilityRole="button"
          testID="site-log-new"
        >
          <Text style={s.newButtonText}>{t('siteLog.list.new')}</Text>
        </Pressable>
      </View>

      <FlatList
        data={events}
        keyExtractor={(e) => e.site_log_event_id}
        refreshing={q.isRefetching}
        onRefresh={() => q.refetch()}
        onEndReached={() => {
          if (q.hasNextPage && !q.isFetchingNextPage) q.fetchNextPage();
        }}
        // Inside the list, not above it: unfinished captures accumulate
        // during an outage, and a fixed block of them pushed the saved
        // records - and the older drafts themselves - off the screen with
        // no way to scroll to them.
        ListHeaderComponent={
          mine.length > 0 ? (
            <View style={s.draftBlock}>
              <Text style={s.sectionLabel}>{t('siteLog.list.drafts_section')}</Text>
              {mine.map((d) => (
                <Pressable
                  key={d.capture_client_id}
                  style={s.draft}
                  onPress={() =>
                    router.push(`/site-log/draft/${d.capture_client_id}` as never)
                  }
                >
                  <Text style={s.draftText} numberOfLines={1}>
                    {d.body_text || t('siteLog.list.draft_no_text')}
                  </Text>
                  <Text style={s.draftHint}>
                    {d.unconfirmed
                      ? t('siteLog.status.unconfirmed_short')
                      : d.server
                        ? t('siteLog.list.draft_on_server', {
                            status: t(`siteLog.status.${d.server.capture_status}`),
                          })
                        : t('siteLog.list.draft_not_sent')}
                  </Text>
                </Pressable>
              ))}
            </View>
          ) : null
        }
        ListEmptyComponent={
          q.isLoading ? (
            <ActivityIndicator style={s.spinner} />
          ) : (
            <Text style={s.empty}>{t('siteLog.list.empty')}</Text>
          )
        }
        ListFooterComponent={
          q.isFetchingNextPage ? <ActivityIndicator style={s.spinner} /> : null
        }
        renderItem={({ item }) => (
          <Pressable style={s.row} onPress={() => openRecord(item.site_log_event_id)}>
            <View style={s.rowMain}>
              <Text style={s.rowText} numberOfLines={2}>
                {item.revision.body_text || t('siteLog.list.no_text')}
              </Text>
              <Text style={s.rowMeta}>
                {new Date(item.created_at).toLocaleString()}
                {item.attachments.length > 0
                  ? ` · ${t('siteLog.list.attachment_count', { count: item.attachments.length })}`
                  : ''}
              </Text>
            </View>
            <StatusBadge
              status={captureStatusBadgeKey(item.capture_status)}
              label={t(`siteLog.status.${item.capture_status}`)}
            />
          </Pressable>
        )}
      />
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  head: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 16,
  },
  h1: { fontSize: 22, fontWeight: '700', color: tokens.ink },
  newButton: {
    backgroundColor: tokens.primary,
    paddingVertical: 8,
    paddingHorizontal: 14,
    borderRadius: 999,
  },
  newButtonText: { color: '#ffffff', fontWeight: '600' },
  sectionLabel: { color: tokens.ink3, fontSize: 12, marginBottom: 6 },
  draftBlock: { paddingHorizontal: 16, paddingBottom: 12 },
  draft: {
    backgroundColor: tokens.warnBg,
    borderColor: tokens.warnBorder,
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    marginBottom: 8,
  },
  draftText: { color: tokens.ink },
  draftHint: { color: tokens.warnMid, fontSize: 12, marginTop: 4 },
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
  rowMain: { flex: 1 },
  rowText: { color: tokens.ink },
  rowMeta: { color: tokens.ink3, fontSize: 12, marginTop: 4 },
  empty: { color: tokens.muted, textAlign: 'center', marginTop: 32 },
  spinner: { marginVertical: 16 },
});
