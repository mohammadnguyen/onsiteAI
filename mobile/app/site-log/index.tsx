import { useCallback, useEffect, useMemo, useState } from 'react';
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
import { useJobs } from '../../src/api/hooks/useJobs';
import { useAuthStore } from '../../src/store/auth';
import { BackLink } from '../../src/siteLog/BackLink';
import { MINE_PAGE_SIZE, listMine } from '../../src/api/siteLog';
import { captureStatusBadgeKey } from '../../src/siteLog/status';
import { useSiteLogDrafts } from '../../src/store/siteLogDrafts';
import { useKeptRecordings } from '../../src/store/keptRecordings';
import { confirmDestructive } from '../../src/siteLog/dialogs';
import {
  fileExists,
  isOwnKeptRecordingPath,
  retainedUri,
} from '../../src/siteLog/files';
import { formatBytes } from '../../src/siteLog/limits';
import { describeMedia } from '../../src/siteLog/summary';
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
  // Job names for the rows. The record carries only the job id; the same
  // list the capture screen's chips use gives the name. Unavailable (no
  // network, not loaded yet) reads as "no job name", never as an error.
  const jobs = useJobs();
  const jobName = useMemo(() => {
    const m = new Map<string, string>();
    jobs.data?.forEach((j) => m.set(j.job_id, j.job_name));
    return m;
  }, [jobs.data]);
  const [keptError, setKeptError] = useState<string | null>(null);
  // The directory is the truth. A preservation interrupted between the
  // copy and the index would otherwise leave a file nothing lists, and an
  // entry whose file has gone would offer a recording that is not there.
  useEffect(() => {
    if (userId) void kept.reconcile(userId);
    // `kept` is the store object and is stable; re-running on every
    // render would read the directory continuously.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);
  // Created ONCE with no source: useAudioPlayer rebuilds and releases the
  // player whenever its source changes, so driving it from state releases
  // the player that is about to play. Same rule as the detail screen.
  const player = useAudioPlayer(null);

  /**
   * Who is doing this, and may they touch this file - decided BEFORE the
   * first await.
   *
   * The previous version read the account AFTER `fileExists`, so a switch
   * landing during that check made the NEW account look like the
   * initiator: the ownership test then passed against the wrong identity
   * and the share sheet opened on the previous worker's recording.
   * Reading identity first is not a detail of style; it is the whole
   * guarantee. Ownership is checked against the path, so an entry from
   * another account cannot be acted on even if it reaches this screen.
   */
  const claim = useCallback((path: string) => {
    const startedAs = useAuthStore.getState().userId;
    const startedUnder = useAuthStore.getState().sessionNonce;
    // Canonical, not a prefix test: `site-log/<me>/oversized/../../
    // <them>/oversized/x.m4a` starts with my prefix and resolves to
    // somebody else's recording. The stored path is persisted state, and
    // persisted state is the thing that can be wrong.
    const owned =
      startedAs !== null && isOwnKeptRecordingPath(path, startedAs);
    return {
      owned,
      stillOurs: () =>
        useAuthStore.getState().userId === startedAs &&
        useAuthStore.getState().sessionNonce === startedUnder,
    };
  }, []);

  const playKept = useCallback(
    async (id: string, path: string) => {
      const { owned, stillOurs } = claim(path);
      setKeptError(null);
      if (!owned) return;
      const uri = retainedUri(path);
      if (uri === null || !(await fileExists(uri))) {
        if (stillOurs()) setKeptError(t('siteLog.kept.missing'));
        return;
      }
      if (!stillOurs()) return;
      try {
        player.replace(uri);
        player.play();
      } catch {
        if (stillOurs()) setKeptError(t('siteLog.kept.play_failed'));
      }
    },
    [claim, player, t],
  );

  const exportKept = useCallback(
    async (path: string, name: string) => {
      const { owned, stillOurs } = claim(path);
      setKeptError(null);
      if (!owned) return;
      const uri = retainedUri(path);
      if (uri === null || !(await fileExists(uri))) {
        if (stillOurs()) setKeptError(t('siteLog.kept.missing'));
        return;
      }
      try {
        if (!(await Sharing.isAvailableAsync())) {
          if (stillOurs()) setKeptError(t('siteLog.kept.export_unavailable'));
          return;
        }
        // Re-checked immediately before the sheet opens: everything above
        // this line is an await the switch could have landed inside.
        if (!stillOurs()) return;
        await Sharing.shareAsync(uri, { mimeType: 'audio/m4a', dialogTitle: name });
      } catch {
        if (stillOurs()) setKeptError(t('siteLog.kept.export_failed'));
      }
    },
    [claim, t],
  );

  const discardKept = useCallback(
    (id: string, name: string) => {
      // The account doing this, read before the dialog: the confirm
      // callback runs later, and the row is only ever this account's.
      //
      // The NONCE as well as the id. A native alert outlives a session:
      // a terminal 401 can clear the tokens, or someone can sign in as
      // themselves again, while the dialog is still on screen. Capturing
      // only the account left the stale confirmation with destructive
      // access to that account's recording, because the ownership check
      // was handed the captured identity and agreed with itself.
      const startedAs = useAuthStore.getState().userId;
      const startedUnder = useAuthStore.getState().sessionNonce;
      if (startedAs === null) return;
      // The ONLY thing that deletes a kept recording, and it asks first.
      confirmDestructive({
        title: t('siteLog.kept.discard_title'),
        body: t('siteLog.kept.discard_body', { name }),
        confirmLabel: t('siteLog.kept.discard'),
        cancelLabel: t('common.cancel'),
        onConfirm: () => {
          // Answered by whoever is signed in NOW, or not acted on.
          const now = useAuthStore.getState();
          if (now.userId !== startedAs || now.sessionNonce !== startedUnder) return;
          void kept.discard(startedAs, id);
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
        renderItem={({ item }) => {
          // What the row says about a record, so two records are never the
          // same "(no text)": the text if there is any, otherwise what was
          // attached ("1 photo · 1 document"); then when, which job, where.
          const media = describeMedia(item.attachments, t);
          const body = item.revision.body_text;
          const job =
            (item.job_id ? jobName.get(item.job_id) : undefined) ??
            (item.job_id ? t('siteLog.detail.assigned') : t('siteLog.list.no_job'));
          const meta = [
            new Date(item.created_at).toLocaleString(),
            job,
            item.revision.internal_location || null,
            // The media line is the title when there is no text; with text
            // it still belongs on the row, as part of the meta.
            body && media ? media : null,
          ]
            .filter((part): part is string => Boolean(part))
            .join(' · ');
          return (
            <Pressable
              style={s.row}
              onPress={() => openRecord(item.site_log_event_id)}
              testID={`record:${item.site_log_event_id}`}
            >
              <View style={s.rowMain}>
                <Text style={s.rowText} numberOfLines={2} testID="record-title">
                  {body || media || t('siteLog.list.no_text')}
                </Text>
                <Text style={s.rowMeta} testID="record-meta">
                  {meta}
                </Text>
              </View>
              <StatusBadge
                status={captureStatusBadgeKey(item.capture_status)}
                label={t(`siteLog.status.${item.capture_status}`)}
              />
            </Pressable>
          );
        }}
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
