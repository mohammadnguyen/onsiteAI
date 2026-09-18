import { useCallback, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLocalSearchParams } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';
import { useAudioPlayer } from 'expo-audio';

import { api } from '../../src/api/client';
import { BackLink } from '../../src/siteLog/BackLink';
import { getEvent, type AttachmentOut } from '../../src/api/siteLog';
import { captureStatusBadgeKey } from '../../src/siteLog/status';
import { useAuthStore } from '../../src/store/auth';
import { StatusBadge } from '../../src/ui/kit';
import { tokens } from '../../src/ui/tokens';

/** What a downloaded attachment is, once it is on this phone. */
type CachedFile = { uri: string; mime?: string };

/**
 * Extensions for the types this flow produces, plus the common ones a user
 * may attach. A cached file with no extension is handed to other apps as
 * an unrecognisable blob - on Android the share sheet falls back to `*\/*`
 * and nothing offers to open it.
 */
const EXTENSION_BY_MIME: Record<string, string> = {
  'image/jpeg': '.jpg',
  'image/png': '.png',
  'image/heic': '.heic',
  'image/webp': '.webp',
  'image/gif': '.gif',
  'audio/m4a': '.m4a',
  'audio/mp4': '.m4a',
  'audio/x-m4a': '.m4a',
  'audio/mpeg': '.mp3',
  'audio/wav': '.wav',
  'application/pdf': '.pdf',
  'text/plain': '.txt',
  'text/csv': '.csv',
};

function headerValue(
  headers: Record<string, string> | undefined,
  name: string,
): string | undefined {
  if (!headers) return undefined;
  const key = Object.keys(headers).find((k) => k.toLowerCase() === name);
  return key ? headers[key] : undefined;
}

/**
 * Renew the access token, if it can be renewed.
 *
 * expo-file-system's downloadAsync is a separate transport: it never passes
 * through the axios interceptor that refreshes on 401. One cheap
 * authenticated request does pass through it, so this borrows the shared
 * refresh rather than duplicating it - no token handling lives here.
 */
async function refreshSession(): Promise<boolean> {
  try {
    await api.get('/auth/me');
    return true;
  } catch {
    return false;
  }
}

/**
 * Read-only. Opening a saved record grants no right to change it: this screen
 * has no edit affordance, and the backend has no revision writer either.
 */
