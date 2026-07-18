import { useEffect, useMemo, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  ActivityIndicator,
  Pressable,
  TouchableOpacity,
  RefreshControl,
  ScrollView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { Href } from 'expo-router';
import { useTranslation } from 'react-i18next';

import {
  useExpensesList,
  type ExpenseListFilters,
  type ReviewStatus,
} from '../../src/api/hooks/useExpenses';
import { useJobs } from '../../src/api/hooks/useJobs';
import { useSuppliers } from '../../src/api/hooks/useSuppliers';
import { useCategories } from '../../src/api/hooks/useCategories';
import { ExpenseRow } from '../../src/components/ExpenseRow';
import {
  OptionPickerModal,
  type PickerOption,
} from '../../src/components/OptionPickerModal';
import { localizeCategoryName } from '../../src/util/category';
import {
  useExpenseListFiltersStore,
  type ExpenseListDatePreset,
} from '../../src/store/expenseListFilters';
import { useOneShotBack } from '../../src/util/navigation';
import { Chip } from '../../src/ui/kit';
import { tokens } from '../../src/ui/tokens';

/**
 * M2-B: full expenses list.
 *
 * Route: ``/expenses/list`` (stack sibling of the detail route).
 * Entered via "View all expenses" under the Last-5 list on the
 * Capture screen (pushed via the tab-bar ➕) and the Home month-spend card.
 *
 * Phone-native by design: a chip bar of single-select filters
 * (job / status / date preset / supplier / category) over an
 * infinitely-scrolling FlatList — NOT a clone of the admin web
 * table.
 *
 * Pagination: M2-A keyset cursor. The hook echoes ``next_cursor``
 * verbatim (opaque token); ``onEndReached`` fetches the next page
 * until the server returns a null cursor.
 *
 * Role behaviour: the backend is authoritative — admins see the
 * whole tenant, contributors are server-scoped to their own rows.
 * This screen sends no role-dependent params.
 *
 * Rejected rows: the DEFAULT view hides them client-side
 * (display-only filter, mirroring RecentCapturesList's dogfood
 * rule "delete = gone from active workflow"). Explicitly selecting
 * the Rejected status filter shows them — the first mobile view of
 * soft-deleted rows. Because the hide is display-only, a page can
 * render slightly short of the server page size; cursor correctness
 * (no duplicate / no skip) is unaffected.
 */

type PickerKind = 'job' | 'status' | 'date' | 'supplier' | 'category';

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}

/** Device-local "from" date for a preset; undefined = no date filter. */
function presetFromDate(preset: ExpenseListDatePreset): string | undefined {
  const now = new Date();
  if (preset === 'week') {
    const sinceMonday = (now.getDay() + 6) % 7;
    const monday = new Date(now);
    monday.setDate(now.getDate() - sinceMonday);
    return isoDate(monday);
  }
  if (preset === 'month') {
    return isoDate(new Date(now.getFullYear(), now.getMonth(), 1));
  }
  return undefined;
}

