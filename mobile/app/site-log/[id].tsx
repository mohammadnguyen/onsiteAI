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
import { isSameLocalFile } from '../../src/siteLog/localDocument';
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

/**
 * How long a recording may sit "Loading…" before it is called failed.
 *
 * The clock is the signal to rely on. expo-audio's iOS player emits a
 * status update when an item becomes ready, not when it fails; a `failed`
 * playbackState is honoured IF it arrives on some other update, but it
 * may never arrive. Android reports no failure state at all.
 */
const AUDIO_LOAD_TIMEOUT_MS = 20_000;

/**
 * Is this status a recording that is really ready to play?
 *
 * `isLoaded` alone is not that. On Android expo-audio derives it from
 * "no longer loading", which is also what a decode FAILURE looks like:
 * ExoPlayer stops loading and sits in `idle`. Reading that as loaded would
 * cancel the failure clock and offer a Play that does nothing.
 */
function isReady(status: { isLoaded: boolean; playbackState: string }): boolean {
  return status.isLoaded && status.playbackState !== 'idle';
}

/**
 * Every mount of this screen is a visit with its own id. The files a visit
 * downloads carry that id, so a download that finishes AFTER the user left
 * and came back can never touch - let alone delete - the file the new
 * visit is showing, and a file left for the share sheet (see `shared`) is
 * never named again by a later visit - not even after the app restarts,
 * which is why this is not a counter.
 */
