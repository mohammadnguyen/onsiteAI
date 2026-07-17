import { useMemo } from 'react';
import { View, Text, Pressable, StyleSheet } from 'react-native';
import { useTranslation } from 'react-i18next';
import { useRouter, type Href } from 'expo-router';

import { useJobs } from '../../api/hooks/useJobs';
import { useLabourEntriesRange } from '../../api/hooks/useLabour';
import { todayISO } from '../../util/dates';
import { formatDays } from '../../util/format';
import { useScaledStyles } from '../../ui/type';
import { tokens } from '../../ui/tokens';

/**
 * forey F1 (handoff §2 今日出勤): did each active job get its
 * attendance logged today?
 *
 * Money-free by construction — worker counts and day fractions only,
 * no rates, no cost — so this renders for contributors too. One
 * range query for today across ALL jobs (job_id omitted), grouped
 * client-side; the per-job/day tick screen stays the Labour tab's job.
 *
 * A row taps through to the Labour tab. It deliberately does NOT
 * preselect the job: the labour screen owns that selection (and its
 * archived-job repair), and a second source of truth for "which job
 * is selected" is exactly what B4 retired.
 */
export function TodayAttendance() {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const router = useRouter();
  const today = todayISO();
  const jobs = useJobs();
  const entries = useLabourEntriesRange(null, today, today);

  const rows = useMemo(() => {
    const active = (jobs.data ?? []).filter((j) => j.status === 'active');
    const byJob = new Map<string, { workers: Set<string>; days: number }>();
    (entries.data ?? []).forEach((e) => {
      const cur = byJob.get(e.job_id) ?? { workers: new Set<string>(), days: 0 };
      cur.workers.add(e.worker_id);
      cur.days += parseFloat(e.day_fraction ?? '0');
      byJob.set(e.job_id, cur);
    });
    // Logged jobs first — the ones still needing a record are what the
    // operator acts on, but they read better under the done ones.
    return active
      .map((j) => ({ job: j, stat: byJob.get(j.job_id) ?? null }))
      .sort((a, b) => (a.stat ? 0 : 1) - (b.stat ? 0 : 1));
  }, [jobs.data, entries.data]);

  // A grey dot is an ASSERTION ("nobody was logged on this job today").
  // Making it on the basis of `entries.data ?? []` meant every job read
  // as un-logged while the query was still in flight, and permanently
  // after a failure. Say nothing until the data is real; say so plainly
  // when it won't load.
  if (rows.length === 0) return null;
  if (entries.isError && !entries.data) {
    return (
      <View testID="home-today-attendance-error">
        <Text style={s.sectionTitle}>{t('home.today_attendance')}</Text>
        <View style={s.card}>
          <Text style={s.errorText}>{t('home.attendance_error')}</Text>
        </View>
      </View>
    );
  }
  if (entries.data === undefined) return null;

  return (
    <View testID="home-today-attendance">
      <Text style={s.sectionTitle}>{t('home.today_attendance')}</Text>
      <View style={s.card}>
        {rows.map((r, i) => (
          <Pressable
            key={r.job.job_id}
            onPress={() => router.navigate('/(tabs)/labour' as unknown as Href)}
            accessibilityRole="button"
            style={({ pressed }) => [
              s.row,
              i > 0 && s.rowBorder,
              pressed && s.rowPressed,
            ]}
            testID={`home-attendance-${r.job.job_id}`}
          >
            <View style={[s.dot, r.stat ? s.dotOk : s.dotNone]} />
            <View style={s.main}>
              <Text style={s.jobName} numberOfLines={1}>
                {r.job.job_name}
              </Text>
              <Text style={s.sub} numberOfLines={1}>
                {r.stat
                  ? t('home.workers_days', {
                      count: r.stat.workers.size,
                      days: formatDays(String(r.stat.days)),
                    })
                  : t('home.not_logged')}
              </Text>
            </View>
            <Text style={[s.tag, r.stat ? s.tagOk : s.tagNone]}>
              {r.stat ? t('labour.saved') : t('home.add_record')}
            </Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

const base = StyleSheet.create({
  sectionTitle: {
    fontSize: 16,
    fontWeight: '800',
    color: tokens.ink,
    marginBottom: 8,
  },
  card: {
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 16,
    overflow: 'hidden',
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    minHeight: 44,
  },
  rowBorder: { borderTopWidth: 1, borderTopColor: tokens.lineSoft },
  rowPressed: { backgroundColor: tokens.lineSoft },
  dot: { width: 8, height: 8, borderRadius: 4 },
  dotOk: { backgroundColor: tokens.okFill },
  dotNone: { backgroundColor: tokens.disabled },
  main: { flex: 1, minWidth: 0 },
  jobName: { fontSize: 14.5, fontWeight: '700', color: tokens.ink },
  sub: { fontSize: 12, color: tokens.ink3, marginTop: 2 },
  tag: {
    fontSize: 10.5,
    fontWeight: '700',
    paddingHorizontal: 9,
    paddingVertical: 3,
    borderRadius: 999,
    borderWidth: 1,
    overflow: 'hidden',
  },
  tagOk: {
    color: tokens.ok,
    backgroundColor: tokens.okBg,
    borderColor: tokens.okBorder,
  },
  errorText: {
    fontSize: 13,
    color: tokens.ink3,
    textAlign: 'center',
    paddingVertical: 16,
  },
  tagNone: {
    color: tokens.warn,
    backgroundColor: tokens.warnBg,
    borderColor: tokens.warnBorder,
  },
});