export default function ExpenseListScreen() {
  const { t } = useTranslation();

  // M2-B review fix: filters live in a store, NOT component state —
  // originally because the old root Slot navigator fully unmounted
  // this screen on every drill-in to the detail route; the store also
  // gets reset on logout (see store/expenseListFilters.ts).
  const jobId = useExpenseListFiltersStore((st) => st.jobId);
  const status = useExpenseListFiltersStore((st) => st.status);
  const datePreset = useExpenseListFiltersStore((st) => st.datePreset);
  const supplierId = useExpenseListFiltersStore((st) => st.supplierId);
  const categoryId = useExpenseListFiltersStore((st) => st.categoryId);
  const setJobId = useExpenseListFiltersStore((st) => st.setJobId);
  const setStatus = useExpenseListFiltersStore((st) => st.setStatus);
  const setDatePreset = useExpenseListFiltersStore((st) => st.setDatePreset);
  const setSupplierId = useExpenseListFiltersStore((st) => st.setSupplierId);
  const setCategoryId = useExpenseListFiltersStore((st) => st.setCategoryId);

  const [openPicker, setOpenPicker] = useState<PickerKind | null>(null);
  // Spec §5 quick pills: 全部 / 待审核 / 现金 / 缺发票. 现金 is a
  // client-side filter (the list endpoint has no payment param);
  // 缺发票 maps to the server's receipt_status. The advanced pickers
  // below stay — they filter dimensions the pills don't cover.
  const [quick, setQuick] = useState<'all' | 'pending' | 'cash' | 'noinv'>(
    'all',
  );
  // Spec §5: the advanced dropdown filters are gone from the UI —
  // clear any persisted values once so they can't invisibly filter.
  useEffect(() => {
    setJobId(null);
    setStatus(null);
    setDatePreset('all' as ExpenseListDatePreset);
    setSupplierId(null);
    setCategoryId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // M2-B review fix: drive the pull-spinner from an explicit "the
  // user pulled" flag — `isRefetching` is also true during background
  // refetches (e.g. post-mutation invalidation), which would pin the
  // spinner without a pull.
  const [userRefreshing, setUserRefreshing] = useState(false);

  const jobs = useJobs();
  const suppliers = useSuppliers();
  const categories = useCategories();

  // Recomputed every render (NOT memoised) so a screen left open
  // across a week/month boundary picks up the new period start on
  // the next render — the value is a plain string, so when the day
  // hasn't changed, downstream memos see an identical input.
  const fromDate = presetFromDate(datePreset);

  const effectiveStatus = quick === 'pending' ? 'pending' : status;
  const filters = useMemo<ExpenseListFilters>(
    () => ({
      ...(jobId ? { jobId } : {}),
      ...(effectiveStatus ? { status: effectiveStatus } : {}),
      ...(supplierId ? { supplierId } : {}),
      ...(categoryId ? { categoryId } : {}),
      ...(fromDate ? { from: fromDate } : {}),
      ...(quick === 'noinv' ? { receiptStatus: 'expected_later' as const } : {}),
    }),
    [jobId, effectiveStatus, supplierId, categoryId, fromDate, quick],
  );

  const list = useExpensesList(filters);

  const jobMap = useMemo(() => {
    const m = new Map<string, string>();
    jobs.data?.forEach((j) => m.set(j.job_id, j.job_name));
    return m;
  }, [jobs.data]);

  const supplierMap = useMemo(() => {
    const m = new Map<string, string>();
    suppliers.data?.forEach((x) => m.set(x.supplier_id, x.supplier_name));
    return m;
  }, [suppliers.data]);

  const categoryMap = useMemo(() => {
    const m = new Map<string, string>();
    categories.data?.forEach((c) => m.set(c.category_id, c.category_name));
    return m;
  }, [categories.data]);

  const items = useMemo(() => {
    const all = (list.data?.pages ?? []).flatMap((p) => p.items);
    const base = effectiveStatus
      ? all
      : all.filter((e) => e.review_status !== 'rejected');
    return quick === 'cash'
      ? base.filter((e) => e.payment_method === 'cash')
      : base;
  }, [list.data, effectiveStatus, quick]);

  const onBack = useOneShotBack('/(tabs)/home' as unknown as Href);

  // M2-B review fix: the error gate matters. Without it, a failed
  // next-page fetch (weak field network, backend 5xx) re-arms
  // onEndReached via the footer's mount/unmount content-size change
  // and produces an unbounded, backoff-free retry loop while the
  // user rests at the bottom. With the gate, a failure parks the
  // list in the footer's error state until the user explicitly
  // retries (footer button) or pulls to refresh.
  const onEndReached = () => {
    if (
      list.hasNextPage &&
      !list.isFetchingNextPage &&
      !list.isFetchNextPageError
    ) {
      void list.fetchNextPage();
    }
  };

  const onRefresh = () => {
    setUserRefreshing(true);
    void list.refetch().finally(() => setUserRefreshing(false));
  };

  // Chip labels reflect the active selection; unselected chips show
  // the filter's name.
  const chipLabels: Record<PickerKind, string> = {
    job: jobId
      ? (jobMap.get(jobId) ?? t('expense_list.filter_job'))
      : t('expense_list.filter_job'),
    status: status
      ? t(`expense.status_${status}`)
      : t('expense_list.filter_status'),
    date:
      datePreset === 'all'
        ? t('expense_list.filter_date')
        : datePreset === 'week'
          ? t('expense_list.date_week')
          : t('expense_list.date_month'),
    supplier: supplierId
      ? (supplierMap.get(supplierId) ?? t('expense_list.filter_supplier'))
      : t('expense_list.filter_supplier'),
    category: categoryId
      ? localizeCategoryName(categoryMap.get(categoryId) ?? null, t)
      : t('expense_list.filter_category'),
  };

  const chipActive: Record<PickerKind, boolean> = {
    job: !!jobId,
    status: !!status,
    date: datePreset !== 'all',
    supplier: !!supplierId,
    category: !!categoryId,
  };

  const allOption: PickerOption = { value: null, label: t('expense_list.all') };

  // One modal instance; contents switch on which chip opened it.
  const picker: {
    title: string;
    options: PickerOption[];
    selected: string | null;
    onSelect: (v: string | null) => void;
  } | null = useMemo(() => {
    switch (openPicker) {
      case 'job':
        return {
          title: t('expense_list.filter_job'),
          options: [
            allOption,
            ...(jobs.data ?? []).map((j) => ({
              value: j.job_id,
              label: j.job_name,
            })),
          ],
          selected: jobId,
          onSelect: setJobId,
        };
      case 'status':
        return {
          title: t('expense_list.filter_status'),
          options: [
            allOption,
            { value: 'pending', label: t('expense.status_pending') },
            { value: 'reviewed', label: t('expense.status_reviewed') },
            { value: 'rejected', label: t('expense.status_rejected') },
          ],
          selected: status,
          onSelect: (v) => setStatus((v as ReviewStatus | null) ?? null),
        };
      case 'date':
        return {
          title: t('expense_list.filter_date'),
          options: [
            allOption,
            { value: 'week', label: t('expense_list.date_week') },
            { value: 'month', label: t('expense_list.date_month') },
          ],
          selected: datePreset === 'all' ? null : datePreset,
          onSelect: (v) =>
            setDatePreset((v as ExpenseListDatePreset | null) ?? 'all'),
        };
      case 'supplier':
        return {
          title: t('expense_list.filter_supplier'),
          options: [
            allOption,
            ...(suppliers.data ?? []).map((x) => ({
              value: x.supplier_id,
              label: x.supplier_name,
            })),
          ],
          selected: supplierId,
          onSelect: setSupplierId,
        };
      case 'category':
        return {
          title: t('expense_list.filter_category'),
          options: [
            allOption,
            ...(categories.data ?? []).map((c) => ({
              value: c.category_id,
              label: localizeCategoryName(c.category_name, t),
            })),
          ],
          selected: categoryId,
          onSelect: setCategoryId,
        };
      default:
        return null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    openPicker,
    jobs.data,
    suppliers.data,
    categories.data,
    jobId,
    status,
    datePreset,
    supplierId,
    categoryId,
    t,
  ]);

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <View style={s.header}>
        <Pressable
          onPress={onBack}
          hitSlop={12}
          testID="expense-list-back"
          accessibilityRole="button"
          accessibilityLabel={t('expense.back')}
          style={({ pressed }) => [s.backBtn, pressed && s.backBtnPressed]}
        >
          <Text style={s.backChevron}>{'‹'}</Text>
          <Text style={s.backLabel}>{t('expense.back')}</Text>
        </Pressable>
        <Text style={s.headerTitle} numberOfLines={1}>
          {t('expense_list.title')}
        </Text>
        <View style={s.headerSpacer} />
      </View>

      {/* Spec §5: one-tap quick pills. 全部 selected = black. */}
      <View style={s.quickRow} testID="expense-quick-pills">
        {(
          [
            ['all', t('exp_list.all')],
            ['pending', t('exp_list.pending')],
            ['cash', t('exp_list.cash')],
            ['noinv', t('exp_list.no_invoice')],
          ] as const
        ).map(([key, label]) => (
          <TouchableOpacity
            key={key}
            style={[s.quickPill, quick === key && s.quickPillOn]}
            onPress={() => setQuick(key)}
            accessibilityRole="radio"
            accessibilityState={{ selected: quick === key }}
            testID={`quick-pill-${key}`}
          >
            <Text
              style={[s.quickPillText, quick === key && s.quickPillTextOn]}
              numberOfLines={1}
            >
              {label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      <FlatList
        data={items}
        keyExtractor={(e) => e.expense_id}
        renderItem={({ item }) => (
          <ExpenseRow expense={item} jobName={jobMap.get(item.job_id)} />
        )}
        style={s.list}
        contentContainerStyle={
          items.length === 0 ? s.listEmptyContainer : s.listContainer
        }
        onEndReached={onEndReached}
        onEndReachedThreshold={0.4}
        refreshControl={
          <RefreshControl
            refreshing={userRefreshing}
            onRefresh={onRefresh}
            tintColor="#1e293b"
          />
        }
        testID="expense-list"
        ListEmptyComponent={
          list.isLoading ? (
            <View style={s.state} testID="expense-list-loading">
              <ActivityIndicator color="#1e293b" />
              <Text style={s.stateText}>{t('common.loading')}</Text>
            </View>
          ) : list.isError ? (
            <View style={s.state} testID="expense-list-error">
              <Text style={[s.stateText, s.errorText]}>
                {t('expense_list.error')}
              </Text>
              <Pressable
                onPress={() => void list.refetch()}
                style={({ pressed }) => [
                  s.linkBtn,
                  pressed && s.linkBtnPressed,
                ]}
                accessibilityRole="button"
                testID="expense-list-retry"
              >
                <Text style={s.linkBtnText}>{t('common.retry')}</Text>
              </Pressable>
            </View>
          ) : (
            <View style={s.state} testID="expense-list-empty">
              <Text style={s.stateText}>{t('expense_list.empty')}</Text>
            </View>
          )
        }
        ListFooterComponent={
          list.isFetchingNextPage ? (
            <View style={s.footer} testID="expense-list-footer-loading">
              <ActivityIndicator color="#1e293b" />
            </View>
          ) : list.isFetchNextPageError ? (
            <Pressable
              onPress={() => void list.fetchNextPage()}
              accessibilityRole="button"
              testID="expense-list-footer-error"
              style={({ pressed }) => [s.footer, pressed && s.linkBtnPressed]}
            >
              <Text style={[s.footerText, s.errorText]}>
                {t('expense_list.error')}
              </Text>
              <Text style={s.linkBtnText}>{t('common.retry')}</Text>
            </Pressable>
          ) : items.length > 0 && !list.hasNextPage ? (
            <View style={s.footer} testID="expense-list-end">
              <Text style={s.footerText}>{t('expense_list.end')}</Text>
            </View>
          ) : null
        }
      />

      {picker ? (
        <OptionPickerModal
          visible
          title={picker.title}
          options={picker.options}
          selected={picker.selected}
          onSelect={picker.onSelect}
          onClose={() => setOpenPicker(null)}
          cancelLabel={t('common.cancel')}
        />
      ) : null}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: tokens.bg },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
    backgroundColor: tokens.surface,
  },
  backBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 4,
    minWidth: 72,
  },
  backBtnPressed: { opacity: 0.5 },
  backChevron: {
    fontSize: 28,
    color: '#1e293b',
    marginRight: 4,
    lineHeight: 28,
  },
  backLabel: { fontSize: 16, color: '#1e293b' },
  headerTitle: {
    flex: 1,
    textAlign: 'center',
    fontSize: 17,
    fontWeight: '600',
    color: '#0f172a',
  },
  headerSpacer: { width: 72 },
  quickRow: {
    flexDirection: 'row',
    gap: 8,
    paddingHorizontal: 16,
    paddingTop: 10,
    backgroundColor: tokens.surface,
  },
  quickPill: {
    paddingHorizontal: 13,
    paddingVertical: 7,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: tokens.line,
    backgroundColor: tokens.surface,
  },
  quickPillOn: { backgroundColor: tokens.ink, borderColor: tokens.ink },
  quickPillText: { fontSize: 12.5, fontWeight: '600', color: tokens.ink2 },
  quickPillTextOn: { color: '#ffffff', fontWeight: '700' },
  chipBarWrap: {
    borderBottomWidth: 1,
    borderBottomColor: '#e2e8f0',
    backgroundColor: tokens.surface,
  },
  chipBar: {
    flexDirection: 'row',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  // B3: filter chips now use src/ui/kit.tsx Chip.
  list: { flex: 1 },
  // F3: rows are self-surfaced cards; the container shows the ground.
  listContainer: { padding: 16 },
  listEmptyContainer: { flexGrow: 1, justifyContent: 'center' },
  state: { alignItems: 'center', padding: 24, gap: 12 },
  stateText: { color: '#64748b', fontSize: 15 },
  errorText: { color: '#b91c1c' },
  linkBtn: { paddingHorizontal: 12, paddingVertical: 8 },
  linkBtnPressed: { opacity: 0.5 },
  linkBtnText: { color: '#1e293b', fontSize: 15, fontWeight: '600' },
  footer: { paddingVertical: 16, alignItems: 'center' },
  footerText: { color: '#94a3b8', fontSize: 13 },
});
