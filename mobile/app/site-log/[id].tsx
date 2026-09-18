import { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  Image,
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
import { getEvent, type AttachmentOut } from '../../src/api/siteLog';
import { captureStatusBadgeKey } from '../../src/siteLog/status';
import { useAuthStore } from '../../src/store/auth';
import { StatusBadge } from '../../src/ui/kit';
import { tokens } from '../../src/ui/tokens';

/**
 * Read-only. Opening a saved record grants no right to change it: this screen
 * has no edit affordance, and the backend has no revision writer either.
 */
export default function SiteLogRecordDetail() {
  const { t } = useTranslation();
  const { id } = useLocalSearchParams<{ id: string }>();
  const token = useAuthStore((s) => s.accessToken);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [playingUri, setPlayingUri] = useState<string | null>(null);
  const player = useAudioPlayer(playingUri ? { uri: playingUri } : null);

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
  const fetchToCache = useCallback(
    async (att: AttachmentOut): Promise<string | null> => {
      if (!att.evidence_id) return null;
      const target = `${FileSystem.cacheDirectory}sitelog-${att.evidence_id}`;
      const existing = await FileSystem.getInfoAsync(target);
      if (existing.exists) return target;
      const base = api.defaults.baseURL ?? '';
      const res = await FileSystem.downloadAsync(
        `${base}/evidence/${att.evidence_id}/download`,
        target,
        { headers: token ? { Authorization: `Bearer ${token}` } : undefined },
      );
      return res.status === 200 ? res.uri : null;
    },
    [token],
  );

  const open = useCallback(
    async (att: AttachmentOut) => {
      setBusyId(att.attachment_client_id);
      try {
        const uri = await fetchToCache(att);
        if (!uri) return;
        if (att.declared_media_type === 'audio') {
          setPlayingUri(uri);
          player.play();
          return;
        }
        if (await Sharing.isAvailableAsync()) await Sharing.shareAsync(uri);
      } finally {
        setBusyId(null);
      }
    },
    [fetchToCache, player],
  );

  if (q.isLoading) return <ActivityIndicator style={s.spinner} />;
  if (!q.data) return <Text style={s.empty}>{t('siteLog.detail.not_found')}</Text>;

  const e = q.data;
  return (
    <SafeAreaView style={s.safe} edges={['top', 'bottom']}>
      <ScrollView contentContainerStyle={s.body}>
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
            {a.state === 'stored' && a.evidence_id ? (
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
