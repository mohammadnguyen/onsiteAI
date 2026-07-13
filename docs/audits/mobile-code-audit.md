# Mobile Code Audit — Maximum Depth (2026-07-03)

Read-only audit of `mobile/` at HEAD `cf78938` (main + L-E1). Protocol: Phase 0 ground truth →
4 parallel layer specialists (every file in layer read line-by-line) → adversarial red-team
cross-examination (every cited line re-opened; kills/demotions recorded) → this synthesis.
Contract set: `CLAUDE.md`, `docs/patterns/mobile-screen-pattern.md`, `docs/mobile-roadmap.md`.
**`AGENTS.md` does not exist in this repo** — noted per protocol, not a finding.
Topology used by all agents: `docs/audits/_map.md`.

Severities are POST-red-team. Confidence: Certain / Likely / Needs-runtime-check.
`[CONTRACT-TENSION]` = device-side money *display* math explicitly operator-approved (F1/F2/O2-A);
recorded as tension, not violation, per adjudication.

---

## 1. Verbatim typecheck result (Phase 0, executed)

```
$ cd mobile && npm install --no-audit --no-fund 2>&1 | tail -5
up to date in 2s

$ npm run typecheck
> mobile@0.1.0 typecheck
> tsc --noEmit
(exit 0 — no output, zero errors)
```

**Zero Blockers from Phase 0.** `package.json` / `package-lock.json` verified untouched after install.

---

## 2. Executive verdict (red team, verbatim — no hedging)

This codebase is well above the quality bar for its stage: token custody, the 401 single-flight
machine, four-state screens, i18n parity, list virtualization, and conditional-spread write bodies
are all genuinely clean, and in-code documentation of operator decisions is the best I have
red-teamed in a project this size. Its two structural weaknesses are session-boundary hygiene
(one shared QueryClient, persisted stores, and module singletons that all survive logout —
B-02/B-05/B-06/C-19 are one missing `resetSession()` helper) and a freshness model that relies
solely on mutation invalidation, which fails exactly where invalidation cannot reach: same-device
missed cells (A2), other devices (X-2), and transport failures during refresh (A1). The High set
that must land before production is small and surgical: A1's conditional catch, B-02/A5's cache
purge, A2's one-line invalidation, and X-1's DatePills flip — under 40 lines combined, all
reversible. The screens layer's residual risk is robustness-to-backend-evolution (the 4-site
enum-crash class and raw-key fallbacks), not present-day correctness, and the money-visibility
posture holds server-side everywhere it matters. Nothing found undermines the architecture
itself; ship the fixes above and this mobile app is operationally trustworthy for the
single-tenant field deployment it targets.

---

## 3. Master table (severity, then confidence)

