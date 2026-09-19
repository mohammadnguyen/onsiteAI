/**
 * Site Log capture: the API surface, typed locally.
 *
 * Deliberately not generated from `components['schemas']` — regenerating the
 * OpenAPI types needs a running backend, and the shapes used here are small
 * and stable. They mirror backend/app/schemas/site_log.py.
 */
import { api } from './client';

export type MediaType = 'text' | 'audio' | 'image' | 'document';
export type AttachmentState = 'awaiting_upload' | 'pending' | 'stored' | 'failed';
export type CaptureStatus = 'pending_upload' | 'complete' | 'partial_failed';

export type AttachmentOut = {
  attachment_client_id: string;
  declared_media_type: MediaType;
  declared_size_bytes: number | null;
  state: AttachmentState;
  evidence_id: string | null;
};

export type RevisionOut = {
  revision_no: number;
  body_text: string | null;
  internal_location: string | null;
  occurred_at: string | null;
  withdrawn: boolean;
  created_at: string;
};

export type SiteLogEventOut = {
  site_log_event_id: string;
  capture_client_id: string;
  author_user_id: string;
  job_id: string | null;
  job_state: 'confirmed' | 'unassigned';
  capture_status: CaptureStatus;
  created_at: string;
  revision: RevisionOut;
  attachments: AttachmentOut[];
};

export type AttachmentDeclare = {
  attachment_client_id: string;
  /**
   * All four classes are declarable by the client. `text` included: the
   * server-owned inline row is reserved by its ID, not by its class, so a
   * picked .txt or .csv is an ordinary attachment that happens to be text.
   * What matters is that this MATCHES what the server derives from the
   * uploaded MIME - see src/siteLog/media.ts.
   */
  declared_media_type: MediaType;
  declared_size_bytes?: number | null;
};

/**
 * The declaration, exactly as first submitted.
 *
 * Kept verbatim in the draft and replayed byte-for-byte: the backend
 * fingerprints the declaration and answers 409 if a replay differs, which is
 * how it refuses to let a retry quietly restate what was captured.
 */
export type Declaration = {
  capture_client_id: string;
  job_id: string | null;
  occurred_at: string | null;
  internal_location: string | null;
  body_text: string | null;
  attachments: AttachmentDeclare[];
};

export async function declareCapture(d: Declaration): Promise<SiteLogEventOut> {
  const r = await api.post<SiteLogEventOut>('/site-log-events', d);
  return r.data;
}

export type LocalFile = { uri: string; name: string; mime: string };

/**
 * How long one attachment upload may take.
 *
 * The shared client's 15 seconds is right for a JSON request and far too
 * short for bytes: the backend accepts up to 25 MiB
 * (`EVIDENCE_MAX_UPLOAD_BYTES`), and a 5 MB photo needs about 40 seconds
 * on a 1 Mbit/s site connection - so every attempt, and every retry,
 * would have timed out on a link that was working.
 *
 * 180 seconds carries a 3 MB photo down to ~0.14 Mbit/s and the 25 MiB
 * worst case at ~1.2 Mbit/s. It is deliberately BOUNDED: an upload that
 * really is dead has to surface as a timeout, which this flow reads as
 * "we do not know whether it saved" and recovers from by asking the
 * server - never as "it failed", and never as a second record.
 */
export const UPLOAD_TIMEOUT_MS = 180_000;

export async function uploadAttachment(
  eventId: string,
  attachmentClientId: string,
  file: LocalFile,
): Promise<AttachmentOut> {
  const form = new FormData();
  // React Native's FormData takes this shape for a file part; the backend
  // reads a single field named `file`.
  form.append('file', {
    uri: file.uri,
    name: file.name,
    type: file.mime,
  } as unknown as Blob);
  const r = await api.put<AttachmentOut>(
    `/site-log-events/${eventId}/attachments/${attachmentClientId}`,
    form,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
      // Per request: the shared client's default is unchanged for
      // everything else.
      timeout: UPLOAD_TIMEOUT_MS,
    },
  );
  return r.data;
}

export async function finalizeCapture(eventId: string): Promise<SiteLogEventOut> {
  const r = await api.post<SiteLogEventOut>(`/site-log-events/${eventId}/finalize`);
  return r.data;
}

export async function assignJob(eventId: string, jobId: string): Promise<SiteLogEventOut> {
  const r = await api.post<SiteLogEventOut>(`/site-log-events/${eventId}/assign-job`, {
    job_id: jobId,
  });
  return r.data;
}

export async function getEvent(eventId: string): Promise<SiteLogEventOut> {
  const r = await api.get<SiteLogEventOut>(`/site-log-events/${eventId}`);
  return r.data;
}

export const MINE_PAGE_SIZE = 20;

export async function listMine(offset = 0, limit = MINE_PAGE_SIZE): Promise<SiteLogEventOut[]> {
  const r = await api.get<SiteLogEventOut[]>('/site-log-events/mine', {
    params: { offset, limit },
  });
  return r.data;
}

/**
 * Find the record this device already created, by the id it generated.
 *
 * The recovery path for a lost response: the client may not know the server's
 * event id, but it always knows its own capture_client_id.
 */
export async function findMineByCaptureClientId(
  captureClientId: string,
): Promise<SiteLogEventOut | null> {
  const r = await api.get<SiteLogEventOut[]>('/site-log-events/mine', {
    params: { capture_client_id: captureClientId },
  });
  return r.data.length > 0 ? r.data[0] : null;
}

/**
 * The server-owned inline-text row, if this event has one.
 *
 * Identified by elimination rather than by deriving the reserved id: any
 * manifest row the client did not declare belongs to the server. The client
 * must never PUT to it — the backend refuses with 422 `inline_text_reserved`,
 * and the bytes come from revision 1 regardless.
 */
export function serverOwnedAttachmentIds(
  event: SiteLogEventOut,
  declaredIds: string[],
): string[] {
  const mine = new Set(declaredIds);
  return event.attachments
    .filter((a) => !mine.has(a.attachment_client_id))
    .map((a) => a.attachment_client_id);
}
