import { useMemo, useState } from 'react';
import {
  View,
  Text,
  FlatList,
  ActivityIndicator,
  StyleSheet,
  TouchableOpacity,
  Modal,
  ScrollView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import { useJob, useJobs, type JobPublic } from '../../src/api/hooks/useJobs';

export default function JobsScreen() {
  const { t } = useTranslation();
  const { data, isLoading, isError } = useJobs();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const jobs = useMemo(() => data ?? [], [data]);

  return (
    <SafeAreaView style={s.safe} edges={['bottom', 'left', 'right']}>
      <View style={s.header}>
        <Text style={s.title}>{t('jobs.title')}</Text>
      </View>
      {isLoading ? (
        <View style={s.center}>
          <ActivityIndicator size="large" color="#1e293b" />
          <Text style={s.loadingText}>{t('jobs.loading')}</Text>
        </View>
      ) : isError ? (
        <View style={s.center}>
          <Text style={s.errText}>{t('common.error')}</Text>
        </View>
      ) : jobs.length === 0 ? (
        <View style={s.center}>
          <Text style={s.emptyText}>{t('jobs.empty')}</Text>
        </View>
      ) : (
        <FlatList
          data={jobs}
          keyExtractor={(item) => item.job_id}
          renderItem={({ item }) => (
            <JobRow job={item} onPress={() => setSelectedId(item.job_id)} />
          )}
          ItemSeparatorComponent={() => <View style={s.sep} />}
          contentContainerStyle={s.listContent}
        />
      )}
      <JobDetailModal
        jobId={selectedId}
        onClose={() => setSelectedId(null)}
      />
    </SafeAreaView>
  );
}

function JobRow({ job, onPress }: { job: JobPublic; onPress: () => void }) {
  return (
    <TouchableOpacity onPress={onPress} style={s.row} testID={`job-row-${job.job_id}`}>
      <View style={s.rowMain}>
        <Text style={s.rowName}>{job.job_name}</Text>
        {job.job_code ? <Text style={s.rowCode}>{job.job_code}</Text> : null}
      </View>
      <Text style={[s.badge, job.status === 'active' ? s.badgeActive : s.badgeCompleted]}>
        {job.status}
      </Text>
    </TouchableOpacity>
  );
}

function JobDetailModal({
  jobId,
  onClose,
}: {
  jobId: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { data, isLoading, isError } = useJob(jobId);
  return (
    <Modal visible={!!jobId} animationType="slide" onRequestClose={onClose} transparent={false}>
      <SafeAreaView style={s.safe}>
        <View style={s.modalHeader}>
          <TouchableOpacity onPress={onClose} accessibilityRole="button">
            <Text style={s.closeBtn}>{'\u00d7'}</Text>
          </TouchableOpacity>
        </View>
        {isLoading ? (
          <View style={s.center}>
            <ActivityIndicator color="#1e293b" />
          </View>
        ) : isError || !data ? (
          <View style={s.center}>
            <Text style={s.errText}>{t('common.error')}</Text>
          </View>
        ) : (
          <ScrollView contentContainerStyle={s.detailWrap}>
            <Text style={s.detailTitle}>{data.job_name}</Text>
            <DetailRow label={t('job.code')} value={data.job_code ?? '-'} />
            <DetailRow label={t('job.status')} value={data.status} />
            <DetailRow
              label={t('job.contract')}
              value={data.contract_value_ex_gst ?? '-'}
            />
            <DetailRow
              label={t('job.budget')}
              value={data.total_budget_ex_gst ?? '-'}
            />
            <DetailRow label={t('job.address')} value={data.site_address ?? '-'} />
            <Text style={s.sectionHeader}>{t('job.aliases')}</Text>
            {data.aliases.length === 0 ? (
              <Text style={s.muted}>-</Text>
            ) : (
              data.aliases.map((a) => (
                <Text key={a.alias_id} style={s.aliasRow}>
                  {a.alias_text}
                </Text>
              ))
            )}
            <Text style={s.sectionHeader}>{t('job.budgets')}</Text>
            {data.category_budgets.length === 0 ? (
              <Text style={s.muted}>-</Text>
            ) : (
              data.category_budgets.map((b) => (
                <View key={b.budget_id} style={s.budgetRow}>
                  <Text style={s.budgetName}>{b.category.category_name}</Text>
                  <Text style={s.budgetAmount}>{b.budget_amount_ex_gst}</Text>
                </View>
              ))
            )}
          </ScrollView>
        )}
      </SafeAreaView>
    </Modal>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={s.detailRow}>
      <Text style={s.detailLabel}>{label}</Text>
      <Text style={s.detailValue}>{value}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#ffffff' },
  header: { paddingHorizontal: 16, paddingTop: 16, paddingBottom: 8 },
  title: { fontSize: 22, fontWeight: '600', color: '#0f172a' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  loadingText: { marginTop: 12, color: '#64748b' },
  emptyText: { color: '#64748b', fontSize: 16 },
  errText: { color: '#b91c1c', fontSize: 16 },
  listContent: { paddingBottom: 24 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 14,
    paddingHorizontal: 16,
    backgroundColor: '#ffffff',
  },
  rowMain: { flex: 1 },
  rowName: { fontSize: 16, color: '#0f172a', fontWeight: '500' },
  rowCode: { fontSize: 13, color: '#64748b', marginTop: 2 },
  badge: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
    overflow: 'hidden',
    fontSize: 12,
    fontWeight: '600',
  },
  badgeActive: { backgroundColor: '#dcfce7', color: '#15803d' },
  badgeCompleted: { backgroundColor: '#e2e8f0', color: '#475569' },
  sep: { height: 1, backgroundColor: '#e2e8f0' },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
  },
  closeBtn: { fontSize: 28, color: '#0f172a', lineHeight: 28 },
  detailWrap: { padding: 16 },
  detailTitle: { fontSize: 22, fontWeight: '600', marginBottom: 16, color: '#0f172a' },
  detailRow: { flexDirection: 'row', paddingVertical: 6 },
  detailLabel: { flex: 1, color: '#64748b' },
  detailValue: { flex: 2, color: '#0f172a' },
  sectionHeader: { fontSize: 15, fontWeight: '600', marginTop: 20, marginBottom: 8, color: '#0f172a' },
  muted: { color: '#94a3b8' },
  aliasRow: { paddingVertical: 4, color: '#0f172a' },
  budgetRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 4 },
  budgetName: { color: '#0f172a' },
  budgetAmount: { color: '#0f172a', fontVariant: ['tabular-nums'] },
});