| ID | Layer | File:Line | Finding (one line) | Sev | Conf |
|---|---|---|---|---|---|
| A1 | API | src/api/client.ts:128-133 | Refresh catch clears BOTH tokens on ANY error — a network blip during refresh destroys a valid 30-day refresh token (forced logout in the product's core weak-network environment) | **High** | Certain |
| A2 | API | src/api/hooks/useExpenses.ts:42-47 | useCreateExpense never invalidates `['jobs']` though pending expenses count into the embedded JobSummary; Jobs tab has no focus-refetch/RefreshControl → spent totals + pressure ranking stale indefinitely after capture | **High** | Certain |
| B-02 | State | settings.tsx:46-54 + _layout.tsx:23 (merged A5) | QueryClient never cleared on logout — next user on a shared device is served the previous user's cached role + money payloads | **High** | Certain |
| C-04 | Screens | expenses/[id].tsx:562/430/467 + CaptureResultCard.tsx:83-91 + ExpenseRow.tsx:50/71 | Unguarded `REASON_COLORS[code]`/`STATUS_COLORS[...]` lookups at 4 sites — ONE new backend enum value crashes capture result, every list row, and the detail screen (expanded to High as a class by red team) | **High** | Certain |
| X-1 | Cross | DatePills.tsx:148-162 × labour.tsx:97-106 × records.tsx:114-119 | Records→labour edit handoff sets a past date while DatePills' sync effect has no else-branch → the **"Today" pill stays lit while the checklist saves a past date**; the date shows nowhere else on the screen | **High** | Certain/Likely |
| A4 | API | client.ts:126 + store/auth.ts:75-78 | Refresh resolving after logout unconditionally setAccessToken → resurrects a live token post-logout | Med | Likely |
| B-01 | State | _layout.tsx:166 + auth.ts:67-70 | Unguarded `await hydrate()` — a SecureStore read rejection strands that launch on the splash spinner (demoted from High: relaunch retries) | Med | Likely |
| B-04 | State | labour.tsx:97-134 + records.tsx:114-119 | Edit-day handoff for an archived job's entry silently rebound to a different active job by the selection-repair effect — attendance saveable against the wrong job (aggravated by X-1) | Med | Likely |
| B-05 | State | store/failures.ts:54-81 | Persisted failed-capture texts (amounts/suppliers) never cleared on logout — visible to the next user | Med | Certain |
| C-01 | Screens | labour.tsx:494 (merged D2) | Labour form with per-row time TextInputs has no KeyboardAvoidingView (clause 5) — demoted from High (manual scroll recovery exists) | Med | Certain |
| C-03 | Screens | export.tsx:192-199 | `me.isError` conflated with "forbidden": weak-network admin told "admins only", no retry (clause 4) | Med | Certain |
| C-05 | Screens | jobs.tsx:620 + jobs/[id]/edit.tsx | Job Edit money surface not role-gated client-side; contributors can open it (server strips values — posture/structure breach, not a live leak) | Med | Certain |
| C-09 | Screens | jobs.tsx:472,712 | SpendingSection hiding relies solely on server 403; non-403 errors render the "Budgets & spending" header to contributors (violates the file's own no-placeholder rule) + guaranteed-403 request per contributor modal open | Med | Certain |
| C-11 | Screens | jobs/[id]/edit.tsx:617-990, export.tsx, settings.tsx +… | Missing accessibilityLabel on form controls/buttons (clause 7), worst on the densest money form | Med | Certain |
| C-13 | Screens | 9 route files > 250 lines (max 1330) (merged D1) | File-discipline breach: CLAUDE.md ~250 soft limit + clause-11 150-line subcomponent rule (JobDetailModal ~330, DetailBody ~232 in-route) | Med | Certain |
| D4 | Perf | ReviewCorrectionsSheet.tsx:193-246 | Bottom-anchored sheet's supplier quick-create input sits in the keyboard-occluded zone, no KAV (clause 5) | Med | Likely |
| D5 | Perf | expenses/[id]/edit.tsx:70-86 + users/new.tsx:46-60 | Two pre-O2B screens still return raw axios `error.message` — untranslated "Network Error"/"timeout of 15000ms exceeded" reaches zh users (clause 2) | Med | Certain |
| X-2 | Cross | _layout.tsx:23-30 × jobs.tsx:230-251 | No AppState→focusManager wiring anywhere + Jobs is the only data tab without RefreshControl → admin money surfaces can never learn of other devices' captures nor recover by gesture (systemic root under A2) | Med (High multi-user) | Certain/Likely |
| X-3 | Cross | expenses.tsx:238-241,528-534 | Capture submit lacks the synchronous savingRef double-tap guard its labour sibling documents as necessary — parallel double-POST races past duplicate detection → double-counted spend on the primary money flow | Med | Certain/NRC |
| A3 | API | useExpenses.ts:297-302 | useUpdateExpense misses `['jobs']` — demoted to Low: route unmount + staleTime-0 self-heals; latent asymmetry | Low | Certain |
| A6 | API | useLabour.ts:103-107,232-238 | Labour save/delete miss `['labour-rollup']` — demoted to Low: only observer is enabled-gated in a modal that must close first; the stale window doesn't exist today | Low | Certain |
| A7 | API | useJobs.ts:156-159 | Job rename doesn't invalidate labour summary/rollup (embed job_name) → stale names on mounted labour screens | Low | Certain |
| A8 | API | errors.ts:78-80 | English-only backend `detail` shown verbatim in zh UI `[CONTRACT-TENSION: established convention]` | Low | Certain |
| A9 | API | reports.ts:92 | Export buildUrl string-concat breaks on trailing-slash apiUrl (axios normalises, export doesn't) → export-only 404 | Low | Likely |
| A10 | API | reports.ts:113 | Fixed per-day export filename — second same-day export overwrites the first, possibly while the share sheet reads it | Low | Certain |
| A11 | API | client.ts:114-137 | Stale-401 after a completed refresh triggers a redundant second refresh (harmless today; a trap if rotation is added) | Low | Certain |
| A12 | API | client.ts:34-41,122-125 | Refresh POST carries the expired Bearer header (backend verified to ignore it) | Low | Certain |
| A13 | API | reports.ts:170-171 | 200-only success check could share a truncated xlsx on mid-body disconnect | Low | NRC |
| B-06 | State | selectedJob/expenseListFilters/labourEditTarget + labour.tsx:64 (merged C-19) | No in-memory store reset on logout — stale cross-user residue (Jobs modal auto-reopens previous session's job; module-level lastUsedJobId) | Low | Certain |
| B-07 | State | app/index.tsx:5 | Only selector-less whole-store zustand subscription in the app | Low | Certain |
| B-08 | State | failures.ts:52,58-68 | Entry count capped (20) but per-entry byte size unbounded | Low | Certain |
| B-09 | State | _layout.tsx:59-75 + failures.ts:67 | Uncaught-app-error records share the 20-cap list and can evict the failed-capture texts the store exists to preserve | Low | Certain |
| B-10 | State | i18n/storage.ts + fontScale.ts vs failures.ts | Non-secret prefs in SecureStore, contradicting the codebase's own "SecureStore reserved for tokens" doc | Low | Certain |
| B-11 | State | client.ts:13-16 | No https enforcement on resolved apiUrl (ATS mitigates on iOS) | Low | Certain |
| B-12 | State | auth.ts:20,31,43 | [INFO] iOS keychain: tokens survive uninstall/reinstall → silent auto-login on a device changing hands | Low | Certain |
| B-13 | State | _layout.tsx:89-99,121-133 | Root ErrorBoundary hardcoded English + raw error.message `[CONTRACT-TENSION: documented pre-i18n crash path]` | Low | Certain |
| C-02 | Screens | export.tsx:201 (merged D3) | Export form lacks KAV — inputs sit top-of-screen (demoted) | Low | Certain |
| C-06 | Screens | jobs/[id]/edit.tsx:326-331 | Device computes canonical contract ex-GST on the WRITE path — reframed Low: format.ts:54-61 documents this as the approved F2 design (±1c drift accepted, Q3) `[CONTRACT-TENSION]` | Low | Certain |
| C-08 | Screens | jobs.tsx:291-296 (merged D7) | Month-spend card device-sums a paged endpoint (≤500 rows) — documented-deliberate cap; add a partial-marker when next_cursor present | Low | Certain |
| C-10 | Screens | ~12 files | Systemic raw `Pressable` where design doesn't diverge (clause 8) — recommend amending the pattern doc rather than mass edits | Low | Certain |
| C-12 | Screens | jobs.tsx:399,886 | JobRow + labour-range chips missing accessibilityRole/Label | Low | Certain |
| C-14 | Screens | login.tsx:38, labour.tsx:320, +2 | Raw English server/axios strings on 4 legacy paths (overlaps D5 for two files) | Low | Certain |
| C-15 | Screens | review-queue.tsx:246, expenses/[id].tsx:469, list.tsx:194 | Unknown backend enums render literal i18n keys (no fallback pattern) | Low | Certain |
| C-16 | Screens | users/index.tsx:267 | Hardcoded fullwidth `（）` punctuation outside i18n (clause 2) | Low | Certain |
| C-17 | Screens | jobs/[id]/edit.tsx:796 | Alias empty state reuses `jobs.empty` ("No jobs yet") — wrong copy | Low | Certain |
| C-18 | Screens | list.tsx:66-86, summary.tsx:53-67 (merged D12) | Local duplicates of util/dates helpers — drift risk | Low | Certain |
| C-20 | Screens | expenses.tsx:424 + records.tsx:84 | Jobs-query failure silently removes chips/picker (clause-4 edge) | Low | Certain |
| C-21 | Screens | login.tsx:45; expenses.tsx:657,690 (merged D9, D16.1) | login SafeAreaView without edges; multi-capture card hand-rolls `$${x.toFixed(2)}` bypassing formatMoney (`$NaN` possible) | Low | Certain |
| C-22 | Screens | expenses/[id].tsx:86-90 + both edit routes | useLocalSearchParams string[] never normalized; array `jobId` would poison the selected-job store | Low | NRC |
| C-23 | Screens | expenses/[id].tsx:133-136 (merged D16.3) | 900ms setTimeout(onBack) without unmount cleanup → possible double navigation | Low | NRC |
| C-24 | Screens | settings.tsx:79-85 | No useMe error state — failed identity shows `-`, silently hides admin entries, no retry | Low | Certain |
| D6 | Perf | labour.tsx:149-203 + WorkerChecklist.tsx:70 | Every time-field keystroke re-renders the whole roster (no memoized row) — fine ≤30 workers `[BEYOND-CONTRACT]` | Low | Certain |
| D10 | Perf | summary.tsx:325,390,427 | Hours rendered via formatDays (9.25h → "9.3") while records uses formatHoursShort — the two screens won't reconcile | Low | Certain |
| D11 | Perf | i18n both locales | 17 unreferenced keys: 8 deliberately-kept dashboard.* + 9 genuinely dead (list in layer report section) | Low | Certain |
| D13 | Perf | records.tsx:84-87 | O(n) .find per rendered row for names; house Map pattern exists in review-queue | Low | Certain |
| D14 | Perf | ExpenseRow/RecentCapturesList/WorkerChecklist/DatePills +5 | Font scaling inconsistent WITHIN covered screens — headings scale, embedded rows don't `[BEYOND-CONTRACT; v1 partial coverage documented]` | Low | Certain |
| D15 | Perf | src/ui/type.ts:31-64 | Per-instance stylesheet copies at scale≠1; suggest a (base,scale) cache `[BEYOND-CONTRACT]` | Low | Certain |
| D16 | Perf | misc (5 residual items) | 'Menlo' not platform-gated; inline separator/renderItem + unmemoized JobRow; O(n²) chip ordering; stale "today" across midnight (DatePills mitigates on re-render); doc-comment drift (X-6: edit.tsx:69-73 says margin/budgets "NOT in v1") | Low | Certain/Likely |

**Passes worth stating (audited, clean):** i18n EN↔ZH key parity exact (447 = 447, empty diff both
directions — command executed in layer D report); NO unbounded ScrollView+.map anywhere (full
20-item inventory — every .map is capped by hook limit, store cap, or domain size);
`useInfiniteQuery` is NOT dead — cursor pagination fully wired in expenses/list.tsx with an
error-gated onEndReached guard; zero TODO/FIXME/HACK markers; four-UI-states discipline strong on
all list screens; O1-S1 contributor ex-GST/GST gating confirmed present; admin gates fail closed
on summary/workers/users/export; token custody proven (headers-only attachment, refresh token in
POST body only, zero `console.*` in mobile/, SecureStore native / localStorage web).

---

## 4. Findings by layer with diff-shaped fix sketches

### Layer A — API & transport (`src/api/**`)

**A1 (High, Certain)** — `client.ts:128-133`. The single-flight refresh promise's catch treats
*every* failure as auth-fatal and clears both tokens; a transport failure (timeout/offline — the
product's normal environment) must not destroy a valid 30-day refresh token.
```diff
   } catch (err) {
-    await useAuthStore.getState().clear();
-    throw err;
+    // Only a REJECTED refresh (4xx) is auth-fatal. Transport failures
+    // (timeout/offline) keep the tokens; the request fails and the
+    // next 401 retries the refresh.
+    if (axios.isAxiosError(err) && err.response && err.response.status >= 400 && err.response.status < 500) {
+      await useAuthStore.getState().clear();
+    }
+    throw err;
   }
```

**A2 (High, Certain)** — `useExpenses.ts:42-47`. Red team re-verified the full chain
(backend `budget_summary.py:11-17` counts pending; `types.ts:1664` embeds summary on JobPublic;
Jobs tab has no RefreshControl; capture fires inside the tabs group so the Slot-remount self-heal
never triggers). One line:
```diff
   onSuccess: () => {
     void qc.invalidateQueries({ queryKey: ['expenses'] });
     void qc.invalidateQueries({ queryKey: ['review-queue'] });
+    void qc.invalidateQueries({ queryKey: ['jobs'] });   // embedded JobSummary counts pending spend
   },
```
Same one-liner for A3 (`useUpdateExpense`, demoted Low) and the labour-rollup root for A6 (Low).

**A4 (Med, Likely)** — `client.ts:126` + `auth.ts:75-78`. Guard the post-refresh commit:
```diff
-  await useAuthStore.getState().setAccessToken(fresh);
+  if (useAuthStore.getState().accessToken !== null) {   // logged out mid-refresh → drop it
+    await useAuthStore.getState().setAccessToken(fresh);
+  }
```
A7/A9/A10/A11/A12/A13 (Low): one-line invalidation add; `new URL()`-based join; timestamped export
filename; refresh-generation counter; strip Bearer on the refresh POST; add downloaded-size sanity
check. Sketches in the same shapes as above.

**Admin-mirror drift (informational, out of mobile scope):** admin useCreateExpense misses
`['review-queue']`; NO admin expense/review mutation invalidates `['jobs']` (broader than A2/A3);
admin export has no timeout; admin error fallbacks show raw English. Recorded for a future admin
audit — not mobile findings.

### Layer B — State & security (`src/store/**`, auth flows)

**B-02 (High, Certain — absorbs A5)** — logout leaves the QueryClient, persisted failures store,
and module singletons intact. One `resetSession()` fixes B-02, B-05, B-06 and C-19 together:
```diff
 // settings.tsx onLogout (and the auth-failure logout path in client.ts)
   await clear();
+  qc.clear();                                   // B-02: purge cached role + money payloads
+  useFailuresStore.getState().clearAll();       // B-05: captured amounts/suppliers
+  useSelectedJobStore.getState().setSelectedJobId(null);   // B-06
+  useLabourEditTargetStore.getState().clear();  // B-06
+  resetLastUsedJobId();                         // C-19: export a resetter from labour.tsx
   router.replace('/(auth)/login');
```

**B-01 (Med, Likely)** — `_layout.tsx:166`:
```diff
-      await useFontScaleStore.getState().hydrate();
-      await hydrate();
+      try { await useFontScaleStore.getState().hydrate(); } catch {}
+      try { await hydrate(); } catch {}   // a keychain read failure must not strand the splash
       setReady(true);
```
B-04 (Med): when consuming labourEditTarget, if the target job is not in activeJobs, surface a
localized "job is archived" notice instead of letting the repair effect rebind silently.
B-08/B-09/B-10/B-11 (Low): truncate persisted texts (~500 chars); separate caps for app-error vs
capture entries; migrate prefs to AsyncStorage-class storage; assert https non-dev.

**Refuted by proof (no findings):** pre-hydration query firing (Slot render-gated + 14
`enabled: !!accessToken` gates); token leakage to storage/logs/URLs/keys; fontScale flash;
labourEditTarget stale closure; setState-during-render.

### Layer C — Screens & conformance (`app/**`)

Full per-screen scorecard (18 screens × 8 contract clauses) retained in the layer detail —
headline: four-states, testID coverage, and i18n discipline broadly hold; the recurring ✗ columns
are KeyboardAvoidingView (2 real cases), accessibilityLabel on dense forms, and file size.

**C-04 (High-as-class, Certain — expanded by red team to 4 sites)** — colour-map lookups crash on
any unknown backend enum:
```diff
-  const colors = REASON_COLORS[code];
+  const colors = REASON_COLORS[code] ?? { bg: '#f1f5f9', fg: '#475569' };
```
Apply the `?? fallback` at expenses/[id].tsx:562 (+430/467 STATUS_COLORS), CaptureResultCard:83-91,
ExpenseRow:50/71 — a new enum then degrades to a grey chip instead of crashing capture + lists + detail.

**C-01 (Med)** — wrap the labour scroll content in `KeyboardAvoidingView behavior='padding'`
(same shape as login.tsx). **C-03 (Med)** — split `me.isError` from `!isAdmin` in export.tsx with
retry. **C-05/C-09 (Med)** — add the client `isAdmin` gate on the Edit button + SpendingSection
render (server strip stays authoritative; this restores the double-gate posture and kills the
guaranteed-403 request). **C-11/C-12 (Med/Low)** — accessibilityLabel pass on the job-edit form,
export, settings controls. **C-13 (Med)** — extraction plan (no behaviour change): JobDetailModal
+ DetailBody + MarginSection + SpendingSection out of jobs.tsx; category-budget block out of
jobs/[id]/edit.tsx; MultiCaptureResultCard out of expenses.tsx. Low items C-14→C-24: one-line
fixes as tabled (route legacy handlers through resolveApiErrorMessage; enum-label fallback helper;
i18n the fullwidth parens; alias empty-state key; delete local date-helper duplicates; surface
jobs-query failure over the chip row; add edges to login SafeAreaView; formatMoney in the
multi-capture card; normalize `[id]` params via `Array.isArray(x) ? x[0] : x`; clear the 900ms
timer in a useEffect cleanup; add a settings error state).

### Layer D — Performance & hygiene

**D4 (Med)** — ReviewCorrectionsSheet: KAV-wrap the sheet body (supplier quick-create input is
keyboard-occluded on small phones). **D5 (Med)** — replace both legacy `extractErrorMessage`
bodies with `resolveApiErrorMessage(err, t, fallback)` (the O2-B pattern; fix-safety verified by
red team). Lows as tabled: D6 memoized checklist row; D9 formatMoney in multi-card; D10 switch
summary hours to formatHoursShort; D11 delete the 9 dead keys (keep dashboard.*); D12/C-18 dedupe
date helpers; D13 Map lookups in records; D14 extend useScaledStyles to the 9 embedded components;
D15 (base,scale) cache; D16 residuals.

**Executed checks (paste-worthy):** i18n key-set diff EN↔ZH → **empty delta both directions
(447 keys each)**. ScrollView+.map inventory → 20 sites, **all bounded** (hook `limit`, store cap
20, roster/domain size) — no unbounded-list finding. `useInfiniteQuery` → **wired and live** in
expenses/list.tsx (cursor pagination + error-gated onEndReached). TODO/FIXME/HACK → **zero**.

### Cross-layer (red team)

**X-1 (High)** — records→labour handoff: DatePills' external-value sync effect
(`DatePills.tsx:148-162`) has no else-branch, so an externally-set past date leaves the **Today
pill highlighted while the tick screen edits and saves that past date**, and the actual date
renders nowhere else on the screen. Fix (verified safe for the capture/edit consumers):
```diff
   } else if (mode === 'custom') {
     setCustomText(formatDateAU(value));
     setCustomError(null);
+  } else {
+    // Externally-driven date (e.g. records → edit-day handoff): reflect it.
+    setMode('custom');
+    setCustomText(formatDateAU(value));
+    setCustomError(null);
   }
```
**X-2 (Med; High if multi-device)** — add the house RefreshControl to the Jobs tab list + 3-line
`AppState`→`focusManager.setFocused` wiring in `_layout.tsx` so resume refetches money surfaces.
**X-3 (Med)** — copy labour.tsx's synchronous `savingRef` double-tap guard into the capture
submit (single + multi paths) — parallel double-POSTs currently evade the backend duplicate flag.
**X-6 (Low)** — fix the stale "NOT in v1" doc-comment in jobs/[id]/edit.tsx:69-73.

---

## 5. Top 5 by risk-reduction-per-line-changed

| # | Fix | Lines | Kills |
|---|---|---|---|
| 1 | A2: add `['jobs']` invalidation to useCreateExpense | 1 | Stale money totals + wrong pressure ranking on the admin's primary screen after every capture |
| 2 | A1: conditional catch in the refresh single-flight | ~6 | Spurious forced logouts (token destruction) on weak networks — the product's daily condition |
| 3 | B-02: `resetSession()` on logout (cache + stores + singletons) | ~8 | Cross-user data exposure class on shared devices (role, money, captured texts, stale modals) |
| 4 | C-04: `?? fallback` on 4 colour-map lookups | ~8 | Whole-screen crash class on any future backend enum addition (capture result, rows, detail) |
| 5 | X-1: DatePills else-branch | ~7 | Attendance written to a date the UI actively misrepresents as "Today" |

(≈30 lines total for the entire High set + X-3's ~5-line guard as the next-best add.)

## 6. Red-team appendix — error correction record

**Killed:**
- B-03 — duplicate of A1 (same lines, same edge); merged.
- C-07 / D8 as violations — F1 current-margin math is operator-approved, shipped, display-only
  (verified); retained only as `[CONTRACT-TENSION]` notes.
- Hunted-and-refuted: pre-hydration query firing (R-1), token leaks (R-2), fontScale flash (R-3),
  labourEditTarget stale-closure (R-4), setState-in-render (R-5), scaleStyles renderItem identity
  churn (per-instance useMemo keeps identity stable), monthEnd future-`to` illegality (backend
  validates writes only — reads legal), export calendar with invalid typed text (defaults to
  today by design). No finding re-flagged a KNOWN-GOOD as absent — the kill rule fired zero times,
  and the Phase-0 known-goods were re-verified clean (15s timeout, single-flight terminals, token
  custody, export error-body deletion).

**Demoted:** A3 High→Low (route-remount self-heal); A6 Med→Low (no live observer window);
B-01 High→Med (single-launch impact); C-01 High→Med (manual scroll recovery); C-02+D3 Med→Low
(inputs top-of-screen); C-06 Med→Low tension (write-path conversion IS the documented F2 design);
C-08+D7 Med→Low (documented deliberate cap).

**Merged duplicates:** A5=B-02; D2=C-01; D3=C-02; C-23=D16.3; D1=C-13; D9⊂C-21; C-19⊂B-06;
X-4→C-04 expansion; X-5→C-09 strengthening.

**Upgraded by re-examination:** C-04 Med→High-as-class (3 additional crash sites found);
C-09 strengthened (header renders to contributors on non-403 errors); A2 strengthened (in-screen
inconsistency: month card updates while rows don't).

**3 worst bugs no specialist caught (mandated hunt):** X-1 (the "Today" lie on an attendance
write surface), X-2 (no resume/focus/pull refresh on the admin money tab — invalidation fixes
alone can't reach other devices), X-3 (double-tap duplicate capture on the money-creating flow).

---
*Phase artifacts: `_map.md` retained (topology). Per-subagent and red-team intermediates deleted
after this merge per protocol. No source file was modified. No fixes applied — awaiting item-by-item
approval.*
