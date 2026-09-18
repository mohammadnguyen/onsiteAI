// SDK 54 moved the classic file API behind /legacy; the new Paths/File
// surface is not needed here and this keeps the call sites unchanged.
import * as FileSystem from 'expo-file-system/legacy';
import { classifyApiError } from '../api/errors';
import {
  declareCapture,
  finalizeCapture,
  findMineByCaptureClientId,
  getEvent,
  serverOwnedAttachmentIds,
  uploadAttachment,
} from '../api/siteLog';
import type { AttachmentOut, Declaration, SiteLogEventOut } from '../api/siteLog';
import type { DraftAttachment, SiteLogDraft } from '../store/siteLogDrafts';

/**
 * Running one capture to the server, safely enough to retry.
 *
 * The rules this file exists to keep, each learned from how the backend
 * actually behaves rather than assumed:
 *
 *  1. IDS ARE STABLE. The capture_client_id and every attachment id are
 *     generated once, persisted, and replayed. A retry never mints new ones,
 *     because new ids mean a second record.
 *  2. A TIMEOUT IS NOT A FAILURE TO SAVE. It says the answer was lost, not
 *     that nothing happened. Every attempt therefore begins by asking the
 *     server what it already has.
 *  3. A 502 CAN CARRY STATE. The declare route answers 502 when the server's
 *     own inline upload failed - and its body is the event. The event id and
 *     the attachment states in it are kept, not discarded.
 *  4. THE INLINE ROW IS THE SERVER'S. It is never uploaded to; the backend
 *     refuses (422 inline_text_reserved) and sources the bytes from revision 1.
 *     Recovering it means replaying the declare, not PUTting.
 *  5. STATE DECIDES. `stored` is never re-uploaded. `pending` is never reset
 *     and never hammered - the client cannot clear it (reset is admin-only,
 *     and only after 15 minutes), so it is reported honestly and left alone.
 */

export type SubmitOutcome =
  | { kind: 'complete'; event: SiteLogEventOut }
  | { kind: 'partial'; event: SiteLogEventOut; failed: string[]; blocked: string[] }
  | { kind: 'created_not_uploaded'; event: SiteLogEventOut; blocked: string[] }
  | { kind: 'unconfirmed'; messageKey: string }
  | { kind: 'error'; messageKey: string; detail?: string; event?: SiteLogEventOut };

type Ctx = {
  draft: SiteLogDraft;
  /** Persist progress after every step that learns something durable. */
  patch: (p: Partial<SiteLogDraft>) => void;
};

/** The event body a 502 from declare carries, if it carries one. */
function eventFrom502(err: unknown): SiteLogEventOut | null {
  const anyErr = err as { response?: { status?: number; data?: { detail?: unknown } } };
  if (anyErr?.response?.status !== 502) return null;
  const detail = anyErr.response?.data?.detail;
  if (detail && typeof detail === 'object' && 'site_log_event_id' in detail) {
    return detail as SiteLogEventOut;
  }
  return null;
}

/**
 * Does this error leave the server's state unknown?
 *
 * A timeout or a dropped connection does. An HTTP answer does not: the server
 * replied, so it decided.
 */
function isUnconfirmed(err: unknown): boolean {
  const kind = classifyApiError(err);
  return kind === 'timeout' || kind === 'offline';
}

/** Verify a picked file is still where the picker left it. */
export async function fileStillExists(uri: string): Promise<boolean> {
  try {
    const info = await FileSystem.getInfoAsync(uri);
    return info.exists;
  } catch {
    return false;
  }
}

/**
 * Step 0 of every attempt: what does the server already have?
 *
 * Returns null only when the server is reachable and has nothing under this
 * capture_client_id. Throws if the server cannot be reached at all, so the
 * caller reports "not confirmed" rather than guessing.
 */
async function serverStateFor(draft: SiteLogDraft): Promise<SiteLogEventOut | null> {
  if (draft.server?.site_log_event_id) {
    return await getEvent(draft.server.site_log_event_id);
  }
  return await findMineByCaptureClientId(draft.capture_client_id);
}

function rememberEvent(ctx: Ctx, event: SiteLogEventOut): void {
  ctx.patch({
    server: {
      site_log_event_id: event.site_log_event_id,
      capture_status: event.capture_status,
      observed_at: Date.now(),
    },
    unconfirmed: false,
  });
}

function mergeAttachmentStates(
  draft: SiteLogDraft,
  event: SiteLogEventOut,
): DraftAttachment[] {
  const byId = new Map<string, AttachmentOut>(
    event.attachments.map((a) => [a.attachment_client_id, a]),
  );
  return draft.attachments.map((a) => {
    const server = byId.get(a.attachment_client_id);
    return server ? { ...a, status: server.state } : a;
  });
}

/**
 * Run (or resume) one capture.
 *
 * Safe to call again after any outcome. It re-reads server state first, so a
 * second call never duplicates work the first one completed.
 */
