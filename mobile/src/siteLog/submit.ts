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
import { useAuthStore } from '../store/auth';
import { fileExists, pathUnderDocuments, retainAttachment, retainedUri } from './files';

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
 *  6. ONE SUBMISSION BELONGS TO ONE SESSION. The account can change while
 *     this is running - a sign-out, or another person signing in. Every
 *     request boundary re-checks it, because the next request would
 *     otherwise go out with the new account's credentials and file one
 *     person's capture under another's name.
 */

export type SubmitOutcome =
  | { kind: 'complete'; event: SiteLogEventOut }
  | {
      kind: 'partial';
      event: SiteLogEventOut;
      failed: string[];
      blocked: string[];
      /** A recovery path that is closed, named so the screen can say so. */
      limitation?: string;
    }
  | {
      kind: 'created_not_uploaded';
      event: SiteLogEventOut;
      blocked: string[];
      /**
       * Which unfinished thing this is, as a translation key. An upload the
       * server is still processing and a text attachment the server failed
       * to store are different facts and read differently to the user; one
       * message for both told people to wait when they should retry.
       */
      bodyKey: string;
      limitation?: string;
    }
  | { kind: 'unconfirmed'; messageKey: string }
  | { kind: 'error'; messageKey: string; detail?: string; event?: SiteLogEventOut };

type Ctx = {
  draft: SiteLogDraft;
  /**
   * Persist progress, durably. Awaited at every step that learns something
   * which must survive the process dying a moment later.
   */
  patch: (p: Partial<SiteLogDraft>) => Promise<void>;
  /**
   * The signed-in user. A draft belongs to the account that created it and is
   * never sent under another one - drafts deliberately survive an involuntary
   * logout, so the next person to sign in on a shared phone must not be able
   * to submit, or even resume, what the previous one wrote.
   */
  userId: string;
  /**
   * The value of `useAuthStore.getState().sessionNonce` read by the SCREEN,
   * synchronously, before it did anything else.
   *
   * Reading it here instead would be too late: the caller persists the draft
   * first, and an account change during that write would be captured as the
   * starting session rather than detected as a change.
   */
  sessionNonce: number;
};

/**
 * Has the signed-in account changed since this submission started?
 *
 * The axios client reads the CURRENT token for every request, so a
 * submission that outlives its sign-in would keep going under whoever is
 * signed in next. The draft's own owner check runs once at the start; this
 * repeats the question at every request boundary, and immediately before
 * each request that writes.
 *
 * It cannot cancel a request already in flight. The client's 401 handler
 * covers the other half: it refuses to replay a request into a session that
 * is no longer the one it was issued under.
 */
function sessionGuard(startedUnder: number): () => boolean {
  return () => useAuthStore.getState().sessionNonce !== startedUnder;
}

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

/**
 * Where this attachment's bytes are NOW.
 *
 * A retained file is recorded by its path under the document directory,
 * because iOS can move the container out from under an absolute URI. The
 * `uri` remains the fallback for drafts written before that was stored.
 */
export function currentUri(att: DraftAttachment): string {
  if (att.path) return retainedUri(att.path) ?? att.uri;
  return att.uri;
}

/**
 * Verify the kept copy is still there.
 *
 * It normally is - the app's own document directory is not reclaimed the
 * way the cache is - but clearing app data or a reinstall removes it, and
 * that must be reported rather than discovered as an upload failure.
 */
