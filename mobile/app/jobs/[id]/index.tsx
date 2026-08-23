import { useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  ActivityIndicator,
  TouchableOpacity,
  StyleSheet,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLocalSearchParams, useRouter, type Href } from 'expo-router';
import { useTranslation } from 'react-i18next';
import axios from 'axios';

import {
  useJob,
  useJobBudgetSummary,
  useUpdateJob,
  useDeleteJob,
  type JobBudgetSummary,
} from '../../../src/api/hooks/useJobs';
import { useJobExpenses } from '../../../src/api/hooks/useExpenses';
import {
  useJobLabourRollup,
  useLabourSummary,
  type JobLabourRollup,
} from '../../../src/api/hooks/useLabour';
import { useMe } from '../../../src/api/hooks/useAuth';
import { RecentCapturesList } from '../../../src/components/RecentCapturesList';
import {
  formatDays,
  formatMoney,
  contractEnteredFromExGst,
  contractGstFromEntered,
} from '../../../src/util/format';
import { monthStart, monthEnd, todayISO } from '../../../src/util/dates';
import { localizeJobStatus } from '../../../src/util/jobStatus';
import { localizeCategoryName } from '../../../src/util/category';
import { useScaledStyles } from '../../../src/ui/type';
import { useOneShotBack } from '../../../src/util/navigation';
import { Chip, StatusBadge } from '../../../src/ui/kit';
import {
  ReceiptIcon,
  UsersIcon,
  FolderIcon,
  NoteIcon,
} from '../../../src/ui/icons';
import { tokens, type Tone } from '../../../src/ui/tokens';

/**
 * UI-kit v2 Batch 4-1: the job-details PAGE.
 *
 * Replaces the old native <Modal> presented from the Jobs tab — a
 * plain pushed route means back() works everywhere without the
 * modal focus-gate/epoch machinery, and expense drill-downs return
 * here naturally (the from=job return path is retired).
 *
 * Layout per the confirmed v2 preview: identity header, second-level
 * tab chips (Overview / Expenses / Labour / Files), the Financial
 * overview card (Revenue hero + contract/GST line + 4-grid + budget
 * bar) and the Projected-margin card on Overview. Files is a
 * placeholder pending its own project (receipts need backend +
 * storage decisions).
 *
 * Money rules unchanged: everything financial is admin-only (server
 * strips regardless; C-05/C-09 double-gate posture); the labour
 * rollup stays contributor-safe (money-free metrics for all roles).
 */

type DetailTab = 'overview' | 'expenses' | 'labour' | 'files' | 'notes';

