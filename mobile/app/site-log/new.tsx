import { useCallback, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
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
import { newCaptureId as newId } from '../../src/siteLog/ids';
import { deriveMediaType } from '../../src/siteLog/media';
import { runSubmit } from '../../src/siteLog/submit';
import { useSiteLogDrafts, type DraftAttachment } from '../../src/store/siteLogDrafts';
import { PrimaryButton } from '../../src/ui/kit';
import { tokens } from '../../src/ui/tokens';

export default function NewSiteLogEntry() {
  const { t } = useTranslation();
  const { data: me } = useMe();
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

  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const [recording, setRecording] = useState(false);

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

  const add = useCallback((a: DraftAttachment) => {
    setAttachments((prev) => [...prev, a]);
  }, []);

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
    add({
      attachment_client_id: newId(),
      media_type: deriveMediaType(mime),
      uri: asset.uri,
      name: asset.fileName ?? 'photo.jpg',
      mime,
      size: asset.fileSize ?? null,
      status: 'awaiting_upload',
    });
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
    add({
      attachment_client_id: newId(),
      media_type: deriveMediaType(mime),
      uri: asset.uri,
      name: asset.name,
      mime,
      size: asset.size ?? null,
      status: 'awaiting_upload',
    });
  }, [add]);

  const toggleRecording = useCallback(async () => {
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
    setRecording(false);
    // Hand the session back to playback so a recording can be played here
    // or on the detail screen straight afterwards. Failing to do so must not
    // cost the user the recording that was just made.
    try {
      await setAudioModeAsync({ allowsRecording: false });
    } catch {
      // Playback may be affected until the next session change; the recording
      // itself is on disk and is still attached below.
    }
    const uri = recorder.uri;
    if (!uri) return;
    add({
      attachment_client_id: newId(),
      media_type: 'audio',
      uri,
      name: `voice-${Date.now()}.m4a`,
      mime: 'audio/m4a',
      size: null,
      status: 'awaiting_upload',
    });
  }, [add, recorder, recording, t]);

  const submit = useCallback(async () => {
    if (!me?.user_id) return;
    if (!bodyText.trim() && attachments.length === 0) {
      setBanner(t('siteLog.error.empty'));
      return;
    }
    setBusy(true);
    setBanner(null);

    // A draft for this capture may already exist, from a previous attempt
    // whose answer was lost. Reuse it: overwriting would discard the pinned
    // declaration and the server id, and a fresh declaration under the same
    // capture id is either ignored or a fingerprint conflict.
    const existing = drafts.get(captureClientId);
    const draft =
      existing ?? {
        capture_client_id: captureClientId,
        user_id: me.user_id,
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
    try {
      await drafts.upsertDurable(draft);
    } catch {
      setBusy(false);
      setBanner(t('siteLog.error.draft_save_failed'));
      return;
    }
    setSubmitted(true);

    let outcome;
    try {
      outcome = await runSubmit({
        draft,
        userId: me.user_id,
        patch: (p) => drafts.patchDurable(captureClientId, p),
      });
    } finally {
      setBusy(false);
    }

    // The list screen stays mounted behind this one, so without this it
    // would still show the state from before this capture existed.
    if (outcome.kind !== 'error') {
      qc.invalidateQueries({ queryKey: ['site-log', 'mine'] });
    }

    if (outcome.kind === 'complete') {
      drafts.remove(captureClientId);
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
    Alert.alert(
      t('siteLog.status.created_title'),
      [
        outcome.kind === 'partial'
          ? t('siteLog.status.partial_body')
          : t('siteLog.status.blocked_body'),
        outcome.limitation ? t(outcome.limitation) : null,
      ]
        .filter(Boolean)
        .join('\n\n'),
      [
        {
          text: t('common.ok'),
          onPress: () =>
            router.replace(`/site-log/${outcome.event.site_log_event_id}` as never),
        },
      ],
    );
  }, [attachments, bodyText, captureClientId, drafts, jobId, me?.user_id, qc, t]);

  return (
    <SafeAreaView style={s.safe} edges={['top', 'bottom']}>
      <ScrollView contentContainerStyle={s.body} keyboardShouldPersistTaps="handled">
        <Text style={s.h1}>{t('siteLog.new.title')}</Text>

        <TextInput
          style={s.input}
          value={bodyText}
          onChangeText={setBodyText}
          editable={!submitted}
          placeholder={t('siteLog.new.text_placeholder')}
          multiline
          accessibilityLabel={t('siteLog.new.text_placeholder')}
        />

        <Pressable
          style={s.row}
          onPress={() => setJobPickerOpen(true)}
          disabled={submitted}
        >
          <Text style={s.rowLabel}>{t('siteLog.new.job')}</Text>
          <Text style={s.rowValue}>{jobName ?? t('siteLog.new.job_unassigned')}</Text>
        </Pressable>
        <Text style={s.hint}>{t('siteLog.new.job_hint')}</Text>

        <View style={s.actions}>
          <Pressable style={s.action} onPress={pickPhoto} disabled={submitted}>
            <Text style={s.actionText}>{t('siteLog.new.add_photo')}</Text>
          </Pressable>
          <Pressable style={s.action} onPress={pickDocument} disabled={submitted}>
            <Text style={s.actionText}>{t('siteLog.new.add_document')}</Text>
          </Pressable>
          <Pressable
            style={[s.action, recording ? s.actionActive : null]}
            onPress={toggleRecording}
            disabled={submitted}
          >
            <Text style={s.actionText}>
              {recording ? t('siteLog.new.stop_recording') : t('siteLog.new.record_voice')}
            </Text>
          </Pressable>
        </View>

        {attachments.map((a) => (
          <View key={a.attachment_client_id} style={s.att}>
            <Text style={s.attName} numberOfLines={1}>
              {a.name}
            </Text>
            <Pressable
              disabled={submitted}
              onPress={() =>
                setAttachments((prev) =>
                  prev.filter((x) => x.attachment_client_id !== a.attachment_client_id),
                )
              }
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
          disabled={busy || submitted || recording}
        />
        {recording ? <Text style={s.hint}>{t('siteLog.new.recording_note')}</Text> : null}
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
