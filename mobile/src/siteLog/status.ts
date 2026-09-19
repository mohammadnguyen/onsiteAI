import type { CaptureStatus } from '../api/siteLog';

/**
 * Capture status → the badge key whose TONE matches.
 *
 * StatusBadge picks its colours from a fixed table of backend enum values and
 * greys out anything it does not know. Left alone it would render all three
 * capture statuses identically, which is exactly the distinction that must not
 * be lost: a record that exists but is still uploading, one whose attachments
 * are all saved, and one that saved some and lost others.
 *
 * The visible text is always the capture status' own localized label; only the
 * colour is borrowed.
 */
export function captureStatusBadgeKey(status: CaptureStatus): string {
  switch (status) {
    case 'complete':
      return 'reviewed'; // green
    case 'partial_failed':
      return 'rejected'; // red
    default:
      return 'pending'; // amber — created, still uploading
  }
}
