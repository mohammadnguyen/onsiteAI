# Mobile dependency map (Phase 0 — audit topology)

Generated read-only for the mobile code audit. HEAD: cf78938 (main+L-E1).

## Routes (app/**)
app/(auth)/_layout.tsx
app/(auth)/login.tsx
app/(tabs)/_layout.tsx
app/(tabs)/expenses.tsx
app/(tabs)/jobs.tsx
app/(tabs)/labour.tsx
app/(tabs)/settings.tsx
app/_layout.tsx
app/expenses/[id].tsx
app/expenses/[id]/edit.tsx
app/expenses/list.tsx
app/export.tsx
app/index.tsx
app/jobs/[id]/edit.tsx
app/labour/records.tsx
app/labour/summary.tsx
app/labour/workers.tsx
app/review-queue.tsx
app/users/index.tsx
app/users/new.tsx

## Hooks (src/api/hooks/**)
src/api/hooks/useAuth.ts
src/api/hooks/useCategories.ts
src/api/hooks/useExpenses.ts
src/api/hooks/useJobs.ts
src/api/hooks/useLabour.ts
src/api/hooks/useReviewQueue.ts
src/api/hooks/useSuppliers.ts
src/api/hooks/useUsers.ts

## Stores / core
src/api/client.ts
src/api/errors.ts
src/api/reports.ts
src/api/types.ts
src/store/auth.ts
src/store/expenseListFilters.ts
src/store/failures.ts
src/store/fontScale.ts
src/store/labourEditTarget.ts
src/store/selectedJob.ts
src/i18n/index.ts
src/i18n/storage.ts
src/ui/type.ts
src/util/category.ts
src/util/dates.ts
src/util/format.ts
src/util/text.ts
src/util/time.ts

## Components
src/components/CaptureResultCard.tsx
src/components/DatePills.tsx
src/components/ExpenseRow.tsx
src/components/JobPickerSheet.tsx
src/components/NewJobModal.tsx
src/components/OptionPickerModal.tsx
src/components/RecentCapturesList.tsx
src/components/RecentFailuresList.tsx
src/components/ReviewCorrectionsSheet.tsx
src/components/WorkerChecklist.tsx

## Import edges: route -> src (hook/store/client/util)
- app/(auth)/_layout.tsx -> 
- app/(auth)/login.tsx -> src/api/client src/api/hooks/useAuth src/store/auth 
- app/(tabs)/_layout.tsx -> 
- app/(tabs)/expenses.tsx -> src/api/errors src/api/hooks/useAuth src/api/hooks/useExpenses src/api/hooks/useJobs src/components/CaptureResultCard src/components/DatePills src/components/JobPickerSheet src/components/RecentCapturesList src/components/RecentFailuresList src/store/failures src/ui/type src/util/dates src/util/text 
- app/(tabs)/jobs.tsx -> src/api/hooks/useAuth src/api/hooks/useExpenses src/api/hooks/useJobs src/api/hooks/useLabour src/api/hooks/useReviewQueue src/components/NewJobModal src/components/RecentCapturesList src/store/selectedJob src/ui/type src/util/dates src/util/format 
- app/(tabs)/labour.tsx -> src/api/errors src/api/hooks/useAuth src/api/hooks/useJobs src/api/hooks/useLabour src/components/DatePills src/components/OptionPickerModal src/components/WorkerChecklist src/store/labourEditTarget src/util/dates src/util/format src/util/time 
- app/(tabs)/settings.tsx -> src/api/client src/api/hooks/useAuth src/i18n src/store/auth src/store/fontScale src/ui/type 
- app/_layout.tsx -> src/i18n src/store/auth src/store/failures src/store/fontScale 
- app/expenses/[id].tsx -> src/api/hooks/useAuth src/api/hooks/useExpenses src/api/hooks/useJobs src/components/ReviewCorrectionsSheet src/store/selectedJob src/ui/type src/util/category src/util/dates src/util/format 
- app/expenses/[id]/edit.tsx -> src/api/hooks/useAuth src/api/hooks/useExpenses src/components/DatePills 
- app/expenses/list.tsx -> src/api/hooks/useCategories src/api/hooks/useExpenses src/api/hooks/useJobs src/api/hooks/useSuppliers src/components/ExpenseRow src/components/OptionPickerModal src/store/expenseListFilters src/util/category 
- app/export.tsx -> src/api/hooks/useAuth src/api/reports src/util/dates 
- app/index.tsx -> src/store/auth 
- app/jobs/[id]/edit.tsx -> src/api/errors src/api/hooks/useCategories src/api/hooks/useJobs src/util/category src/util/format 
- app/labour/records.tsx -> src/api/hooks/useAuth src/api/hooks/useJobs src/api/hooks/useLabour src/components/OptionPickerModal src/i18n src/store/labourEditTarget src/ui/type src/util/dates src/util/time 
- app/labour/summary.tsx -> src/api/hooks/useAuth src/api/hooks/useJobs src/api/hooks/useLabour src/components/OptionPickerModal src/ui/type src/util/dates src/util/format 
- app/labour/workers.tsx -> src/api/hooks/useAuth src/api/hooks/useLabour src/util/format 
- app/review-queue.tsx -> src/api/hooks/useExpenses src/api/hooks/useJobs src/api/hooks/useReviewQueue src/util/dates src/util/format 
- app/users/index.tsx -> src/api/hooks/useAuth src/api/hooks/useUsers 
- app/users/new.tsx -> src/api/hooks/useUsers 

## Hook -> client/store edges
- src/api/hooks/useAuth.ts -> ../../store/auth ../client ../types 
- src/api/hooks/useCategories.ts -> ../../store/auth ../client ../types 
- src/api/hooks/useExpenses.ts -> ../../store/auth ../client ../types 
- src/api/hooks/useJobs.ts -> ../../store/auth ../../util/text ../client ../types 
- src/api/hooks/useLabour.ts -> ../../store/auth ../client ../types axios 
- src/api/hooks/useReviewQueue.ts -> ../../store/auth ../client ../types ./useExpenses 
- src/api/hooks/useSuppliers.ts -> ../../store/auth ../client ../types 
- src/api/hooks/useUsers.ts -> ../../store/auth ../client ../types 