export default function JobDetailScreen() {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const jobId = id ?? null;

  const onBack = useOneShotBack('/(tabs)/jobs');

  const { isLoading, data } = useJob(jobId);
  const me = useMe();
  const isAdmin = me.data?.role === 'admin';
  // C-09: summary query admin-gated client-side (no guaranteed-403
  // request for contributors); server stays authoritative.
  const summary = useJobBudgetSummary(isAdmin ? jobId : null);
  const jobExpenses = useJobExpenses(jobId, 20);
  const [labourRange, setLabourRange] = useState<'all' | 'month'>('all');
  const labourRollup = useJobLabourRollup(
    jobId,
    labourRange === 'month' ? monthStart(todayISO()) : null,
    labourRange === 'month' ? monthEnd(todayISO()) : null,
  );

  const [tab, setTab] = useState<DetailTab>('overview');

  const updateJob = useUpdateJob(jobId ?? '');
  const deleteJob = useDeleteJob();
  const lifecycleBusy = updateJob.isPending || deleteJob.isPending;

  const performStatusChange = async (target: 'active' | 'completed') => {
    try {
      await updateJob.mutateAsync({ status: target });
    } catch (err) {
      const detail = axios.isAxiosError(err)
        ? err.response?.data?.detail
        : undefined;
      Alert.alert(
        t('common.error'),
        typeof detail === 'string' ? detail : t('job.lifecycle_error'),
      );
    }
  };

  const onArchive = () => {
    Alert.alert(
      t('job.archive_confirm_title'),
      t('job.archive_confirm_message'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('job.archive_cta'),
          onPress: () => void performStatusChange('completed'),
        },
      ],
    );
  };

  const onReopen = () => {
    Alert.alert(
      t('job.reopen_confirm_title'),
      t('job.reopen_confirm_message'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('job.reopen_cta'),
          onPress: () => void performStatusChange('active'),
        },
      ],
    );
  };

  const onDeleteJob = () => {
    Alert.alert(
      t('job.delete_confirm_title'),
      t('job.delete_confirm_message'),
      [
        { text: t('common.cancel'), style: 'cancel' },
        {
          text: t('job.delete_cta'),
          style: 'destructive',
          onPress: async () => {
            if (!jobId) return;
            try {
              await deleteJob.mutateAsync({ jobId });
              // Job is gone — leave the page.
              onBack();
            } catch (err) {
              const detail = axios.isAxiosError(err)
                ? err.response?.data?.detail
                : undefined;
              // A 409 carries the backend's "…Archive it instead."
              // guidance verbatim — exactly what the user should see.
              Alert.alert(
                t('common.error'),
                typeof detail === 'string' ? detail : t('job.lifecycle_error'),
              );
            }
          },
        },
      ],
    );
  };

  // M5: Delete only when the expense query loaded and returned zero
  // RAW rows (raw includes rejected — zero genuinely means empty).
  // The server's 409 guard remains the authority.
  const emptyForDelete =
    isAdmin &&
    jobExpenses.isSuccess &&
    (jobExpenses.data?.items.length ?? 1) === 0;

  const subline = data
    ? [
        data.job_code,
        data.site_address,
        ...data.aliases.map((a) => a.alias_text),
      ]
        .filter(Boolean)
        .join(' · ')
    : '';

  const TABS: Array<{ key: DetailTab; label: string }> = [
    { key: 'overview', label: t('job.tab_overview') },
    { key: 'expenses', label: t('job.tab_expenses') },
    { key: 'labour', label: t('job.tab_labour') },
    { key: 'files', label: t('job.tab_files') },
    { key: 'notes', label: t('job.tab_notes') },
  ];

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right', 'bottom']}>
      <View style={s.headerRow}>
        <TouchableOpacity
          onPress={onBack}
          hitSlop={12}
          testID="job-detail-back"
          accessibilityRole="button"
          style={s.backBtn}
        >
          <Text style={s.backChevron}>{'‹'}</Text>
          <Text style={s.backLabel}>{t('tabs.jobs')}</Text>
        </TouchableOpacity>
        <Text style={s.headerTitle} numberOfLines={1}>
          {t('job.details_title')}
        </Text>
        {/* C-05: Edit is a money surface — admin-only client gate on
            top of the server's require_admin. */}
        {data && isAdmin ? (
          <TouchableOpacity
            onPress={() =>
              router.push(`/jobs/${jobId}/edit` as unknown as Href)
            }
            hitSlop={12}
            testID="job-detail-edit"
            accessibilityRole="button"
            style={s.editBtn}
          >
            <Text style={s.editBtnText}>{t('job.edit')}</Text>
          </TouchableOpacity>
        ) : (
          <View style={s.editSpacer} />
        )}
      </View>

      {isLoading ? (
        <View style={s.center}>
          <ActivityIndicator color="#1e293b" />
        </View>
      ) : !data ? (
        // Cached data keeps rendering on a failed refetch; the error
        // screen is only for a cold open with nothing to show.
        <View style={s.center}>
          <Text style={s.errText}>{t('common.error')}</Text>
        </View>
      ) : (
        <ScrollView contentContainerStyle={s.wrap}>
          <View style={s.identityRow}>
            <Text style={s.jobName} numberOfLines={2}>
              {data.job_name}
            </Text>
            <StatusBadge
              status={data.status}
              label={localizeJobStatus(data.status, t)}
            />
          </View>
          {subline ? (
            <Text style={s.subline} numberOfLines={2}>
              {subline}
            </Text>
          ) : null}

          {tab !== 'overview' ? (
            <TouchableOpacity
              onPress={() => setTab('overview')}
              style={s.subBack}
              accessibilityRole="button"
              testID="job-sub-back"
            >
              <Text style={s.subBackText}>
                {'‹ ' + t('job.tab_overview')}
              </Text>
            </TouchableOpacity>
          ) : null}

          {tab === 'overview' ? (
            <>
              {isAdmin ? (
                <>
                  <FinancialOverviewCard job={data} summary={summary} />
                  <MarginCard job={data} summary={summary} />
                  <PaidCard summary={summary} />
                </>
              ) : (
                <DetailRow
                  label={t('job.status')}
                  value={localizeJobStatus(data.status, t)}
                />
              )}
              {/* Fidelity §8 入口宫格: 4 tiles — 支出/用工/文件/备注. */}
              <View style={s.entryGrid} testID="job-entry-grid">
                {(
                  [
                    ['expenses', t('job.tab_expenses'), ReceiptIcon],
                    ['labour', t('job.tab_labour'), UsersIcon],
                    ['files', t('job.tab_files'), FolderIcon],
                    ['notes', t('job.tab_notes'), NoteIcon],
                  ] as const
                ).map(([key, label, Icon]) => (
                  <TouchableOpacity
                    key={key}
                    style={s.entryTile}
                    onPress={() => setTab(key)}
                    accessibilityRole="button"
                    testID={`job-tab-${key}`}
                  >
                    <Icon size={19} color={tokens.ink2} />
                    <Text style={s.entryTileText} numberOfLines={1}>
                      {label}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>
              {isAdmin ? (
                <View style={s.lifecycleSection} testID="job-lifecycle">
                  {data.status === 'active' ? (
                    <TouchableOpacity
                      onPress={onArchive}
                      disabled={lifecycleBusy}
                      style={[
                        s.lifecycleBtn,
                        lifecycleBusy && s.lifecycleBtnDisabled,
                      ]}
                      testID="job-archive"
                      accessibilityRole="button"
                    >
                      <Text style={s.lifecycleBtnText}>
                        {t('job.archive_cta')}
                      </Text>
                    </TouchableOpacity>
                  ) : (
                    <TouchableOpacity
                      onPress={onReopen}
                      disabled={lifecycleBusy}
                      style={[
                        s.lifecycleBtn,
                        lifecycleBusy && s.lifecycleBtnDisabled,
                      ]}
                      testID="job-reopen"
                      accessibilityRole="button"
                    >
                      <Text style={s.lifecycleBtnText}>
                        {t('job.reopen_cta')}
                      </Text>
                    </TouchableOpacity>
                  )}
                  {emptyForDelete ? (
                    <TouchableOpacity
                      onPress={onDeleteJob}
                      disabled={lifecycleBusy}
                      style={[
                        s.jobDeleteBtn,
                        lifecycleBusy && s.lifecycleBtnDisabled,
                      ]}
                      testID="job-delete"
                      accessibilityRole="button"
                    >
                      <Text style={s.jobDeleteBtnText}>
                        {t('job.delete_cta')}
                      </Text>
                    </TouchableOpacity>
                  ) : null}
                </View>
              ) : null}
            </>
          ) : null}

          {tab === 'expenses' ? (
            <RecentCapturesList
              query={jobExpenses}
              heading={t('job.expenses')}
            />
          ) : null}

          {tab === 'labour' ? (
            <LabourDaysSection
              jobId={jobId}
              rollup={labourRollup}
              isAdmin={isAdmin}
              range={labourRange}
              onRangeChange={setLabourRange}
            />
          ) : null}

          {tab === 'files' ? (
            <View style={s.comingSoonBox} testID="job-files-placeholder">
              <Text style={s.comingSoonText}>
                {t('job.files_coming_soon')}
              </Text>
            </View>
          ) : null}

          {/* Notes: placeholder like Files — the backend has no notes
              field yet, so the real feature needs its own project. */}
          {tab === 'notes' ? (
            <View style={s.comingSoonBox} testID="job-notes-placeholder">
              <Text style={s.comingSoonText}>
                {t('job.notes_coming_soon')}
              </Text>
            </View>
          ) : null}
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

/* ================= Financial overview (B4-1) ================= */

function FinancialOverviewCard({
  job,
  summary,
}: {
  job: NonNullable<ReturnType<typeof useJob>['data']>;
  summary: ReturnType<typeof useJobBudgetSummary>;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();

  // F2 display-hint model (unchanged math): stored value is ALWAYS
  // ex-GST; "entered" reconstructs the as-typed gross for inclusive
  // contracts.
  const storedEx =
    job.contract_value_ex_gst != null
      ? Number(job.contract_value_ex_gst)
      : null;
  const incl = job.gst_mode === 'inclusive';
  const entered =
    storedEx != null ? contractEnteredFromExGst(storedEx, incl) : null;

  const is403 =
    axios.isAxiosError(summary.error) &&
    summary.error.response?.status === 403;

  const sum = summary.data;
  const pct =
    sum?.percent_consumed != null ? parseFloat(sum.percent_consumed) : null;

  return (
    <View style={s.card} testID="job-financial-overview">
      <Text style={s.cardTitle}>{t('job.budgets_and_spending')}</Text>

      {/* Fidelity §8: the hero is 剩余预算 (34/800) with 「预算 $X ·
          已用 N%」 beneath. Contract/revenue demote to a secondary
          line below the bar. */}

      {/* Spending 4-grid + bar (summary data, four-state). */}
      {is403 ? null : summary.isLoading ? (
        <View style={s.inlineLoading} testID="job-spending-loading">
          <ActivityIndicator size="small" color="#64748b" />
          <Text style={s.inlineLoadingText}>{t('job.spending_loading')}</Text>
        </View>
      ) : summary.isError && !sum ? (
        <Text style={s.inlineError} testID="job-spending-error">
          {t('job.spending_load_error')}
        </Text>
      ) : sum ? (
        <View testID="job-spending-body">
          {/* Dogfood 2026-08-24: the operator could never see SPENT
              without mental math (the old card showed only remaining +
              two duplicate 62% bars). Twin heroes put all three numbers
              on screen: spent (+% of budget) and remaining (+budget);
              the category bar below scales to BUDGET so its grey tail
              IS the remaining share — one bar, no duplicate. */}
          {sum.total_budget_ex_gst != null && sum.remaining_ex_gst != null ? (
            <View style={s.heroRow}>
              <View style={s.heroCol}>
                <Text style={s.revLabel}>{t('job.spent_hero_label')}</Text>
                <Text
                  style={s.heroTwin}
                  testID="job-spent-hero"
                  numberOfLines={1}
                  adjustsFontSizeToFit
                  minimumFontScale={0.6}
                >
                  {formatMoney(sum.actual_ex_gst)}
                </Text>
                <Text style={s.heroSub} numberOfLines={1}>
                  {pct != null
                    ? t('job.pct_of_budget', { pct: pct.toFixed(0) })
                    : ' '}
                </Text>
              </View>
              <View style={s.heroColRight}>
                <Text style={s.revLabel}>
                  {t('job.remaining_label_short')}
                </Text>
                <Text
                  style={[
                    s.heroTwin,
                    sum.overspend ? s.overspendValue : s.remainOkValue,
                  ]}
                  testID="job-remaining-hero"
                  numberOfLines={1}
                  adjustsFontSizeToFit
                  minimumFontScale={0.6}
                >
                  {formatMoney(sum.remaining_ex_gst)}
                </Text>
                <Text style={s.heroSub} numberOfLines={1}>
                  {t('job.of_budget_total', {
                    budget: formatMoney(sum.total_budget_ex_gst),
                  })}
                </Text>
              </View>
            </View>
          ) : (
            <>
              <Text style={s.revLabel}>{t('job.spent_hero_label')}</Text>
              <Text
                style={s.heroRemaining}
                testID="job-spent-hero"
                numberOfLines={1}
                adjustsFontSizeToFit
                minimumFontScale={0.7}
              >
                {formatMoney(sum.actual_ex_gst)}
              </Text>
              <Text style={s.heroSub} numberOfLines={1}>
                {t('job.no_budget_set')}
              </Text>
            </>
          )}
          <CompositionBar
            categories={sum.categories ?? []}
            uncategorised={sum.uncategorised_actual_ex_gst}
            budgetTotal={sum.total_budget_ex_gst}
          />
          {storedEx != null ? (
            <>
              <View style={s.cardDivider} />
              <View style={s.contractLine}>
                <Text style={s.contractText}>
                  {`${t('job.ex_gst_revenue')} ${formatMoney(storedEx)}`}
                </Text>
                <View style={s.gstChip}>
                  <Text style={s.gstChipText}>
                    {t(incl ? 'job.gst_including' : 'job.gst_none_cash')}
                  </Text>
                </View>
                {entered != null && incl ? (
                  <Text style={s.contractText}>
                    {`· ${t('job.gst_amount')} ${formatMoney(contractGstFromEntered(entered, incl))}`}
                  </Text>
                ) : null}
              </View>
            </>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

/**
 * F4 (handoff §8 成本构成条): where the spend went, as one segmented
 * bar + a legend. The spec draws four FIXED classes (材料/人工/分包/
 * 其他); real data is the tenant's own category list, so this renders
 * the top three by actual spend and buckets the tail as 其他 — same
 * four segments, true numbers. Colours are the cat1-4 semantic set.
 * Renders nothing until there is actual spend.
 */
const CAT_COLOURS = [tokens.cat1, tokens.cat2, tokens.cat3, tokens.cat4];

function CompositionBar({
  categories,
  uncategorised,
  budgetTotal,
}: {
  categories: Array<{
    category_id: string;
    category_name: string;
    actual_ex_gst: string;
  }>;
  /** NULL-category spend (AI captures can leave category unset). The
   *  backend returns it separately so the bar can reconcile with the
   *  card's 已支出 total — it belongs in the 其他 bucket. */
  uncategorised: string | null | undefined;
  /** Job budget (ex GST). When it exceeds total spend the bar scales
   *  to the BUDGET, so the grey track tail visually IS the remaining
   *  share — replaces the old separate %-used bar. Overspend (or no
   *  budget) falls back to scaling by spend, a full bar. */
  budgetTotal: string | null | undefined;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();

  const spent = categories
    .map((c) => ({
      id: c.category_id,
      name: c.category_name,
      value: parseFloat(c.actual_ex_gst),
    }))
    .filter((c) => Number.isFinite(c.value) && c.value > 0)
    .sort((a, b) => b.value - a.value);
  const catTotal = spent.reduce((a, c) => a + c.value, 0);
  const total =
    catTotal + (uncategorised != null && Number.isFinite(parseFloat(uncategorised)) && parseFloat(uncategorised) > 0 ? parseFloat(uncategorised) : 0);
  if (total <= 0) return null;

  const budget = budgetTotal != null ? parseFloat(budgetTotal) : NaN;
  const denom =
    Number.isFinite(budget) && budget > total ? budget : total;

  const top = spent.slice(0, 3);
  const uncat = uncategorised != null ? parseFloat(uncategorised) : 0;
  const restValue =
    spent.slice(3).reduce((a, c) => a + c.value, 0) +
    (Number.isFinite(uncat) && uncat > 0 ? uncat : 0);
  const segs = [
    ...top.map((c) => ({ id: c.id, name: c.name, value: c.value })),
    ...(restValue > 0
      ? [{ id: '__rest__', name: t('job.other_categories'), value: restValue }]
      : []),
  ];

  return (
    <View style={s.compWrap} testID="job-composition">
      <View style={s.compBar}>
        {segs.map((seg, i) => (
          <View
            key={seg.id}
            style={{
              flex: seg.value / denom,
              backgroundColor: CAT_COLOURS[i % CAT_COLOURS.length],
            }}
          />
        ))}
        {denom > total ? (
          <View style={{ flex: (denom - total) / denom }} />
        ) : null}
      </View>
      <View style={s.compLegend}>
        {segs.map((seg, i) => (
          <View key={seg.id} style={s.compLegendItem}>
            <View
              style={[
                s.compDot,
                { backgroundColor: CAT_COLOURS[i % CAT_COLOURS.length] },
              ]}
            />
            <Text style={s.compName} numberOfLines={1}>
              {seg.id === '__rest__'
                ? seg.name
                : localizeCategoryName(seg.name, t)}
            </Text>
            <Text style={s.compValue} numberOfLines={1}>
              {formatMoney(seg.value.toFixed(2))}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
}

/** Fidelity §8 实付卡: cash-flow view — 现金按面值 + 发票按含GST总额. */
function PaidCard({
  summary,
}: {
  summary: ReturnType<typeof useJobBudgetSummary>;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const sum = summary.data;
  if (!sum) return null;
  return (
    <View style={s.card} testID="job-paid-card">
      <Text style={s.cardTitle}>{t('job.paid_title')}</Text>
      <Text style={s.metricHint}>{t('job.paid_sub')}</Text>
      <Text
        style={s.paidValue}
        numberOfLines={1}
        adjustsFontSizeToFit
        minimumFontScale={0.7}
      >
        {formatMoney(sum.actual_inc_gst)}
      </Text>
    </View>
  );
}

/* ================= Margin card (moved from the modal, B3 logic) ==== */

function MarginCard({
  job,
  summary,
}: {
  job: NonNullable<ReturnType<typeof useJob>['data']>;
  summary: ReturnType<typeof useJobBudgetSummary>;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const data = summary.data;
  if (!data) return null; // admin-only (contributor gets no data)

  const contract =
    job.contract_value_ex_gst != null
      ? Number(job.contract_value_ex_gst)
      : null;
  const target =
    job.target_profit_ratio_pct != null
      ? Number(job.target_profit_ratio_pct)
      : null;

  // Current margin (to date) = (contract - cost-so-far)/contract, only
  // when a positive contract exists (display arithmetic on server
  // figures — unchanged since F1).
  const current =
    contract != null && contract > 0
      ? ((contract - Number(data.actual_ex_gst)) / contract) * 100
      : null;

  // Projected margin comes from the SERVER (budgeted_profit_ratio_pct);
  // zero/no budget -> no misleading 100% hero.
  const budget =
    data.total_budget_ex_gst != null
      ? Number(data.total_budget_ex_gst)
      : null;
  const projected =
    budget != null && budget > 0 && data.budgeted_profit_ratio_pct != null
      ? Number(data.budgeted_profit_ratio_pct)
      : null;

  // Fidelity §8: with NO contract there is no margin to show — render
  // the dashed set-it-up prompt instead of vanishing (admins only;
  // contributors have no summary and never reach here).
  if (contract == null || contract <= 0) {
    return (
      <View style={s.card} testID="job-margin-nocontract">
        <Text style={s.cardTitle}>{t('job.margin_header')}</Text>
        <View style={s.noContractBox}>
          <Text style={s.noContractText}>{t('job.no_contract_prompt')}</Text>
        </View>
      </View>
    );
  }
  if (target == null && current == null && projected == null) return null;

  const hero = projected ?? current;
  const heroIsProjected = projected != null;
  const delta = hero != null && target != null ? hero - target : null;

  return (
    <View style={s.card} testID="job-margin">
      <Text style={s.cardTitle}>{t('job.margin_header')}</Text>
      {hero != null ? (
        <>
          <Text style={s.marginHeroLabel}>
            {heroIsProjected
              ? t('job.projected_margin')
              : t('job.current_margin_to_date')}
          </Text>
          <View style={s.marginHeroRow}>
            <Text
              style={s.marginHeroValue}
              testID={
                heroIsProjected ? 'job-margin-projected' : 'job-margin-current'
              }
            >
              {hero.toFixed(1)}%
            </Text>
            {delta != null && target != null ? (
              <View
                style={[
                  s.marginPill,
                  delta >= 0 ? s.marginPillOk : s.marginPillBad,
                ]}
                testID="job-margin-delta"
              >
                <Text
                  style={[
                    s.marginPillText,
                    { color: delta >= 0 ? tokens.ok : tokens.bad },
                  ]}
                >
                  {t('job.margin_delta_pill', {
                    delta: `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}`,
                    target: target.toFixed(1),
                  })}
                </Text>
              </View>
            ) : null}
          </View>
          {/* F4 (handoff §8 利润刻度尺): 0-50% scale, filled to the
              hero margin, with a tick at the target. Values are the
              same server-derived figures as the hero — the ruler is
              pure presentation. */}
          <View style={s.ruler} testID="job-margin-ruler">
            <View style={s.rulerTrack}>
              <View
                style={[
                  s.rulerFill,
                  {
                    width: `${(Math.min(Math.max(hero, 0), 50) / 50) * 100}%`,
                    backgroundColor:
                      delta != null && delta < 0 ? tokens.warnFill : tokens.okFill,
                  },
                ]}
              />
              {target != null && target >= 0 && target <= 50 ? (
                <View
                  style={[s.rulerTick, { left: `${(target / 50) * 100}%` }]}
                />
              ) : null}
            </View>
            <View style={s.rulerLabels}>
              <Text style={s.rulerLabel}>0%</Text>
              {target != null ? (
                <Text style={s.rulerLabel}>
                  {t('job.ruler_target', { target: target.toFixed(0) })}
                </Text>
              ) : null}
              <Text style={s.rulerLabel}>50%</Text>
            </View>
          </View>
          {heroIsProjected ? (
            <Text style={s.metricHint} testID="job-margin-projected-hint">
              {t('job.projected_margin_hint')}
            </Text>
          ) : null}
        </>
      ) : target != null ? (
        <DetailRow
          label={t('job.target_margin_pct')}
          value={`${target.toFixed(1)}%`}
        />
      ) : null}
      {heroIsProjected && current != null ? (
        <DetailRow
          label={t('job.current_margin_to_date')}
          value={`${current.toFixed(1)}%`}
        />
      ) : null}
      {current != null ? (
        <Text style={s.metricHint} testID="job-margin-todate-hint">
          {t('job.margin_todate_hint')}
        </Text>
      ) : null}
    </View>
  );
}

/* ================= Labour rollup (moved from the modal) ============ */

function LabourDaysSection({
  jobId,
  rollup,
  isAdmin,
  range,
  onRangeChange,
}: {
  jobId: string | null;
  rollup: ReturnType<typeof useJobLabourRollup>;
  isAdmin: boolean;
  range: 'all' | 'month';
  onRangeChange: (next: 'all' | 'month') => void;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  const row: JobLabourRollup | undefined = rollup.data?.[0];
  // F4 (handoff §9): admin-only by-worker list for THIS job. The
  // summary endpoint is admin-only (403 for contributors) — every arg
  // is nulled for non-admins so the query is disabled and never fires
  // (C-09 posture: no guaranteed-403 requests).
  const summary = useLabourSummary(
    isAdmin && range === 'month' ? monthStart(todayISO()) : null,
    isAdmin && range === 'month' ? monthEnd(todayISO()) : null,
    isAdmin ? jobId : null,
  );
  const workers = (summary.data?.workers ?? [])
    .map((w) => ({
      id: w.worker_id,
      name: w.display_name,
      cost: w.labour_cost != null ? parseFloat(w.labour_cost) : null,
      days: w.total_days,
      hours: w.total_hours,
      gap: w.entries_costed < w.entries_total,
    }))
    .sort((a, b) => (b.cost ?? 0) - (a.cost ?? 0));
  const maxCost = Math.max(1, ...workers.map((w) => w.cost ?? 0));

  return (
    <View testID="job-labour-days">
      <Text style={s.sectionHeader}>{t('labour.job_rollup_header')}</Text>
      <View style={s.labourRangeRow}>
        {(['all', 'month'] as const).map((opt) => (
          <Chip
            key={opt}
            label={t(
              opt === 'all' ? 'labour.range_all_time' : 'labour.range_this_month',
            )}
            selected={range === opt}
            onPress={() => onRangeChange(opt)}
            testID={`job-labour-range-${opt}`}
          />
        ))}
      </View>

      {rollup.isLoading ? (
        <View style={s.inlineLoading} testID="job-labour-loading">
          <ActivityIndicator size="small" color="#64748b" />
        </View>
      ) : rollup.isError ? (
        <Text style={s.inlineError} testID="job-labour-error">
          {t('labour.job_days_error')}
        </Text>
      ) : (
        <>
          {/* Three money-free metrics, shown to every role. */}
          <DetailRow
            label={t('labour.job_labourers_label')}
            value={String(row?.labourers ?? 0)}
          />
          <DetailRow
            label={t('labour.job_worker_days_label')}
            value={formatDays(row?.worker_days ?? 0)}
          />
          <DetailRow
            label={t('labour.job_days_on_site_label')}
            value={String(row?.days_on_site ?? 0)}
          />
          <Text style={s.metricHint} testID="labour-days-hint">
            {t('labour.days_metrics_hint')}
          </Text>
          {/* Admin-only money rows — server nulls these for
              contributors; the isAdmin gate is defence in depth. */}
          {isAdmin && row?.total_hours != null ? (
            <DetailRow
              label={t('labour.total_hours')}
              value={t('labour.hours_value', { hours: row.total_hours })}
            />
          ) : null}
          {isAdmin && row?.labour_cost != null ? (
            <DetailRow
              label={t('labour.job_cost_label')}
              value={formatMoney(row.labour_cost)}
            />
          ) : null}
          {/* By-worker (admin): cost-proportional blue bars; amber when
              the worker has uncosted entries (missing rate/hours). Only
              renders on REAL data — a failed fetch shows nothing rather
              than asserting an empty crew (F1 evidence rule). */}
          {isAdmin && summary.data && workers.length > 0 ? (
            <View style={s.byWorkerWrap} testID="job-by-worker">
              <Text style={s.byWorkerTitle}>{t('job.by_worker')}</Text>
              {workers.map((w) => (
                <View key={w.id} style={s.bwRow}>
                  <View style={s.bwTop}>
                    <Text style={s.bwName} numberOfLines={1}>
                      {w.name}
                    </Text>
                    <Text style={s.bwMeta} numberOfLines={1}>
                      {[
                        w.cost != null ? formatMoney(w.cost.toFixed(2)) : null,
                        // Day-only entries now cost via the org
                        // default-day-hours parameter, so "· 0 h" next
                        // to money read as contradiction — hours only
                        // renders when hours were actually recorded.
                        w.hours != null
                          ? t('job.bw_days_hours', {
                              days: formatDays(w.days),
                              hours: w.hours,
                            })
                          : t('job.bw_days_only', {
                              days: formatDays(w.days),
                            }),
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </Text>
                  </View>
                  <View style={s.bwTrack}>
                    <View
                      style={[
                        s.bwFill,
                        {
                          width: `${Math.min(100, Math.max(2, ((w.cost ?? 0) / maxCost) * 100))}%`,
                          backgroundColor: w.gap
                            ? tokens.warnFill
                            : tokens.primary,
                        },
                      ]}
                    />
                  </View>
                </View>
              ))}
            </View>
          ) : null}
          {isAdmin && summary.data && workers.length === 0 ? (
            <Text style={s.metricHint} testID="job-by-worker-empty">
              {t('job.labour_empty')}
            </Text>
          ) : null}
        </>
      )}
    </View>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  const s = useScaledStyles(base);
  return (
    <View style={s.detailRow}>
      <Text style={s.detailLabel}>{label}</Text>
      <Text style={s.detailValue}>{value}</Text>
    </View>
  );
}

/* ================= styles ================= */

const base = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderBottomWidth: 1,
    borderBottomColor: tokens.line,
    backgroundColor: tokens.surface,
  },
  backBtn: {
    minWidth: 64,
    minHeight: 44,
    flexDirection: 'row',
    alignItems: 'center',
  },
  backChevron: { fontSize: 30, lineHeight: 32, color: tokens.primary },
  backLabel: { fontSize: 15, color: tokens.primary, marginLeft: 2 },
  headerTitle: {
    flex: 1,
    textAlign: 'center',
    fontSize: 16,
    fontWeight: '700',
    color: tokens.ink,
  },
  editBtn: { minWidth: 64, minHeight: 44, alignItems: 'flex-end', justifyContent: 'center' },
  editBtnText: { fontSize: 15, fontWeight: '600', color: tokens.primary },
  editSpacer: { minWidth: 64 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  errText: { color: '#b91c1c', fontSize: 15 },
  wrap: { padding: 16, gap: 12, paddingBottom: 32 },

  identityRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 10,
  },
  jobName: { flex: 1, fontSize: 22, fontWeight: '700', color: tokens.ink },
  subline: { fontSize: 12.5, color: tokens.ink3 },

  subBack: { paddingVertical: 4 },
  subBackText: { fontSize: 14, fontWeight: '600', color: tokens.primary },
  entryGrid: { flexDirection: 'row', gap: 8 },
  entryTile: {
    flex: 1,
    alignItems: 'center',
    gap: 6,
    paddingVertical: 14,
    backgroundColor: tokens.surface,
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 14,
  },
  entryTileText: { fontSize: 12, fontWeight: '600', color: tokens.ink2 },
  heroRemaining: {
    fontSize: 34,
    fontWeight: '800',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
    letterSpacing: -1,
    marginTop: 2,
  },
  heroSub: {
    fontSize: 12.5,
    color: tokens.ink2,
    marginTop: 2,
    fontVariant: ['tabular-nums'],
  },
  heroRow: { flexDirection: 'row', gap: 12, marginTop: 2 },
  heroCol: { flex: 1 },
  heroColRight: { flex: 1, alignItems: 'flex-end' },
  heroTwin: {
    fontSize: 26,
    fontWeight: '800',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
    letterSpacing: -0.6,
    marginTop: 2,
  },
  remainOkValue: { color: tokens.ok },
  paidValue: {
    fontSize: 22,
    fontWeight: '800',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
    letterSpacing: -0.3,
    marginTop: 6,
  },

  card: {
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 18,
    padding: 14,
    backgroundColor: tokens.surface,
    shadowColor: '#101828',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.05,
    shadowRadius: 14,
    elevation: 2,
  },
  cardTitle: { fontSize: 13.5, fontWeight: '700', color: tokens.ink },
  cardDivider: {
    height: 1,
    backgroundColor: tokens.lineSoft,
    marginVertical: 10,
  },

  revLabel: { marginTop: 8, fontSize: 12, color: tokens.ink3 },
  revValue: {
    fontSize: 26,
    fontWeight: '800',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
    letterSpacing: -0.5,
  },
  contractLine: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 6,
    marginTop: 4,
  },
  contractText: {
    fontSize: 12,
    color: tokens.ink2,
    fontVariant: ['tabular-nums'],
  },
  gstChip: {
    borderRadius: 999,
    borderWidth: 1,
    borderColor: tokens.line,
    backgroundColor: tokens.lineSoft,
    paddingHorizontal: 7,
    paddingVertical: 2,
  },
  gstChipText: { fontSize: 10, fontWeight: '700', color: tokens.ink2 },

  grid: { flexDirection: 'row', flexWrap: 'wrap' },
  gridCell: { width: '50%', paddingVertical: 6, paddingRight: 8 },
  gridLabel: { fontSize: 11.5, color: tokens.ink2 },
  gridValue: {
    fontSize: 15.5,
    fontWeight: '700',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
    marginTop: 1,
  },
  overspendValue: { color: tokens.bad },
  barWrap: { marginTop: 6 },
  compWrap: { marginTop: 12, gap: 10 },
  compBar: {
    flexDirection: 'row',
    height: 14,
    borderRadius: 7,
    overflow: 'hidden',
    backgroundColor: tokens.barTrack,
  },
  compLegend: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    rowGap: 6,
  },
  compLegendItem: {
    width: '50%',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingRight: 8,
  },
  compDot: { width: 9, height: 9, borderRadius: 3 },
  compName: { flexShrink: 1, fontSize: 12, color: tokens.ink2 },
  noContractBox: {
    marginTop: 10,
    borderWidth: 1.5,
    borderColor: tokens.disabled,
    borderStyle: 'dashed',
    borderRadius: 12,
    padding: 14,
  },
  noContractText: { fontSize: 13, color: tokens.ink3, lineHeight: 19 },
  byWorkerWrap: { marginTop: 12, gap: 10 },
  byWorkerTitle: { fontSize: 13, fontWeight: '700', color: tokens.ink },
  bwRow: { gap: 5 },
  bwTop: {
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    gap: 10,
  },
  bwName: { flexShrink: 1, fontSize: 13.5, fontWeight: '600', color: tokens.ink },
  bwMeta: { fontSize: 11.5, color: tokens.ink2, fontVariant: ['tabular-nums'] },
  bwTrack: {
    height: 7,
    borderRadius: 4,
    backgroundColor: tokens.barTrack,
    overflow: 'hidden',
  },
  bwFill: { height: 7, borderRadius: 4 },
  ruler: { marginTop: 10, gap: 4 },
  rulerTrack: {
    height: 10,
    borderRadius: 5,
    backgroundColor: tokens.barTrack,
    overflow: 'visible',
  },
  rulerFill: { height: 10, borderRadius: 5 },
  rulerTick: {
    position: 'absolute',
    top: -2.5,
    width: 2.5,
    height: 15,
    borderRadius: 1,
    backgroundColor: tokens.ink,
  },
  rulerLabels: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  rulerLabel: { fontSize: 10.5, color: tokens.muted },
  compValue: {
    fontSize: 12,
    fontWeight: '700',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
  },

  inlineLoading: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 6,
  },
  inlineLoadingText: { fontSize: 12.5, color: tokens.ink2 },
  inlineError: { fontSize: 12.5, color: '#b91c1c', paddingVertical: 6 },
  metricHint: { fontSize: 12, color: '#64748b', paddingVertical: 2, marginTop: 4 },

  marginHeroLabel: { fontSize: 12, color: tokens.ink3, marginTop: 8 },
  marginHeroRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: 10,
    marginTop: 4,
    flexWrap: 'wrap',
  },
  marginHeroValue: {
    fontSize: 29,
    fontWeight: '800',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
    letterSpacing: -0.5,
  },
  marginPill: {
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  marginPillOk: { backgroundColor: tokens.okBg, borderColor: tokens.okBorder },
  marginPillBad: {
    backgroundColor: tokens.badBg,
    borderColor: tokens.badBorder,
  },
  marginPillText: {
    fontSize: 10.5,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
  },

  sectionHeader: {
    fontSize: 15,
    fontWeight: '700',
    color: tokens.ink,
    marginTop: 4,
  },
  labourRangeRow: { flexDirection: 'row', gap: 8, marginVertical: 8 },

  detailRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'baseline',
    paddingVertical: 6,
    gap: 12,
  },
  detailLabel: { fontSize: 13.5, color: tokens.ink2, flexShrink: 0 },
  detailValue: {
    fontSize: 14.5,
    fontWeight: '600',
    color: tokens.ink,
    fontVariant: ['tabular-nums'],
    flexShrink: 1,
    textAlign: 'right',
  },

  lifecycleSection: { gap: 10, marginTop: 4 },
  lifecycleBtn: {
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 12,
    paddingVertical: 12,
    alignItems: 'center',
    backgroundColor: '#ffffff',
  },
  lifecycleBtnDisabled: { opacity: 0.5 },
  lifecycleBtnText: { fontSize: 14.5, fontWeight: '600', color: tokens.ink },
  jobDeleteBtn: {
    borderWidth: 1,
    borderColor: tokens.badBorder,
    borderRadius: 12,
    paddingVertical: 12,
    alignItems: 'center',
    backgroundColor: tokens.badBg,
  },
  jobDeleteBtnText: { fontSize: 14.5, fontWeight: '600', color: tokens.bad },

  comingSoonBox: {
    borderWidth: 1,
    borderColor: tokens.line,
    borderRadius: 14,
    padding: 24,
    alignItems: 'center',
    // On the grey ground a lineSoft fill is invisible — this box sits
    // in the detail scroll body, not inside a card.
    backgroundColor: tokens.surface,
  },
  comingSoonText: {
    fontSize: 13.5,
    color: tokens.ink2,
    textAlign: 'center',
    lineHeight: 20,
  },
});
