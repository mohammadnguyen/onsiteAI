import { useCallback, useState } from 'react';
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
import * as Sharing from 'expo-sharing';
import { useAudioPlayer } from 'expo-audio';

import { useMe } from '../../src/api/hooks/useAuth';
import { useAuthStore } from '../../src/store/auth';
import { BackLink } from '../../src/siteLog/BackLink';
import { MINE_PAGE_SIZE, listMine } from '../../src/api/siteLog';
import { captureStatusBadgeKey } from '../../src/siteLog/status';
import { useSiteLogDrafts } from '../../src/store/siteLogDrafts';
import { useKeptRecordings } from '../../src/store/keptRecordings';
import { confirmDestructive } from '../../src/siteLog/dialogs';
import { fileExists, retainedUri } from '../../src/siteLog/files';
import { formatBytes } from '../../src/siteLog/limits';
import { StatusBadge } from '../../src/ui/kit';
import { tokens } from '../../src/ui/tokens';

export default function MySiteLogRecords() {
  const { t } = useTranslation();
  const { data: me } = useMe();
  // /auth/me needs the network; the token does not. On a cold start with no
  // signal the unsent captures on this phone must still be findable, and
  // they must still be the right account's.
  const tokenUserId = useAuthStore((s) => s.userId);
  const userId = me?.user_id ?? tokenUserId;
  const drafts = useSiteLogDrafts();
  const mine = userId ? drafts.forUser(userId) : [];

  // Recordings refused for size. Kept until the user says otherwise, and
  // reachable from here - see src/store/keptRecordings.ts.
  const kept = useKeptRecordings();
  const keptMine = userId ? kept.forUser(userId) : [];
  const [keptError, setKeptError] = useState<string | null>(null);
  // Created ONCE with no source: useAudioPlayer rebuilds and releases the
  // player whenever its source changes, so driving it from state releases
  // the player that is about to play. Same rule as the detail screen.
  const player = useAudioPlayer(null);

  const playKept = useCallback(
    async (id: string, path: string) => {
      setKeptError(null);
      const uri = retainedUri(path);
      if (uri === null || !(await fileExists(uri))) {
        setKeptError(t('siteLog.kept.missing'));
        return;
      }
      try {
        player.replace(uri);
        player.play();
      } catch {
        setKeptError(t('siteLog.kept.play_failed'));
      }
    },
    [player, t],
  );

  const exportKept = useCallback(
    async (path: string, name: string) => {
      setKeptError(null);
      const uri = retainedUri(path);
      if (uri === null || !(await fileExists(uri))) {
        setKeptError(t('siteLog.kept.missing'));
        return;
      }
      try {
        if (!(await Sharing.isAvailableAsync())) {
          setKeptError(t('siteLog.kept.export_unavailable'));
          return;
        }
        await Sharing.shareAsync(uri, { mimeType: 'audio/m4a', dialogTitle: name });
      } catch {
        setKeptError(t('siteLog.kept.export_failed'));
      }
    },
    [t],
  );

  const discardKept = useCallback(
    (id: string, name: string) => {
      // The ONLY thing that deletes a kept recording, and it asks first.
      confirmDestructive({
        title: t('siteLog.kept.discard_title'),
        body: t('siteLog.kept.discard_body', { name }),
        confirmLabel: t('siteLog.kept.discard'),
        cancelLabel: t('common.cancel'),
        onConfirm: () => {
          void kept.discard(id);
        },
      });
    },
    [kept, t],
  );

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
      <View style={s.backRow}>
        <BackLink fallback="/(tabs)/home" />
      </View>
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
          mine.length > 0 || keptMine.length > 0 ? (
            <View style={s.draftBlock}>
              {keptMine.length > 0 ? (
                <View style={s.keptBlock}>
                  {/* A recording refused for size is kept until the user
                      discards it, and this is where they find it. Without
                      an entry point it is preservation nobody can use. */}
                  <Text style={s.sectionLabel}>{t('siteLog.kept.section')}</Text>
                  {keptMine.map((k) => (
                    <View key={k.id} style={s.kept} testID={`kept:${k.id}`}>
                      <Text style={s.draftText} numberOfLines={1}>
                        {k.name}
                      </Text>
                      <Text style={s.draftHint}>
                        {t('siteLog.kept.hint', { size: formatBytes(k.size) })}
                      </Text>
                      <View style={s.keptActions}>
                        <Pressable
                          onPress={() => playKept(k.id, k.path)}
                          testID={`kept-play:${k.id}`}
                          hitSlop={8}
                        >
                          <Text style={s.keptAction}>{t('siteLog.kept.play')}</Text>
                        </Pressable>
                        <Pressable
                          onPress={() => exportKept(k.path, k.name)}
                          testID={`kept-export:${k.id}`}
                          hitSlop={8}
                        >
                          <Text style={s.keptAction}>{t('siteLog.kept.export')}</Text>
                        </Pressable>
                        <Pressable
                          onPress={() => discardKept(k.id, k.name)}
                          testID={`kept-discard:${k.id}`}
                          hitSlop={8}
                        >
                          <Text style={s.keptDiscard}>{t('siteLog.kept.discard')}</Text>
                        </Pressable>
                      </View>
                    </View>
                  ))}
                  {keptError ? <Text style={s.error}>{keptError}</Text> : null}
                </View>
              ) : null}
              {mine.length > 0 ? (
                <Text style={s.sectionLabel}>{t('siteLog.list.drafts_section')}</Text>
              ) : null}
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
          ) : q.isError ? (
            // "We could not ask" and "you have none" are different facts.
            // Showing the empty text for a failed request tells the user
            // their records are gone.
            <Text style={s.error}>{t('siteLog.list.load_failed')}</Text>
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
  backRow: { paddingHorizontal: 8, paddingTop: 4 },
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
  keptBlock: { paddingBottom: 12 },
  kept: {
    backgroundColor: tokens.surface,
    borderColor: tokens.line,
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    marginBottom: 8,
  },
  keptActions: { flexDirection: 'row', gap: 18, marginTop: 8 },
  keptAction: { color: tokens.primary, fontSize: 14, fontWeight: '600' },
  keptDiscard: { color: '#b91c1c', fontSize: 14, fontWeight: '600' },
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
  error: {
    color: tokens.warnMid,
    textAlign: 'center',
    marginTop: 32,
    paddingHorizontal: 24,
  },
  spinner: { marginVertical: 16 },
});
