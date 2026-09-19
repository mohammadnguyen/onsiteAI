import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';
import {
  RecordingPresets,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  useAudioRecorder,
} from 'expo-audio';

import { useMe } from '../../src/api/hooks/useAuth';
import { useJobs } from '../../src/api/hooks/useJobs';
import { JobPickerSheet } from '../../src/components/JobPickerSheet';
import { BackLink } from '../../src/siteLog/BackLink';
import { useScreenActive } from '../../src/siteLog/useScreenActive';
import { notify } from '../../src/siteLog/dialogs';
import {
  RetentionError,
  releaseAttachment,
  releaseCapture,
  retainAttachment,
} from '../../src/siteLog/files';
import { newCaptureId as newId } from '../../src/siteLog/ids';
import { deriveMediaType } from '../../src/siteLog/media';
import { currentUri, runSubmit } from '../../src/siteLog/submit';
import { useAuthStore } from '../../src/store/auth';
import { useSiteLogDrafts, type DraftAttachment } from '../../src/store/siteLogDrafts';
import { PrimaryButton } from '../../src/ui/kit';
import { tokens } from '../../src/ui/tokens';

export default function NewSiteLogEntry() {
  const { t } = useTranslation();
  const { data: me } = useMe();
  // Same reason as the list screen: a capture must be startable with no
  // signal, and it must be filed under the right account when it is.
  const tokenUserId = useAuthStore((s) => s.userId);
  const userId = me?.user_id ?? tokenUserId;
  const { data: jobs } = useJobs();
  const drafts = useSiteLogDrafts();
  const qc = useQueryClient();

  const [captureClientId] = useState(newId);
  const [bodyText, setBodyText] = useState('');
  const [jobId, setJobId] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<DraftAttachment[]>([]);
  const [jobPickerOpen, setJobPickerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  // Once a capture has been sent it is no longer editable here: the
  // declaration is pinned, so edits could never reach the server.
  const [submitted, setSubmitted] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);

  // Attachments need the native file APIs: the picker URI is read back with
  // expo-file-system before upload, and sent as a React Native file part.
  // Neither works in a browser, and the flow's own file check would report
  // every attachment as missing - so on web they are not offered at all,
  // and the screen says why instead of failing quietly. Text capture works
  // on every platform.
  const attachmentsSupported = Platform.OS !== 'web';

  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const [recording, setRecording] = useState(false);
  /** True while a start or stop is in progress, awaits included. */
  const [audioBusy, setAudioBusy] = useState(false);
  /**
   * How many picked files are still being copied.
   *
   * A picker returns before the bytes are kept, and a declaration pinned in
   * that window would not include the file - which then cannot be added to
   * it at all, because the declaration is pinned.
   */
  const [retaining, setRetaining] = useState(0);

  // Active jobs only: the backend refuses a completed job at declare time,
  // so offering one would be an invitation to a 422.
  const activeJobs = useMemo(
    () => (jobs ?? []).filter((j) => j.status === 'active'),
    [jobs],
  );
  const jobName = useMemo(
    () => activeJobs.find((j) => j.job_id === jobId)?.job_name ?? null,
    [activeJobs, jobId],
  );

  /**
   * Keep the bytes, then record the attachment.
   *
   * In that order, and never the other way round: an attachment recorded
   * against a cache URI is a draft claiming to hold something it may lose.
   * If the copy fails - no space is the usual reason - the user is told and
   * nothing is added, rather than a draft being quietly left incomplete.
   */
  const add = useCallback(
    async (a: DraftAttachment, sourceUri: string): Promise<void> => {
      if (!userId) return;
      setRetaining((n) => n + 1);
      try {
        const kept = await retainAttachment({
          userId,
          captureClientId,
          attachmentId: a.attachment_client_id,
          sourceUri,
          name: a.name,
          expectedSize: a.size,
        });
        setAttachments((prev) => [
          ...prev,
          {
            ...a,
            uri: kept.uri,
            path: kept.path,
            size: a.size ?? kept.size,
            retained: true,
          },
        ]);
      } catch (err) {
        setBanner(
          err instanceof RetentionError && err.cause === 'unavailable'
            ? t('siteLog.error.attachments_unavailable')
            : t('siteLog.error.attachment_not_kept'),
        );
      } finally {
        setRetaining((n) => n - 1);
      }
    },
    [captureClientId, t, userId],
  );

  const pickPhoto = useCallback(async () => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) {
      setBanner(t('siteLog.error.photo_permission'));
      return;
    }
    const res = await ImagePicker.launchImageLibraryAsync({ quality: 0.8 });
    const asset = res.canceled ? null : res.assets[0];
    if (!asset) return;
    const mime = asset.mimeType ?? 'image/jpeg';
    await add(
      {
        attachment_client_id: newId(),
        media_type: deriveMediaType(mime),
        uri: asset.uri,
        name: asset.fileName ?? 'photo.jpg',
        mime,
        size: asset.fileSize ?? null,
        status: 'awaiting_upload',
      },
      asset.uri,
    );
  }, [add, t]);

  const pickDocument = useCallback(async () => {
    const res = await DocumentPicker.getDocumentAsync({ copyToCacheDirectory: true });
    const asset = res.canceled ? null : res.assets[0];
    if (!asset) return;
    // The picker returns whatever the user chose - a photo, a voice memo, a
    // text file. Declaring all of them `document` made the server refuse the
    // upload, because it derives the class from the bytes' MIME and compares.
    // The declaration is pinned, so that refusal was permanent: the class is
    // derived here the same way instead.
    const mime = asset.mimeType ?? 'application/octet-stream';
    await add(
      {
        attachment_client_id: newId(),
        media_type: deriveMediaType(mime),
        uri: asset.uri,
        name: asset.name,
        mime,
        size: asset.size ?? null,
        status: 'awaiting_upload',
      },
      asset.uri,
    );
  }, [add]);

  const toggleRecording = useCallback(async () => {
    // Held for the whole of this function, including its awaits. Saving is
    // blocked throughout: starting and stopping both have gaps during which
    // `recording` alone said the microphone was idle, and a capture saved in
    // one of those gaps would have pinned a declaration without the voice
    // note - which is then unattachable, because the declaration is pinned.
    setAudioBusy(true);
    try {
      if (!recording) {
        // Permission and the recording audio session both have to be in place
        // first: Android rejects prepareToRecordAsync without the permission,
        // and iOS refuses to start until the mode allows recording.
        try {
          const perm = await requestRecordingPermissionsAsync();
          if (!perm.granted) {
            setBanner(t('siteLog.error.mic_permission'));
            return;
          }
          await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
          await recorder.prepareToRecordAsync();
          recorder.record();
          setRecording(true);
        } catch {
          setBanner(t('siteLog.error.recording'));
          setRecording(false);
        }
        return;
      }
      try {
        await recorder.stop();
      } catch {
        setBanner(t('siteLog.error.recording'));
      }
      // Attached BEFORE `recording` is cleared, so there is no moment in
      // which the button is live and the recording is not yet part of the
      // capture.
      const uri = recorder.uri;
      if (uri) {
        // A recording lives in a temporary file by definition, so this copy
        // is the only thing that makes it survive.
        await add(
          {
            attachment_client_id: newId(),
            media_type: 'audio',
            uri,
            name: `voice-${Date.now()}.m4a`,
            mime: 'audio/m4a',
            size: null,
            status: 'awaiting_upload',
          },
          uri,
        );
      }
      setRecording(false);
      // Hand the session back to playback so a recording can be played here
      // or on the detail screen straight afterwards. Failing to do so must
      // not cost the user the recording that was just made.
      try {
        await setAudioModeAsync({ allowsRecording: false });
      } catch {
        // Playback may be affected until the next session change; the
        // recording itself is on disk and already attached above.
      }
    } finally {
      setAudioBusy(false);
    }
  }, [add, recorder, recording, t]);

  // Read by the unmount cleanup, which must see the CURRENT values rather
  // than the ones captured when the effect first ran.
  const sentRef = useRef(false);
  /**
   * Whether this screen is the one in front of the user.
   *
   * A submission outlives the screen that started it, and the router acts
   * on the CURRENT route. Finishing while the user is on another screen -
   * pushed on top of this one, or opened after leaving it - must not move
   * them or answer them; if that other screen is a second capture, a
   * `router.replace` would take its unsaved text and its files with it.
   */
  const activeRef = useScreenActive();
  const userIdRef = useRef<string | null>(null);
  userIdRef.current = userId ?? null;
  useEffect(() => {
    return () => {
      // Leaving this screen without sending discards the capture - the
      // attachments only ever existed in this screen's state - so the kept
      // copies go with it. A capture that WAS sent owns a draft now, and
      // its files are released only when the server confirms it saved or
      // the user discards it.
      if (sentRef.current) return;
      const userId = userIdRef.current;
      if (userId) void releaseCapture(userId, captureClientId);
    };
  }, [captureClientId]);

  const submit = useCallback(async () => {
    if (!userId) return;
    if (!bodyText.trim() && attachments.length === 0) {
      setBanner(t('siteLog.error.empty'));
      return;
    }
    // Read synchronously, before anything is awaited: this is the session
    // the user tapped Save in, and it is what every later step is checked
    // against.
    const sessionNonce = useAuthStore.getState().sessionNonce;
    setBusy(true);
    setBanner(null);

    // A draft for this capture may already exist, from a previous attempt
    // whose answer was lost. Reuse it: overwriting would discard the pinned
    // declaration and the server id, and a fresh declaration under the same
    // capture id is either ignored or a fingerprint conflict.
    const existing = drafts.get(captureClientId);
    if (!existing && drafts.atCapacity(userId)) {
      // Refused rather than making room: the oldest unfinished capture is
      // somebody's unsent work, and its text and files exist nowhere else.
      setBusy(false);
      setBanner(t('siteLog.error.draft_capacity'));
      return;
    }
    const draft =
      existing ?? {
        capture_client_id: captureClientId,
        user_id: userId,
        created_at: Date.now(),
        updated_at: Date.now(),
        declaration: null,
        body_text: bodyText,
        job_id: jobId,
        attachments,
        server: null,
        unconfirmed: false,
        last_message: null,
      };
    // Written and FLUSHED before the first request: if the process dies now,
    // the typed text, the chosen files and the ids are all still on disk. If
    // the phone cannot write it, nothing is sent - an unsaved capture whose
    // ids exist only in memory is exactly what this flow must never create -
    // and the button is given back rather than left spinning.
    // Set BEFORE the write, not after it: the write is awaited, and leaving
    // the screen during it would otherwise run the abandon-cleanup and
    // delete the very files the submission is about to send.
    sentRef.current = true;
    // Other screens need to know this capture is in flight - the resume
    // screen must not let it be discarded from under this submission.
    drafts.beginSubmit(captureClientId);
    try {
      await drafts.upsertDurable(draft);
    } catch {
      // The in-memory copy was written before the flush failed. Left there,
      // the next Save would reuse it and send the text and files as they
      // were BEFORE the user's subsequent edits - including a file they had
      // since removed. A capture that never reached storage is discarded
      // instead; the form still holds everything.
      if (!existing) drafts.remove(captureClientId);
      // Nothing was sent and no draft survives, so this capture is
      // abandonable again. The kept copies stay: the form still holds the
      // attachments, and the user may simply try again.
      sentRef.current = Boolean(existing);
      drafts.endSubmit(captureClientId);
      setBusy(false);
      setBanner(t('siteLog.error.draft_save_failed'));
      return;
    }
    setSubmitted(true);

    let outcome;
    try {
      outcome = await runSubmit({
        draft,
        userId,
        sessionNonce,
        patch: (p) => drafts.patchDurable(captureClientId, p),
      });
    } catch {
      // A progress write to storage rejected. The draft itself is already
      // on disk, so nothing is lost, but the user must be told rather than
      // left looking at a locked form.
      setBanner(t('siteLog.error.draft_save_failed'));
      return;
    } finally {
      drafts.endSubmit(captureClientId);
      setBusy(false);
    }

    // ---- Bookkeeping: always, whether or not the user is still here ----
    // The list screen stays mounted behind this one, so without this it
    // would still show the state from before this capture existed. And a
    // capture the server confirmed must release its draft and its files
    // even if the user walked away - otherwise it keeps a slot against the
    // per-account limit and keeps files nothing will ever send.
    if (outcome.kind !== 'error') {
      qc.invalidateQueries({ queryKey: ['site-log', 'mine'] });
    }
    if (outcome.kind === 'complete') {
      await drafts.removeAndRelease(captureClientId);
    }

    // ---- Anything the user SEES: only while they are still here --------
    // Re-checked here, after every await above, and against the screen
    // being ACTIVE rather than merely mounted. Also against the session:
    // the answer to one account's submission is not shown to the next.
    if (!activeRef.current) return;
    if (useAuthStore.getState().sessionNonce !== sessionNonce) return;

    if (outcome.kind === 'complete') {
      router.replace(`/site-log/${outcome.event.site_log_event_id}` as never);
      return;
    }
    if (outcome.kind === 'unconfirmed') {
      // The record may exist. Continue in the resume screen, which reads
      // server state first — editing here would diverge from what was sent.
      router.replace(`/site-log/draft/${captureClientId}` as never);
      return;
    }
    if (outcome.kind === 'error') {
      setBanner(outcome.detail ?? t(outcome.messageKey));
      return;
    }
    // Created, but not everything is saved. The record exists — say so, and
    // send the user to it rather than implying nothing happened.
    notify({
      title: t('siteLog.status.created_title'),
      body: [
        outcome.kind === 'partial'
          ? t('siteLog.status.partial_body')
          : t(outcome.bodyKey),
        outcome.limitation ? t(outcome.limitation) : null,
      ]
        .filter(Boolean)
        .join('\n\n'),
      okLabel: t('common.ok'),
      onOk: () => {
        // Checked again HERE: the dialog is dismissed whenever the user
        // gets to it, which may be long after they have moved on.
        if (!activeRef.current) return;
        if (useAuthStore.getState().sessionNonce !== sessionNonce) return;
        router.replace(`/site-log/${outcome.event.site_log_event_id}` as never);
      },
    });
  }, [activeRef, attachments, bodyText, captureClientId, drafts, jobId, qc, t, userId]);

  return (
    <SafeAreaView style={s.safe} edges={['top', 'bottom']}>
      <ScrollView contentContainerStyle={s.body} keyboardShouldPersistTaps="handled">
        <BackLink fallback="/site-log" />
        <Text style={s.h1}>{t('siteLog.new.title')}</Text>

        <TextInput
          style={s.input}
          value={bodyText}
          onChangeText={setBodyText}
          // Not while the first write is in flight either: the submission
          // already took a copy of these values, so an edit made now would
          // be shown but never sent.
          editable={!submitted && !busy}
          placeholder={t('siteLog.new.text_placeholder')}
          multiline
          accessibilityLabel={t('siteLog.new.text_placeholder')}
        />

        <Pressable
          style={s.row}
          onPress={() => setJobPickerOpen(true)}
          disabled={submitted || busy}
        >
          <Text style={s.rowLabel}>{t('siteLog.new.job')}</Text>
          <Text style={s.rowValue}>{jobName ?? t('siteLog.new.job_unassigned')}</Text>
        </Pressable>
        <Text style={s.hint}>{t('siteLog.new.job_hint')}</Text>

        {attachmentsSupported ? (
          <View style={s.actions}>
            <Pressable style={s.action} onPress={pickPhoto} disabled={submitted || busy}>
              <Text style={s.actionText}>{t('siteLog.new.add_photo')}</Text>
            </Pressable>
            <Pressable style={s.action} onPress={pickDocument} disabled={submitted || busy}>
              <Text style={s.actionText}>{t('siteLog.new.add_document')}</Text>
            </Pressable>
            <Pressable
              style={[s.action, recording ? s.actionActive : null]}
              onPress={toggleRecording}
              // `busy` too: the first durable write is awaited with
              // `submitted` still false, and a recording started in that
              // window could never reach the declaration being pinned.
              disabled={submitted || audioBusy || busy}
            >
              <Text style={s.actionText}>
                {recording ? t('siteLog.new.stop_recording') : t('siteLog.new.record_voice')}
              </Text>
            </Pressable>
          </View>
        ) : (
          <Text style={s.hint}>{t('siteLog.new.attachments_mobile_only')}</Text>
        )}

        {attachments.map((a) => (
          <View key={a.attachment_client_id} style={s.att}>
            <Text style={s.attName} numberOfLines={1}>
              {a.name}
            </Text>
            <Pressable
              disabled={submitted || busy}
              onPress={() => {
                setAttachments((prev) =>
                  prev.filter((x) => x.attachment_client_id !== a.attachment_client_id),
                );
                // Nothing references it any more: it was never in a draft.
                void releaseAttachment(currentUri(a));
              }}
            >
              <Text style={s.remove}>{t('common.remove')}</Text>
            </Pressable>
          </View>
        ))}

        {banner ? <Text style={s.banner}>{banner}</Text> : null}
        {busy ? <ActivityIndicator style={s.spinner} /> : null}

        <PrimaryButton
          label={t('siteLog.new.submit')}
          onPress={submit}
          disabled={busy || submitted || recording || audioBusy || retaining > 0}
        />
        {recording || audioBusy ? (
          <Text style={s.hint}>{t('siteLog.new.recording_note')}</Text>
        ) : null}
        {retaining > 0 ? <Text style={s.hint}>{t('siteLog.new.keeping_files')}</Text> : null}
        {submitted ? <Text style={s.hint}>{t('siteLog.new.sent_hint')}</Text> : null}
      </ScrollView>

      <JobPickerSheet
        visible={jobPickerOpen}
        jobs={activeJobs}
        selectedJobId={jobId}
        labelFor={(j) => j.job_name}
        onSelect={(id) => {
          setJobId(id);
          setJobPickerOpen(false);
        }}
        onClose={() => setJobPickerOpen(false)}
      />
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  body: { padding: 16, gap: 12 },
  h1: { fontSize: 22, fontWeight: '700', color: tokens.ink },
  input: {
    minHeight: 120,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 12,
    padding: 12,
    color: tokens.ink,
    textAlignVertical: 'top',
  },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: tokens.line,
  },
  rowLabel: { color: tokens.ink, fontWeight: '600' },
  rowValue: { color: tokens.muted },
  hint: { color: tokens.muted, fontSize: 12 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  action: {
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: tokens.line,
  },
  actionActive: { borderColor: tokens.ink },
  actionText: { color: tokens.ink },
  att: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 8,
  },
  attName: { flex: 1, color: tokens.ink, marginRight: 12 },
  remove: { color: tokens.muted },
  banner: { color: tokens.ink, backgroundColor: tokens.warnBg, padding: 10, borderRadius: 8 },
  spinner: { marginVertical: 8 },
});