export async function fileStillExists(uri: string): Promise<boolean> {
  return await fileExists(uri);
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

async function rememberEvent(
  patch: (p: Partial<SiteLogDraft>) => Promise<void>,
  event: SiteLogEventOut,
): Promise<void> {
  await patch({
    server: {
      site_log_event_id: event.site_log_event_id,
      capture_status: event.capture_status,
      observed_at: Date.now(),
    },
    unconfirmed: false,
  });
}

function mergeAttachmentStates(
  attachments: DraftAttachment[],
  event: SiteLogEventOut,
): DraftAttachment[] {
  const byId = new Map<string, AttachmentOut>(
    event.attachments.map((a) => [a.attachment_client_id, a]),
  );
  return attachments.map((a) => {
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
  if (draft.user_id !== ctx.userId) {
    return { kind: 'error', messageKey: 'siteLog.error.not_yours' };
  }
  const sessionChanged = sessionGuard(ctx.sessionNonce);
  if (sessionChanged()) {
    return { kind: 'error', messageKey: 'siteLog.error.session_changed' };
  }
  // Nothing is written to the draft store on this path: after a sign-out the
  // draft may legitimately have been wiped, and re-adding it would resurrect
  // one account's capture inside another's session.
  const stale: SubmitOutcome = {
    kind: 'error',
    messageKey: 'siteLog.error.session_changed',
  };
  /** A limitation that was hit and could not be worked around. */
  let limitation: string | undefined;
  /**
   * Persist, unless the account has changed.
   *
   * After a sign-out the draft may legitimately have been wiped, and a late
   * write would put one account's capture back inside another's session.
   */
  const patch = async (p: Partial<SiteLogDraft>): Promise<void> => {
    if (sessionChanged()) return;
    await ctx.patch(p);
  };
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
  if (!draft.declaration) await patch({ declaration });

  // ---- 0. Rescue anything an older build left in the OS cache --------
  // Before ANY request: a resume with no signal returns long before the
  // upload loop, and those bytes can be reclaimed at any moment. Keeping
  // them changes where they live and nothing else - same id, same
  // declaration, same bytes.
  const local = new Map(draft.attachments.map((a) => [a.attachment_client_id, a]));
  const unkeepable = new Set<string>();
  for (const att of draft.attachments) {
    if (att.retained === true && att.path) continue;
    if (att.retained === true) {
      // Kept by a build that recorded only the absolute URI. The bytes are
      // already ours; all that is missing is where they are RELATIVE to the
      // document directory - which is what survives the container moving.
      // Adopted rather than copied: copying would be pointless work and,
      // if the old URI is stale, would fail and lose them.
      const guess = pathUnderDocuments(att.uri, {
        userId: ctx.userId,
        captureClientId: draft.capture_client_id,
      });
      if (guess === null) {
        // The recorded path is not inside this account's and this
        // capture's folder. Copying from it anyway would import another
        // account's file and then send it as this one's: the refusal has
        // to END here, not fall through to the copy below.
        unkeepable.add(att.attachment_client_id);
        continue;
      }
      const candidate = retainedUri(guess);
      if (candidate !== null && (await fileExists(candidate))) {
        local.set(att.attachment_client_id, { ...att, uri: candidate, path: guess });
        continue;
      }
      // Claimed as kept, inside our own folder, but not there any more.
      unkeepable.add(att.attachment_client_id);
      continue;
    }
    try {
      const kept = await retainAttachment({
        userId: ctx.userId,
        captureClientId: draft.capture_client_id,
        attachmentId: att.attachment_client_id,
        sourceUri: currentUri(att),
        name: att.name,
        expectedSize: att.size,
      });
      local.set(att.attachment_client_id, {
        ...att,
        uri: kept.uri,
        path: kept.path,
        retained: true,
      });
    } catch {
      // The cache file has gone, or it cannot be copied. Either way this
      // attachment cannot be sent from this phone; saying so beats failing
      // an upload later with something unexplainable.
      unkeepable.add(att.attachment_client_id);
    }
  }
  const attachments = [...local.values()];
  if (attachments.some((a, i) => a !== draft.attachments[i])) {
    await patch({ attachments });
  }

  let event: SiteLogEventOut | null = null;

  // ---- 1. What does the server already have? --------------------------
  try {
    event = await serverStateFor(draft);
  } catch (err) {
    if (isUnconfirmed(err)) {
      await patch({ unconfirmed: true, last_message: 'siteLog.status.unconfirmed' });
      return { kind: 'unconfirmed', messageKey: 'siteLog.status.unconfirmed' };
    }
    return { kind: 'error', messageKey: 'siteLog.error.lookup' };
  }
  if (sessionChanged()) return stale;

  // ---- 2. Declare, if it does not exist yet ---------------------------
  if (!event) {
    // Written BEFORE the request goes out, and flushed. Between the server
    // creating the record and this client hearing about it there is a
    // window in which the process can die - a crash, a kill, a battery.
    // With the draft still saying "not sent", the next launch would assert
    // something nobody observed, and the user would be invited to capture
    // it all again. Saying "unknown" is both true and recoverable: resuming
    // asks the server first, under the same capture_client_id.
    await patch({ unconfirmed: true, last_message: 'siteLog.status.unconfirmed' });
    // That write is awaited, and an account change can land inside it. The
    // next line sends a request; the client stamps it with whatever session
    // is current, so nothing downstream would catch this.
    if (sessionChanged()) return stale;
    try {
      event = await declareCapture(declaration);
    } catch (err) {
      const carried = eventFrom502(err);
      if (carried) {
        // The record EXISTS. Only the server's own inline upload failed.
        await rememberEvent(patch, carried);
        await patch({
          // The RESCUED list: merging the draft's original one would put
          // the cache URIs back and drop the retained flags, and the next
          // resume would then copy from a file that is no longer there.
          attachments: mergeAttachmentStates(attachments, carried),
          last_message: 'siteLog.status.inline_failed',
        });
        return {
          kind: 'created_not_uploaded',
          event: carried,
          blocked: [],
          // Not "still processing": the server's own inline upload FAILED.
          // It is recoverable - resuming replays the declaration - so the
          // message has to send the user to the retry, not to waiting.
          bodyKey: 'siteLog.status.inline_failed',
        };
      }
      if (isUnconfirmed(err)) {
        await patch({ unconfirmed: true, last_message: 'siteLog.status.unconfirmed' });
        return { kind: 'unconfirmed', messageKey: 'siteLog.status.unconfirmed' };
      }
      const anyErr = err as { response?: { status?: number; data?: { detail?: unknown } } };
      const status = anyErr?.response?.status;
      // A 4xx is the SERVER refusing: nothing was created, so the result is
      // not unknown any more. A 5xx is not that. A gateway can answer 502 or
      // 504 after the backend committed the event, so the uncertainty
      // stands and the next attempt asks before doing anything.
      if (typeof status === 'number' && status < 500) {
        await patch({ unconfirmed: false, last_message: null });
      } else {
        // A 5xx is not an answer about what happened. Reporting "the entry
        // could not be created" would contradict the flag just persisted
        // and send the user off to capture it all again.
        return { kind: 'unconfirmed', messageKey: 'siteLog.status.unconfirmed' };
      }
      const detail =
        typeof anyErr?.response?.data?.detail === 'string'
          ? (anyErr.response!.data!.detail as string)
          : undefined;
      return { kind: 'error', messageKey: 'siteLog.error.declare', detail };
    }
  }
  await rememberEvent(patch, event);
  if (sessionChanged()) return stale;

  // ---- 2b. Recover the server-owned inline row, if it needs it --------
  // The only way to repair inline text is to replay the declaration: the row
  // is the server's, the client may not upload to it, and the bytes come from
  // revision 1. Without this, a record whose inline upload failed once stayed
  // partially failed for ever, because every later attempt found the event
  // and skipped declare.
  const declaredIdsForInline = declaration.attachments.map((a) => a.attachment_client_id);
  const inlineNeedsRecovery = event.attachments.some(
    (a) =>
      !declaredIdsForInline.includes(a.attachment_client_id) &&
      (a.state === 'failed' || a.state === 'awaiting_upload'),
  );
  if (inlineNeedsRecovery) {
    try {
      event = await declareCapture(declaration);
      await rememberEvent(patch, event);
    } catch (err) {
      const carried = eventFrom502(err);
      if (carried) {
        // Still failing server-side. The record stands; say so rather than
        // retrying into the same wall.
        event = carried;
        await rememberEvent(patch, event);
        await patch({ last_message: 'siteLog.status.inline_failed' });
      } else if (isUnconfirmed(err)) {
        await patch({ unconfirmed: true, last_message: 'siteLog.status.unconfirmed' });
        return { kind: 'unconfirmed', messageKey: 'siteLog.status.unconfirmed' };
      } else {
        const status = (err as { response?: { status?: number } })?.response?.status;
        if (typeof status === 'number' && status < 500) {
          // The server itself refused, and will refuse the same replay
          // again - a job completed since the declare is the usual reason.
          // The record and its ids stand; the closed recovery path is
          // recorded AND returned so the screen states it instead of
          // inviting a retry that cannot work. No substitute record is
          // created and nothing is reported as saved.
          limitation = 'siteLog.status.inline_unrecoverable';
          await patch({ last_message: limitation });
        } else {
          // A 5xx or a 502 with no event in it says nothing about whether
          // the replay took. Calling that permanent would tell the user to
          // stop trying at exactly the moment trying again is the right
          // thing to do.
          await patch({ unconfirmed: true, last_message: 'siteLog.status.unconfirmed' });
          return { kind: 'unconfirmed', messageKey: 'siteLog.status.unconfirmed' };
        }
      }
    }
    if (sessionChanged()) return stale;
  }

  // ---- 3. Upload what still needs uploading ---------------------------
  const declaredIds = declaration.attachments.map((a) => a.attachment_client_id);
  const serverOwned = new Set(serverOwnedAttachmentIds(event, declaredIds));
  const byId = new Map(event.attachments.map((a) => [a.attachment_client_id, a]));

  const failed: string[] = [];
  const blocked: string[] = [];
  // Whether the FILE is still on this phone is a local fact, tracked apart
  // from the server's view of the attachment. Folding it into the same field
  // let the final merge overwrite "the file is gone" with the server's
  // `awaiting_upload`, and the screen then offered a retry that could only
  // fail again.
  const missingLocally = new Set<string>();

  for (const att of attachments) {
    if (serverOwned.has(att.attachment_client_id)) continue; // never ours to send
    if (unkeepable.has(att.attachment_client_id)) {
      // Its bytes could not be rescued above.
      failed.push(att.attachment_client_id);
      missingLocally.add(att.attachment_client_id);
      continue;
    }
    const state = byId.get(att.attachment_client_id)?.state ?? 'awaiting_upload';
    if (state === 'stored') continue; // already saved: do not send again
    if (state === 'pending') {
      // An attempt is in flight or was stranded by a lost connection. The
      // client cannot clear it: reset is admin-only and refuses for 15
      // minutes. Say so; do not retry into a certain 409.
      blocked.push(att.attachment_client_id);
      continue;
    }
    const source = currentUri(att);
    if (!(await fileStillExists(source))) {
      failed.push(att.attachment_client_id);
      missingLocally.add(att.attachment_client_id);
      continue;
    }
    // Checked once per attachment, here rather than at the top of the loop:
    // retaining and checking the file are themselves awaits, so this is the
    // last point before bytes go out under whatever account is current.
    if (sessionChanged()) return stale;
    try {
      await uploadAttachment(event.site_log_event_id, att.attachment_client_id, {
        uri: source,
        name: att.name,
        mime: att.mime,
      });
    } catch (err) {
      if (isUnconfirmed(err)) {
        // The bytes may or may not have landed. Leave it; the next attempt
        // re-reads state and will skip it if it is stored.
        await patch({ unconfirmed: true, last_message: 'siteLog.status.unconfirmed' });
        return { kind: 'unconfirmed', messageKey: 'siteLog.status.unconfirmed' };
      }
      failed.push(att.attachment_client_id);
    }
  }

  // ---- 4. Finalize, once nothing is still in flight -------------------
  // This read is inside the uncertainty path like every other request. Left
  // outside it, a connection lost after the uploads rejected out of the whole
  // routine: the caller's spinner never cleared, and the draft was left
  // marked confirmed when it was anything but.
  if (sessionChanged()) return stale;
  let finalEvent: SiteLogEventOut;
  try {
    finalEvent = await getEvent(event.site_log_event_id);
  } catch (err) {
    if (isUnconfirmed(err)) {
      await patch({ unconfirmed: true, last_message: 'siteLog.status.unconfirmed' });
      return { kind: 'unconfirmed', messageKey: 'siteLog.status.unconfirmed' };
    }
    return { kind: 'error', messageKey: 'siteLog.error.lookup', event };
  }

  const stillPending = finalEvent.attachments.some((a) => a.state === 'pending');
  if (!stillPending) {
    if (sessionChanged()) return stale;
    try {
      finalEvent = await finalizeCapture(finalEvent.site_log_event_id);
    } catch (err) {
      if (isUnconfirmed(err)) {
        await patch({ unconfirmed: true, last_message: 'siteLog.status.unconfirmed' });
        return { kind: 'unconfirmed', messageKey: 'siteLog.status.unconfirmed' };
      }
      // Finalize refuses while anything is in flight; the record still exists.
    }
  }

  await rememberEvent(patch, finalEvent);
  await patch({
    attachments: mergeAttachmentStates(attachments, finalEvent).map((a) =>
      missingLocally.has(a.attachment_client_id) && a.status !== 'stored'
        ? { ...a, status: 'missing' as const }
        : a,
    ),
  });

  if (blocked.length > 0) {
    return {
      kind: 'created_not_uploaded',
      event: finalEvent,
      blocked,
      bodyKey: 'siteLog.status.blocked_body',
      limitation,
    };
  }
  if (finalEvent.capture_status === 'complete') {
    return { kind: 'complete', event: finalEvent };
  }
  return { kind: 'partial', event: finalEvent, failed, blocked, limitation };
}
