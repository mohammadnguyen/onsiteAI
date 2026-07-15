import type { TFunction } from 'i18next';

/**
 * Map the backend's job-status enum ("active" / "completed") to a
 * translated label. Unknown / future statuses fall back to the raw
 * value rather than rendering a raw i18n key string (C-04 discipline).
 * Shared by the Jobs list row and the job-details page (B4).
 */
export function localizeJobStatus(status: string, t: TFunction): string {
  switch (status) {
    case 'active':
      return t('job.status_active');
    case 'completed':
      return t('job.status_completed');
    default:
      return status;
  }
}