export async function runSubmit(ctx: Ctx): Promise<SubmitOutcome> {
  const { draft } = ctx;
  const declaration: Declaration = draft.declaration ?? {
    capture_client_id: draft.capture_client_id,
    job_id: draft.job_id,
    occurred_at: null,
    internal_location: null,
    body_text: draft.body_text.trim() ? draft.body_text : null,
    attachments: draft.attachments.map((a) => ({
      attachment_client_id: a.attachment_client_id,
      declared_media_type: a.media_type,
      declared_size_bytes: a.size,
    })),
  };
  // Pin the declaration before the first request, so a retry replays exactly
  // what was first sent even if the user has since edited the form.
  if (!draft.declaration) ctx.patch({ declaration });

  let event: SiteLogEventOut | null = null;

  // ---- 1. What does the server already have? --------------------------
  try {
    event = await serverStateFor(draft);
  } catch (err) {
    if (isUnconfirmed(err)) {
      ctx.patch({ unconfirmed: true, last_message: 'siteLog.status.unconfirmed' });
      return { kind: 'unconfirmed', messageKey: 'siteLog.status.unconfirmed' };
    }
    return { kind: 'error', messageKey: 'siteLog.error.lookup' };
  }

  // ---- 2. Declare, if it does not exist yet ---------------------------
  if (!event) {
    try {
      event = await declareCapture(declaration);
    } catch (err) {
      const carried = eventFrom502(err);
      if (carried) {
        // The record EXISTS. Only the server's own inline upload failed.
        rememberEvent(ctx, carried);
        ctx.patch({
          attachments: mergeAttachmentStates(draft, carried),
          last_message: 'siteLog.status.inline_failed',
        });
        return { kind: 'created_not_uploaded', event: carried, blocked: [] };
      }
      if (isUnconfirmed(err)) {
        ctx.patch({ unconfirmed: true, last_message: 'siteLog.status.unconfirmed' });
        return { kind: 'unconfirmed', messageKey: 'siteLog.status.unconfirmed' };
      }
      const anyErr = err as { response?: { status?: number; data?: { detail?: unknown } } };
      const detail =
        typeof anyErr?.response?.data?.detail === 'string'
          ? (anyErr.response!.data!.detail as string)
          : undefined;
      return { kind: 'error', messageKey: 'siteLog.error.declare', detail };
    }
  }
  rememberEvent(ctx, event);

  // ---- 3. Upload what still needs uploading ---------------------------
  const declaredIds = declaration.attachments.map((a) => a.attachment_client_id);
  const serverOwned = new Set(serverOwnedAttachmentIds(event, declaredIds));
  const byId = new Map(event.attachments.map((a) => [a.attachment_client_id, a]));

  const failed: string[] = [];
  const blocked: string[] = [];

  for (const att of draft.attachments) {
    if (serverOwned.has(att.attachment_client_id)) continue; // never ours to send
    const state = byId.get(att.attachment_client_id)?.state ?? 'awaiting_upload';
    if (state === 'stored') continue; // already saved: do not send again
    if (state === 'pending') {
      // An attempt is in flight or was stranded by a lost connection. The
      // client cannot clear it: reset is admin-only and refuses for 15
      // minutes. Say so; do not retry into a certain 409.
      blocked.push(att.attachment_client_id);
      continue;
    }
    if (!(await fileStillExists(att.uri))) {
      failed.push(att.attachment_client_id);
      ctx.patch({
        attachments: draft.attachments.map((a) =>
          a.attachment_client_id === att.attachment_client_id
            ? { ...a, status: 'missing' }
            : a,
        ),
      });
      continue;
    }
    try {
      await uploadAttachment(event.site_log_event_id, att.attachment_client_id, {
        uri: att.uri,
        name: att.name,
        mime: att.mime,
      });
    } catch (err) {
      if (isUnconfirmed(err)) {
        // The bytes may or may not have landed. Leave it; the next attempt
        // re-reads state and will skip it if it is stored.
        ctx.patch({ unconfirmed: true, last_message: 'siteLog.status.unconfirmed' });
        return { kind: 'unconfirmed', messageKey: 'siteLog.status.unconfirmed' };
      }
      failed.push(att.attachment_client_id);
    }
  }

  // ---- 4. Finalize, once nothing is still in flight -------------------
  let finalEvent = await getEvent(event.site_log_event_id);
  const stillPending = finalEvent.attachments.some((a) => a.state === 'pending');
  if (!stillPending) {
    try {
      finalEvent = await finalizeCapture(finalEvent.site_log_event_id);
    } catch (err) {
      if (isUnconfirmed(err)) {
        ctx.patch({ unconfirmed: true, last_message: 'siteLog.status.unconfirmed' });
        return { kind: 'unconfirmed', messageKey: 'siteLog.status.unconfirmed' };
      }
      // Finalize refuses while anything is in flight; the record still exists.
    }
  }

  rememberEvent(ctx, finalEvent);
  ctx.patch({ attachments: mergeAttachmentStates(draft, finalEvent) });

  if (blocked.length > 0) {
    return { kind: 'created_not_uploaded', event: finalEvent, blocked };
  }
  if (finalEvent.capture_status === 'complete') {
    return { kind: 'complete', event: finalEvent };
  }
  return { kind: 'partial', event: finalEvent, failed, blocked };
}
