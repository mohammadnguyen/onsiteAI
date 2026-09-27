import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Modal,
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
import { setAudioModeAsync, useAudioPlayer, useAudioPlayerStatus } from 'expo-audio';
import { WebView } from 'react-native-webview';

import { api } from '../../src/api/client';
import { BackLink } from '../../src/siteLog/BackLink';
import { getEvent, type AttachmentOut } from '../../src/api/siteLog';
import { captureStatusBadgeKey } from '../../src/siteLog/status';
import { useScreenActive } from '../../src/siteLog/useScreenActive';
import { useAuthStore } from '../../src/store/auth';
import { StatusBadge } from '../../src/ui/kit';
import { tokens } from '../../src/ui/tokens';

/** What a downloaded attachment is, once it is on this phone. */
type CachedFile = { uri: string; mime?: string };

/** What is being shown full-screen, if anything. */
type Viewer =
  | { kind: 'image'; uri: string; title: string }
  | { kind: 'pdf'; uri: string; title: string };

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

/** m:ss for a duration in seconds; "--:--" when not known yet. */
function clock(seconds: number | undefined): string {
  if (seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return '--:--';
  const whole = Math.floor(seconds);
  const m = Math.floor(whole / 60);
  const s = whole % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
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
 *
 * WHAT THIS SCREEN NOW DOES WITH EACH KIND OF EVIDENCE, and why:
 *  - text is the body, shown once. The server keeps a copy of it as an
 *    evidence row; that row is marked `is_inline_text` by the server and is
 *    not listed as if the user had attached a file.
 *  - a photo is shown here, full screen, and can be zoomed.
 *  - a recording plays here, with pause, elapsed and total time, progress,
 *    and a plain message when it cannot be loaded.
 *  - a PDF is read here.
 *  - anything else, and every stored file as a second option, can be
 *    handed to another app through the share sheet - which used to be the
 *    ONLY thing "view" and "open" did.
 */
export default function SiteLogRecordDetail() {
  const { t } = useTranslation();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewer, setViewer] = useState<Viewer | null>(null);
  const [viewerFailed, setViewerFailed] = useState(false);
  const [viewerLoading, setViewerLoading] = useState(false);
  // Which attachment the player currently holds, if any.
  const [audioAttId, setAudioAttId] = useState<string | null>(null);
  // Keyed by attachment, not by "the loaded one": a recording that failed
  // to load is exactly the one that never became the loaded one.
  const [audioFailed, setAudioFailed] = useState<{ id: string; message: string } | null>(null);
  // Created ONCE, with no source. useAudioPlayer rebuilds - and releases -
  // the player whenever the source it is given changes, so driving it from
  // state meant every tap released the player that was about to play.
  const player = useAudioPlayer(null);
  const audio = useAudioPlayerStatus(player);
  // Is this screen the one in front of the user? Read inside async work
  // that outlives the render it started in.
  const activeRef = useScreenActive();
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

  // A signed-out or switched session takes everything this screen is
  // showing with it: the viewer closes, the player stops. The rows below
  // are re-fetched under the new account by the query key anyway.
  const sessionNonce = useAuthStore((st) => st.sessionNonce);
  const sessionOpenedUnder = useRef(sessionNonce);
  useEffect(() => {
    // On a CHANGE, not on mount: the session this screen opened under is
    // the one it belongs to.
    if (sessionNonce === sessionOpenedUnder.current) return;
    sessionOpenedUnder.current = sessionNonce;
    setViewer(null);
    setAudioAttId(null);
    // Files fetched under the old account are not this account's to see.
    cached.current.clear();
    try {
      player.pause();
    } catch {
      // nothing loaded
    }
  }, [player, sessionNonce]);

  // Leaving the screen must not leave a recording playing underneath the
  // next one.
  useEffect(
    () => () => {
      try {
        player.pause();
      } catch {
        // nothing loaded
      }
    },
    [player],
  );

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

  /**
   * Download for one attachment, with the two checks every result must
   * pass before it is acted on: the session that started it is still the
   * session, and this screen is still the one in front of the user. A
   * download outlives both, and acting on it afterwards is how one
   * account's file reaches another account's screen.
   */
  const fetchGuarded = useCallback(
    async (att: AttachmentOut): Promise<CachedFile | null | 'stale'> => {
      const openedUnder = useAuthStore.getState().sessionNonce;
      const file = await fetchToCache(att);
      if (useAuthStore.getState().sessionNonce !== openedUnder) return 'stale';
      if (!activeRef.current) return 'stale';
      return file;
    },
    [activeRef, fetchToCache],
  );

  const mediaLabel = useCallback(
    (a: AttachmentOut) => t(`siteLog.media.${a.declared_media_type}`),
    [t],
  );

  /** Show a photo or a PDF here, in the app. */
  const view = useCallback(
    async (att: AttachmentOut) => {
      setBusyId(att.attachment_client_id);
      setError(null);
      try {
        const file = await fetchGuarded(att);
        if (file === 'stale') return;
        if (!file) {
          setError(t('siteLog.error.download'));
          return;
        }
        const isPdf = file.mime === 'application/pdf' || file.uri.endsWith('.pdf');
        if (att.declared_media_type === 'image') {
          setViewerFailed(false);
          setViewer({ kind: 'image', uri: file.uri, title: mediaLabel(att) });
        } else if (isPdf) {
          setViewerFailed(false);
          setViewerLoading(true);
          setViewer({ kind: 'pdf', uri: file.uri, title: mediaLabel(att) });
        } else {
          // Not something this app renders itself; the share sheet is the
          // honest option and is offered as such.
          setError(t('siteLog.detail.no_viewer'));
        }
      } catch {
        setError(t('siteLog.error.download'));
      } finally {
        setBusyId(null);
      }
    },
    [fetchGuarded, mediaLabel, t],
  );

  /** Hand a stored file to another app. Explicit, never what "view" means. */
  const share = useCallback(
    async (att: AttachmentOut) => {
      setBusyId(att.attachment_client_id);
      setError(null);
      try {
        const file = await fetchGuarded(att);
        if (file === 'stale') return;
        if (!file) {
          setError(t('siteLog.error.download'));
          return;
        }
        if (!(await Sharing.isAvailableAsync())) {
          setError(t('siteLog.detail.share_unavailable'));
          return;
        }
        // Re-checked after the availability await, for the same reason.
        if (!activeRef.current) return;
        // The type goes with the file: without it the receiving app has
        // only the name to go on.
        await Sharing.shareAsync(file.uri, file.mime ? { mimeType: file.mime } : undefined);
      } catch {
        setError(t('siteLog.detail.share_failed'));
      } finally {
        setBusyId(null);
      }
    },
    [activeRef, fetchGuarded, t],
  );

  /**
   * Play, or pause, a recording.
   *
   * The audio mode is set for PLAYBACK first, every time. On iOS the mode
   * is passed straight through - nothing merges it with the previous one -
   * and the only place this app ever set `playsInSilentMode` was while
   * recording. So on a site phone with the ringer switch on silent, a
   * recording that had saved perfectly well played nothing at all. That
   * is the reported "recording will not play", as far as the code can
   * show; the device confirms it.
   */
  const toggleAudio = useCallback(
    async (att: AttachmentOut) => {
      const id = att.attachment_client_id;
      const failed = (message: string) => setAudioFailed({ id, message });
      setAudioFailed(null);
      // Already loaded here: just toggle.
      if (audioAttId === id && audio.isLoaded) {
        try {
          if (audio.playing) player.pause();
          else player.play();
        } catch {
          failed(t('siteLog.detail.audio_failed'));
        }
        return;
      }
      setBusyId(id);
      setError(null);
      try {
        const file = await fetchGuarded(att);
        if (file === 'stale') return;
        if (!file) {
          failed(t('siteLog.error.download'));
          return;
        }
        try {
          await setAudioModeAsync({
            playsInSilentMode: true,
            allowsRecording: false,
            interruptionMode: 'doNotMix',
            shouldPlayInBackground: false,
          });
        } catch {
          // Playback may still work; if it does not, the failure below
          // says so.
        }
        if (!activeRef.current) return;
        // Imperative, on the instance this screen keeps: replace the
        // source, then play it.
        player.replace({ uri: file.uri });
        player.play();
        setAudioAttId(id);
      } catch {
        setAudioAttId(null);
        failed(t('siteLog.detail.audio_failed'));
      } finally {
        setBusyId(null);
      }
    },
    [activeRef, audio.isLoaded, audio.playing, audioAttId, fetchGuarded, player, t],
  );

  // A recording that reaches its end shows as ready to play again, not as
  // stuck at the last second.
  useEffect(() => {
    if (audio.didJustFinish) {
      try {
        player.pause();
        void player.seekTo(0);
      } catch {
        // nothing to reset
      }
    }
  }, [audio.didJustFinish, player]);

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
  // Body text is shown above as the body. The server's own copy of it,
  // marked by the server, is not a file the user attached and is not
  // listed as one. Only an explicit `true` hides a row: an older backend
  // that does not send the flag hides nothing, and a .txt the user really
  // attached is `text` too and stays.
  const files = e.attachments.filter((a) => a.is_inline_text !== true);
  const progress =
    audio.isLoaded && audio.duration > 0
      ? Math.min(1, Math.max(0, audio.currentTime / audio.duration))
      : 0;

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
          <Text style={s.text} testID="detail-body">
            {e.revision.body_text}
          </Text>
        ) : (
          <Text style={s.empty}>{t('siteLog.list.no_text')}</Text>
        )}

        {files.map((a) => {
          const stored = a.state === 'stored' && Boolean(a.evidence_id) && canOpenAttachments;
          const busy = busyId === a.attachment_client_id;
          const isThisAudio = a.declared_media_type === 'audio' && audioAttId === a.attachment_client_id;
          return (
            <View key={a.attachment_client_id} style={s.att} testID="attachment-row">
              <View style={s.attHead}>
                <View style={s.attMain}>
                  <Text style={s.attType}>{mediaLabel(a)}</Text>
                  <Text style={s.attState}>{t(`siteLog.attachment.${a.state}`)}</Text>
                </View>
                {stored ? (
                  <View style={s.attActions}>
                    {a.declared_media_type === 'audio' ? (
                      <Pressable
                        onPress={() => toggleAudio(a)}
                        disabled={busy}
                        testID={`audio-toggle:${a.attachment_client_id}`}
                        hitSlop={8}
                      >
                        <Text style={s.openLink}>
                          {isThisAudio && audio.playing
                            ? t('siteLog.detail.pause')
                            : t('siteLog.detail.play')}
                        </Text>
                      </Pressable>
                    ) : a.declared_media_type === 'image' ||
                      a.declared_media_type === 'document' ? (
                      <Pressable
                        onPress={() => view(a)}
                        disabled={busy}
                        testID={`view:${a.attachment_client_id}`}
                        hitSlop={8}
                      >
                        <Text style={s.openLink}>{t('siteLog.detail.view')}</Text>
                      </Pressable>
                    ) : null}
                    <Pressable
                      onPress={() => share(a)}
                      disabled={busy}
                      testID={`share:${a.attachment_client_id}`}
                      hitSlop={8}
                    >
                      <Text style={s.shareLink}>{t('siteLog.detail.share')}</Text>
                    </Pressable>
                  </View>
                ) : null}
              </View>
              {busy ? <ActivityIndicator style={s.attSpinner} /> : null}
              {isThisAudio ? (
                <View style={s.player} testID="audio-player">
                  <View style={s.track}>
                    <View style={[s.trackFill, { width: `${Math.round(progress * 100)}%` }]} />
                  </View>
                  <Text style={s.clock}>
                    {audio.isLoaded
                      ? `${clock(audio.currentTime)} / ${clock(audio.duration)}`
                      : t('siteLog.detail.audio_loading')}
                  </Text>
                </View>
              ) : null}
              {audioFailed?.id === a.attachment_client_id ? (
                <Text style={s.warnNote} testID="audio-failed">
                  {audioFailed.message}
                </Text>
              ) : null}
            </View>
          );
        })}

        {!canOpenAttachments && files.some((a) => a.state === 'stored') ? (
          <Text style={s.readOnly}>{t('siteLog.detail.open_mobile_only')}</Text>
        ) : null}
        {error ? <Text style={s.warnNote}>{error}</Text> : null}
        {e.capture_status === 'partial_failed' ? (
          <Text style={s.warnNote}>{t('siteLog.detail.partial_note')}</Text>
        ) : null}
        <Text style={s.readOnly}>{t('siteLog.detail.read_only')}</Text>
      </ScrollView>

      {/* Full-screen viewer for a photo or a PDF. One modal, two bodies:
          the close control, the title and the failure text are shared, so
          both kinds behave the same way. */}
      <Modal
        visible={viewer !== null}
        animationType="fade"
        onRequestClose={() => setViewer(null)}
        presentationStyle="fullScreen"
      >
        <SafeAreaView style={s.viewerSafe} edges={['top', 'bottom']}>
          <View style={s.viewerBar}>
            <Text style={s.viewerTitle} numberOfLines={1}>
              {viewer?.title ?? ''}
            </Text>
            <Pressable onPress={() => setViewer(null)} hitSlop={12} testID="viewer-close">
              <Text style={s.viewerClose}>{t('siteLog.detail.close')}</Text>
            </Pressable>
          </View>
          {viewer?.kind === 'image' ? (
            // ScrollView zoom is native on iOS: pinch to zoom, double-tap
            // handled by the platform. No gesture library needed.
            <ScrollView
              style={s.viewerBody}
              contentContainerStyle={s.viewerContent}
              maximumZoomScale={4}
              minimumZoomScale={1}
              centerContent
              testID="viewer-image"
            >
              <Image
                source={{ uri: viewer.uri }}
                style={s.viewerImage}
                resizeMode="contain"
                onError={() => setViewerFailed(true)}
                testID="viewer-image-body"
              />
            </ScrollView>
          ) : null}
          {viewer?.kind === 'pdf' ? (
            // WKWebView renders a local PDF natively on iOS: pages, scroll,
            // pinch zoom. `allowingReadAccessToURL` is what lets it read a
            // file:// under the cache directory. Only the file scheme may
            // load: a link inside a drawing goes nowhere, not to Safari and
            // not to a converter. Nothing leaves the phone - no preview
            // service, no credentials.
            <WebView
              source={{ uri: viewer.uri }}
              originWhitelist={['file://*']}
              onShouldStartLoadWithRequest={(req) => req.url.startsWith('file://')}
              allowingReadAccessToURL={FileSystem.cacheDirectory ?? undefined}
              allowFileAccess
              allowFileAccessFromFileURLs
              style={s.viewerBody}
              onLoadEnd={() => setViewerLoading(false)}
              onError={() => {
                setViewerLoading(false);
                setViewerFailed(true);
              }}
              onHttpError={() => {
                setViewerLoading(false);
                setViewerFailed(true);
              }}
              testID="viewer-pdf"
            />
          ) : null}
          {viewerLoading ? <ActivityIndicator style={s.viewerSpinner} color="#ffffff" /> : null}
          {viewerFailed ? (
            <Text style={s.viewerFailed} testID="viewer-failed">
              {viewer?.kind === 'pdf'
                ? t('siteLog.detail.pdf_failed')
                : t('siteLog.detail.image_failed')}
            </Text>
          ) : null}
        </SafeAreaView>
      </Modal>
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
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 12,
    padding: 12,
  },
  attHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  attMain: { flex: 1 },
  attActions: { flexDirection: 'row', gap: 16 },
  attType: { color: tokens.ink, fontWeight: '600' },
  attState: { color: tokens.ink3, fontSize: 12, marginTop: 2 },
  attSpinner: { marginTop: 8 },
  openLink: { color: tokens.primary, fontWeight: '600' },
  shareLink: { color: tokens.ink3, fontWeight: '600' },
  player: { marginTop: 10, gap: 6 },
  track: { height: 4, borderRadius: 2, backgroundColor: tokens.lineSoft, overflow: 'hidden' },
  trackFill: { height: 4, backgroundColor: tokens.primary },
  clock: { color: tokens.ink3, fontSize: 12 },
  warnNote: { color: tokens.warnMid, marginTop: 8 },
  readOnly: { color: tokens.muted, fontSize: 12, marginTop: 16 },
  empty: { color: tokens.muted, marginTop: 16 },
  spinner: { marginTop: 48 },
  viewerSafe: { flex: 1, backgroundColor: '#000000' },
  viewerBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  viewerTitle: { color: '#ffffff', fontWeight: '600', flex: 1 },
  viewerClose: { color: '#ffffff', fontWeight: '700', fontSize: 16 },
  viewerBody: { flex: 1, backgroundColor: '#000000' },
  viewerContent: { flexGrow: 1, justifyContent: 'center' },
  viewerImage: { width: '100%', height: '100%' },
  viewerSpinner: { position: 'absolute', top: '50%', left: '50%' },
  viewerFailed: { color: '#ffffff', textAlign: 'center', padding: 16 },
});
