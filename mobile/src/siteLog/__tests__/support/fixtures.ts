import { AxiosError } from 'axios';
import type {
  AttachmentOut,
  AttachmentState,
  CaptureStatus,
  SiteLogEventOut,
} from '../../../api/siteLog';
import type { DraftAttachment, SiteLogDraft } from '../../../store/siteLogDrafts';

export const USER = 'user-a';
export const CAPTURE = 'capture-1';
export const EVENT_ID = 'event-1';
/** The server's own inline-text row: an id the client never declared. */
export const INLINE_ID = 'inline-row-1';

export function attachment(over: Partial<DraftAttachment> = {}): DraftAttachment {
  const id = over.attachment_client_id ?? 'att-1';
  const path = `site-log/${USER}/${CAPTURE}/${id}.jpg`;
  return {
    attachment_client_id: id,
    media_type: 'image',
    // What this build writes: the absolute uri AND the path under the
    // document directory it was resolved from. A test that wants a draft
    // from the previous build passes `path: undefined` explicitly.
    uri: `file:///documents/${path}`,
    path,
    name: `${id}.jpg`,
    mime: 'image/jpeg',
    size: 10,
    status: 'awaiting_upload',
    retained: true,
    ...over,
  };
}

export function draft(over: Partial<SiteLogDraft> = {}): SiteLogDraft {
  return {
    capture_client_id: CAPTURE,
    user_id: USER,
    created_at: 1,
    updated_at: 1,
    declaration: null,
    body_text: 'Poured 12m3 bay 3',
    job_id: null,
    attachments: [attachment()],
    server: null,
    unconfirmed: false,
    last_message: null,
    ...over,
  };
}

export function serverAttachment(
  id: string,
  state: AttachmentState,
  over: Partial<AttachmentOut> = {},
): AttachmentOut {
  return {
    attachment_client_id: id,
    declared_media_type: id === INLINE_ID ? 'text' : 'image',
    declared_size_bytes: 10,
    state,
    evidence_id: state === 'stored' ? `ev-${id}` : null,
    ...over,
  };
}

export function serverEvent(
  attachments: AttachmentOut[],
  status: CaptureStatus = 'pending_upload',
): SiteLogEventOut {
  return {
    site_log_event_id: EVENT_ID,
    capture_client_id: CAPTURE,
    author_user_id: USER,
    job_id: null,
    job_state: 'unassigned',
    capture_status: status,
    created_at: '2026-09-19T00:00:00Z',
    revision: {
      revision_no: 1,
      body_text: 'Poured 12m3 bay 3',
      internal_location: null,
      occurred_at: null,
      withdrawn: false,
      created_at: '2026-09-19T00:00:00Z',
    },
    attachments,
  };
}

/** A transport failure: the request never completed, so nothing is known. */
export function timeoutError(): AxiosError {
  return new AxiosError('timeout of 15000ms exceeded', 'ECONNABORTED');
}

export function offlineError(): AxiosError {
  return new AxiosError('Network Error', 'ERR_NETWORK');
}

/** An HTTP answer: the server decided. */
export function httpError(status: number, detail?: unknown): AxiosError {
  const err = new AxiosError(`Request failed with status code ${status}`);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  err.response = { status, data: { detail }, statusText: '', headers: {}, config: {} as any };
  return err;
}