function newVisitId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
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
  // Per row, not one for the screen: tapping a second row must not make
  // the first row look idle while its download is still running.
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(() => new Set());
  const setBusy = useCallback((attId: string, on: boolean) => {
    setBusyIds((prev) => {
      if (prev.has(attId) === on) return prev;
      const next = new Set(prev);
      if (on) next.add(attId);
      else next.delete(attId);
      return next;
    });
  }, []);
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
  // The status hook keeps the LAST native status until the next event
  // arrives, so right after `replace` it still describes the previous
  // recording - including a previous recording's failure. Each attempt
  // remembers the status object it started with; that object is stale by
  // definition and is never read as this attempt's outcome.
  const latestStatus = useRef(audio);
  latestStatus.current = audio;
  const statusAtStart = useRef(audio);
  // Is this screen the one in front of the user? Read inside async work
  // that outlives the render it started in.
  const activeRef = useScreenActive();
  // This visit's id (see `newVisitId`), fixed at first render.
  const visitId = useRef<string | null>(null);
  if (visitId.current === null) visitId.current = newVisitId();
  // Is this visit still mounted? Read by downloads that outlive it.
  const mounted = useRef(true);
  // What this screen has already downloaded. Deliberately per visit: a file
  // cached under a previous sign-in is never reused, and a failed download
  // leaves nothing behind to be served later.
  const cached = useRef(new Map<string, CachedFile>());
  // Every file this visit promoted, cached or not, so leaving the screen
  // removes them all. A visit's files are its own: nothing else names them.
  const produced = useRef(new Set<string>());
  // Files handed to the share sheet. Those are NOT removed when the screen
  // goes: the sheet - and on Android the app the user picks - reads the
  // file after `shareAsync` returns, and an auth failure can unmount this
  // screen underneath an open sheet. They are left to the OS, which owns
  // the cache directory; nothing in this app names them again.
  const shared = useRef(new Set<string>());
  // Downloads in progress, so a second tap on the same attachment joins the
  // first download instead of starting another one onto the same files.
  // Joined only within the session that started it.
  const inflight = useRef(new Map<string, { nonce: number; promise: Promise<CachedFile | null> }>());
  // Scratch names are unique per attempt: two attempts never share a
  // `.part`, whatever else goes wrong.
  const attempts = useRef(0);
  useEffect(
    () => () => {
      mounted.current = false;
      // Best effort, unawaited: the screen is gone. A download still
      // running sees `mounted` false and removes its own scratch. A file
      // that was handed to the share sheet stays (see `shared`).
      for (const uri of produced.current) {
        if (shared.current.has(uri)) continue;
        void FileSystem.deleteAsync(uri, { idempotent: true }).catch(() => undefined);
      }
      produced.current.clear();
      cached.current.clear();
      inflight.current.clear();
    },
    [],
  );
  // Opening an attachment downloads it with expo-file-system, which has no
  // web implementation. Offering a button that cannot work is worse than
  // saying where it does work.
  const canOpenAttachments = Platform.OS !== 'web';
  // The PDF viewer is WKWebView's own renderer. Android's WebView has no
  // PDF renderer, so there a PDF gets the honest message and Share, not an
  // empty page.
  const canRenderPdf = Platform.OS === 'ios';

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
    // Files fetched under the old account are not this account's to see,
    // and a download the old account started is not one to join.
    cached.current.clear();
    inflight.current.clear();
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
   * One download of one attachment's bytes, to a cache file. Nothing is
   * written back to the record.
   *
   * `openedUnder` is the session this download was started for. A download
   * that finishes after that session has ended is NOT cached: the map was
   * cleared for the new account, and putting the old account's file back
   * into it would hand that file to the next tap.
   */
  const download = useCallback(async (att: AttachmentOut, openedUnder: number): Promise<CachedFile | null> => {
    if (!att.evidence_id) return null;
    const base = api.defaults.baseURL ?? '';
    const url = `${base}/evidence/${att.evidence_id}/download`;
    // Downloaded to a temporary name first. downloadAsync writes the
    // response body whatever the status, so promoting on existence alone
    // would cache a 401 page and then keep serving it as the attachment.
    attempts.current += 1;
    const stem = `${FileSystem.cacheDirectory}sitelog-v${visitId.current}-${att.evidence_id}`;
    const scratch = `${stem}-${attempts.current}.part`;
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
    if (res.status !== 200 || !mounted.current) {
      // Not a file to keep: a bad answer, or a visit that has ended while
      // the bytes were still coming.
      await FileSystem.deleteAsync(scratch, { idempotent: true });
      return null;
    }

    const mime = (headerValue(res.headers, 'content-type') ?? '').split(';')[0].trim();
    const target = stem + (EXTENSION_BY_MIME[mime] ?? '');
    await FileSystem.deleteAsync(target, { idempotent: true });
    await FileSystem.moveAsync({ from: scratch, to: target });
    produced.current.add(target);

    const entry: CachedFile = { uri: target, mime: mime || undefined };
    // Cached only for the session it was fetched for. The file itself is
    // left where it is - the next download of this attachment overwrites
    // it, and it may already BE the new session's own copy.
    if (useAuthStore.getState().sessionNonce === openedUnder) {
      cached.current.set(att.evidence_id, entry);
    }
    return entry;
  }, []);

  /**
   * The cached file for an attachment, downloading it once if needed.
   *
   * A second request for the same attachment while the first download is
   * still running JOINS it - within the same session - rather than
   * starting another download onto the same paths, whose promotion would
   * race the first one's.
   */
  const fetchToCache = useCallback(
    (att: AttachmentOut, openedUnder: number): Promise<CachedFile | null> => {
      if (!att.evidence_id) return Promise.resolve(null);
      const hit = cached.current.get(att.evidence_id);
      if (hit) return Promise.resolve(hit);
      const running = inflight.current.get(att.evidence_id);
      if (running && running.nonce === openedUnder) return running.promise;
      const key = att.evidence_id;
      const promise = download(att, openedUnder).finally(() => {
        if (inflight.current.get(key)?.promise === promise) inflight.current.delete(key);
      });
      inflight.current.set(key, { nonce: openedUnder, promise });
      return promise;
    },
    [download],
  );

  /**
   * The two checks every awaited result must pass before it is acted on:
   * the session that started the action is still the session, and this
   * screen is still the one in front of the user. Applied after EVERY
   * await, not only the download - the session can change during the
   * audio-mode call or the share-availability call just as well, and a
   * result acted on afterwards is how one account's file reaches another
   * account's screen.
   */
  const stillHere = useCallback(
    (openedUnder: number) =>
      useAuthStore.getState().sessionNonce === openedUnder && activeRef.current,
    [activeRef],
  );

  /** Download for one attachment, guarded as above. */
  const fetchGuarded = useCallback(
    async (att: AttachmentOut, openedUnder: number): Promise<CachedFile | null | 'stale'> => {
      const file = await fetchToCache(att, openedUnder);
      return stillHere(openedUnder) ? file : 'stale';
    },
    [fetchToCache, stillHere],
  );

  /** Close the full-screen viewer and forget its loading and failure state,
   *  so the next one - of either kind - starts clean. */
  const closeViewer = useCallback(() => {
    setViewer(null);
    setViewerLoading(false);
    setViewerFailed(false);
  }, []);

  const mediaLabel = useCallback(
    (a: AttachmentOut) => t(`siteLog.media.${a.declared_media_type}`),
    [t],
  );

  /** Show a photo or a PDF here, in the app. */
  const view = useCallback(
    async (att: AttachmentOut) => {
      const openedUnder = useAuthStore.getState().sessionNonce;
      setBusy(att.attachment_client_id, true);
      setError(null);
      try {
        const file = await fetchGuarded(att, openedUnder);
        if (file === 'stale') return;
        if (!file) {
          setError(t('siteLog.error.download'));
          return;
        }
        const isPdf = file.mime === 'application/pdf' || file.uri.endsWith('.pdf');
        if (att.declared_media_type === 'image') {
          setViewerFailed(false);
          setViewerLoading(false);
          setViewer({ kind: 'image', uri: file.uri, title: mediaLabel(att) });
        } else if (isPdf && canRenderPdf) {
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
        setBusy(att.attachment_client_id, false);
      }
    },
    [canRenderPdf, fetchGuarded, mediaLabel, setBusy, t],
  );

  /** Hand a stored file to another app. Explicit, never what "view" means. */
  const share = useCallback(
    async (att: AttachmentOut) => {
      const openedUnder = useAuthStore.getState().sessionNonce;
      setBusy(att.attachment_client_id, true);
      setError(null);
      try {
        const file = await fetchGuarded(att, openedUnder);
        if (file === 'stale') return;
        if (!file) {
          setError(t('siteLog.error.download'));
          return;
        }
        const available = await Sharing.isAvailableAsync();
        // Another await has passed: same two checks again.
        if (!stillHere(openedUnder)) return;
        if (!available) {
          setError(t('siteLog.detail.share_unavailable'));
          return;
        }
        // The type goes with the file: without it the receiving app has
        // only the name to go on. From here the file belongs to the sheet.
        shared.current.add(file.uri);
        await Sharing.shareAsync(file.uri, file.mime ? { mimeType: file.mime } : undefined);
      } catch {
        setError(t('siteLog.detail.share_failed'));
      } finally {
        setBusy(att.attachment_client_id, false);
      }
    },
    [fetchGuarded, setBusy, stillHere, t],
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
      const openedUnder = useAuthStore.getState().sessionNonce;
      const failed = (message: string) => setAudioFailed({ id, message });
      setAudioFailed(null);
      // Already loaded here: just toggle.
      if (audioAttId === id && isReady(audio)) {
        try {
          if (audio.playing) player.pause();
          else player.play();
        } catch {
          failed(t('siteLog.detail.audio_failed'));
        }
        return;
      }
      setBusy(id, true);
      setError(null);
      try {
        const file = await fetchGuarded(att, openedUnder);
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
        // The mode call is an await like any other: the session may have
        // changed underneath it. Focus alone says nothing about the account.
        if (!stillHere(openedUnder)) return;
        // Imperative, on the instance this screen keeps: replace the
        // source, then play it. Whatever status is current right now
        // belongs to the recording before this one.
        statusAtStart.current = latestStatus.current;
        player.replace({ uri: file.uri });
        player.play();
        setAudioAttId(id);
      } catch {
        setAudioAttId(null);
        failed(t('siteLog.detail.audio_failed'));
      } finally {
        setBusy(id, false);
      }
    },
    [audio, audioAttId, fetchGuarded, player, setBusy, stillHere, t],
  );

  // A recording that downloaded fine can still fail in the native player -
  // corrupt, or a codec this phone lacks - AFTER replace/play have returned.
  // Nothing above catches that. A `failed` status ends the attempt when one
  // arrives; a recording that never becomes loaded is failed after a bound
  // whether or not any status arrives, instead of "Loading…" for ever.
  // Either way the row says so and Share stays available.
  useEffect(() => {
    if (!audioAttId) return;
    const giveUp = () => {
      setAudioFailed({ id: audioAttId, message: t('siteLog.detail.audio_failed') });
      setAudioAttId(null);
      try {
        player.pause();
      } catch {
        // nothing loaded
      }
    };
    // A status this attempt started with is the PREVIOUS recording's. Its
    // failure is not this one's, and its "loaded" is not this one's either:
    // until a fresh status arrives, this recording is loading.
    const fresh = audio !== statusAtStart.current;
    if (fresh && audio.playbackState === 'failed') {
      giveUp();
      return;
    }
    // Ready, by a fresh status: the clock stops. "Loaded" in an idle player
    // is Android's failure shape and does not count.
    if (fresh && isReady(audio)) return;
    const timer = setTimeout(giveUp, AUDIO_LOAD_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [audio, audioAttId, player, t]);

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
  const audioReady = isReady(audio);
  const progress =
    audioReady && audio.duration > 0
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
          const busy = busyIds.has(a.attachment_client_id);
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
                    {audioReady
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
        onRequestClose={closeViewer}
        presentationStyle="fullScreen"
      >
        <SafeAreaView style={s.viewerSafe} edges={['top', 'bottom']}>
          <View style={s.viewerBar}>
            <Text style={s.viewerTitle} numberOfLines={1}>
              {viewer?.title ?? ''}
            </Text>
            <Pressable onPress={closeViewer} hitSlop={12} testID="viewer-close">
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
            // pinch zoom. Two fences, both on the ONE opened document:
            //
            //  - `allowingReadAccessToURL` is the document itself, not the
            //    cache directory. WKWebView grants read access to exactly
            //    the file named when it is a file (a directory would grant
            //    its whole tree). Nothing else this app cached is readable
            //    from inside the web view.
            //  - every navigation request is checked against the opened
            //    document by full canonical path (src/siteLog/localDocument):
            //    a TAP on a link inside a drawing - https, another local
            //    file, a same-named file elsewhere - navigates nowhere. Not
            //    to Safari, not to a converter.
            //  - link previews are off. With them on, a LONG-PRESS on a link
            //    is WebKit's own UI, not a navigation: it fetches the remote
            //    page to draw a preview and opens Safari on commit, and none
            //    of that consults the callback.
            //
            // What remains, and is not closable from here: with previews
            // off, a long-press still shows WebKit's link sheet, whose
            // "Open" is policed by the callback but whose Copy / Share /
            // Reading List act on the link's ADDRESS (text) directly. No
            // file, no credential, no navigation leaves the web view.
            //
            // The whitelist is '*' ON PURPOSE. react-native-webview hands
            // any URL that fails the whitelist to Linking.openURL - the
            // system browser - WITHOUT consulting the callback below. So a
            // narrower whitelist is exactly what would open links outside
            // the app. Letting everything reach the callback, and refusing
            // there, is the only configuration that blocks.
            <WebView
              source={{ uri: viewer.uri }}
              originWhitelist={['*']}
              onShouldStartLoadWithRequest={(req) => isSameLocalFile(req.url, viewer.uri)}
              allowingReadAccessToURL={viewer.uri}
              allowsLinkPreview={false}
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
          {viewerLoading ? (
            <ActivityIndicator style={s.viewerSpinner} color="#ffffff" testID="viewer-spinner" />
          ) : null}
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