export default function SiteLogRecordDetail() {
  const { t } = useTranslation();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Created ONCE, with no source. useAudioPlayer rebuilds - and releases -
  // the player whenever the source it is given changes, so driving it from
  // state meant every tap released the player that was about to play.
  const player = useAudioPlayer(null);
  // What this screen has already downloaded. Deliberately per visit: a file
  // cached under a previous sign-in is never reused, and a failed download
  // leaves nothing behind to be served later.
  const cached = useRef(new Map<string, CachedFile>());
  // Opening an attachment downloads it with expo-file-system, which has no
  // web implementation. Offering a button that cannot work is worse than
  // saying where it does work.
  const canOpenAttachments = Platform.OS !== 'web';

  const q = useQuery({
    queryKey: ['site-log', 'event', id],
    queryFn: () => getEvent(String(id)),
    enabled: Boolean(id),
  });

  /**
   * Evidence bytes come from an authenticated stream, so they are downloaded
   * to a cache file first and then handed to the platform. Nothing is written
   * back to the record.
   */
  const fetchToCache = useCallback(async (att: AttachmentOut): Promise<CachedFile | null> => {
    if (!att.evidence_id) return null;
    const hit = cached.current.get(att.evidence_id);
    if (hit) return hit;

    const base = api.defaults.baseURL ?? '';
    const url = `${base}/evidence/${att.evidence_id}/download`;
    // Downloaded to a temporary name first. downloadAsync writes the
    // response body whatever the status, so promoting on existence alone
    // would cache a 401 page and then keep serving it as the attachment.
    const scratch = `${FileSystem.cacheDirectory}sitelog-${att.evidence_id}.part`;
    const attempt = () => {
      // Read at each attempt, never closed over: after a refresh the stored
      // token is a different one.
      const token = useAuthStore.getState().accessToken;
      return FileSystem.downloadAsync(url, scratch, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
    };

    let res;
    try {
      res = await attempt();
      if (res.status === 401 && (await refreshSession())) {
        res = await attempt();
      }
    } catch {
      // A dropped connection REJECTS rather than answering. Left uncaught it
      // escaped the screen's handler, which has only a finally: the spinner
      // cleared and nothing said why.
      await FileSystem.deleteAsync(scratch, { idempotent: true });
      return null;
    }
    if (res.status !== 200) {
      await FileSystem.deleteAsync(scratch, { idempotent: true });
      return null;
    }

    const mime = (headerValue(res.headers, 'content-type') ?? '').split(';')[0].trim();
    const target =
      `${FileSystem.cacheDirectory}sitelog-${att.evidence_id}` +
      (EXTENSION_BY_MIME[mime] ?? '');
    await FileSystem.deleteAsync(target, { idempotent: true });
    await FileSystem.moveAsync({ from: scratch, to: target });

    const entry: CachedFile = { uri: target, mime: mime || undefined };
    cached.current.set(att.evidence_id, entry);
    return entry;
  }, []);

  const open = useCallback(
    async (att: AttachmentOut) => {
      setBusyId(att.attachment_client_id);
      setError(null);
      try {
        const file = await fetchToCache(att);
        if (!file) {
          setError(t('siteLog.error.download'));
          return;
        }
        if (att.declared_media_type === 'audio') {
          // Imperative, on the instance this screen keeps: replace the source,
          // then play it.
          player.replace({ uri: file.uri });
          player.play();
          return;
        }
        if (await Sharing.isAvailableAsync()) {
          // The type goes with the file: without it the receiving app has
          // only the name to go on.
          await Sharing.shareAsync(
            file.uri,
            file.mime ? { mimeType: file.mime } : undefined,
          );
        }
      } catch {
        // Playback and the share sheet can both throw. Whatever went wrong,
        // the user is told rather than left with a button that did nothing.
        setError(t('siteLog.error.download'));
      } finally {
        setBusyId(null);
      }
    },
    [fetchToCache, player, t],
  );

  if (q.isLoading) return <ActivityIndicator style={s.spinner} />;
  if (!q.data) {
    return (
      <SafeAreaView style={s.safe} edges={['top']}>
        <BackLink fallback="/site-log" />
        <Text style={s.empty}>{t('siteLog.detail.not_found')}</Text>
      </SafeAreaView>
    );
  }

  const e = q.data;
  return (
    <SafeAreaView style={s.safe} edges={['top', 'bottom']}>
      <ScrollView contentContainerStyle={s.body}>
        <BackLink fallback="/site-log" />
        <View style={s.headRow}>
          <Text style={s.h1}>{t('siteLog.detail.title')}</Text>
          <StatusBadge
            status={captureStatusBadgeKey(e.capture_status)}
            label={t(`siteLog.status.${e.capture_status}`)}
          />
        </View>
        <Text style={s.meta}>{new Date(e.created_at).toLocaleString()}</Text>
        <Text style={s.meta}>
          {e.job_state === 'unassigned'
            ? t('siteLog.detail.unassigned')
            : t('siteLog.detail.assigned')}
        </Text>

        {e.revision.body_text ? (
          <Text style={s.text}>{e.revision.body_text}</Text>
        ) : (
          <Text style={s.empty}>{t('siteLog.list.no_text')}</Text>
        )}

        {e.attachments.map((a) => (
          <View key={a.attachment_client_id} style={s.att}>
            <View style={s.attMain}>
              <Text style={s.attType}>{t(`siteLog.media.${a.declared_media_type}`)}</Text>
              <Text style={s.attState}>{t(`siteLog.attachment.${a.state}`)}</Text>
            </View>
            {a.state === 'stored' && a.evidence_id && canOpenAttachments ? (
              <Pressable onPress={() => open(a)} disabled={busyId === a.attachment_client_id}>
                <Text style={s.openLink}>
                  {a.declared_media_type === 'audio'
                    ? t('siteLog.detail.play')
                    : a.declared_media_type === 'image'
                      ? t('siteLog.detail.view')
                      : t('siteLog.detail.open')}
                </Text>
              </Pressable>
            ) : null}
          </View>
        ))}

        {!canOpenAttachments && e.attachments.some((a) => a.state === 'stored') ? (
          <Text style={s.readOnly}>{t('siteLog.detail.open_mobile_only')}</Text>
        ) : null}
        {error ? <Text style={s.warnNote}>{error}</Text> : null}
        {e.capture_status === 'partial_failed' ? (
          <Text style={s.warnNote}>{t('siteLog.detail.partial_note')}</Text>
        ) : null}
        <Text style={s.readOnly}>{t('siteLog.detail.read_only')}</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  body: { padding: 16, gap: 10 },
  headRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  h1: { fontSize: 22, fontWeight: '700', color: tokens.ink },
  meta: { color: tokens.ink3, fontSize: 12 },
  text: { color: tokens.ink, fontSize: 16, marginTop: 8 },
  att: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 12,
    padding: 12,
  },
  attMain: { flex: 1 },
  attType: { color: tokens.ink, fontWeight: '600' },
  attState: { color: tokens.ink3, fontSize: 12, marginTop: 2 },
  openLink: { color: tokens.primary, fontWeight: '600' },
  warnNote: { color: tokens.warnMid, marginTop: 8 },
  readOnly: { color: tokens.muted, fontSize: 12, marginTop: 16 },
  empty: { color: tokens.muted, marginTop: 16 },
  spinner: { marginTop: 48 },
});
