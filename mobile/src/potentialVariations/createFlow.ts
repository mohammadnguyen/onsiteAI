import axios from 'axios';

import {
  createPotentialVariation,
  findMyPvCreate,
  type PvOut,
} from '../api/potentialVariations';
import { useAuthStore } from '../store/auth';
import { usePvPendingCreates, type PendingPvCreate } from '../store/pvPendingCreates';

/**
 * Sending a potential-variation flag, and finding out what happened to one
 * whose answer was lost (DEC-PV-001, reliability "P"). No screen state here:
 * the screens call these and show the outcome.
 *
 * A deliberate copy of src/tasks/createFlow.ts (the founder chose a copy
 * over a Task refactor); the rules are the same. A flag is either ANSWERED
 * (the flag, or a refusal the server gave on purpose), UNAVAILABLE (the
 * server answered, but not about this flag - see `classify`) or UNKNOWN.
 * Unavailable and unknown keep the pending record, its key and its values;
 * nothing is retried in the background, and nothing is sent again until the
 * server has been asked whether the first one went through.
 *
 * Every request is made for the session the user acted in - `startedUnder`,
 * the auth store's session nonce read BEFORE the first await - and only for
 * a record that belongs to the signed-in account.
 *
 * The differences from tasks:
 * - The flag pins the entry version AND the job (or no job) the person saw.
 *   If the entry has moved on, the server refuses with `source_changed` or
 *   `job_changed` and nothing is created under the key; the screen reloads
 *   the entry and asks the person to check it and save again, under a new
 *   key.
 * - A key the server says it has already used for a different flag
 *   (`conflict`) is never given up: the record is kept and the flag is
 *   looked up by that key. Giving the key up for a new one could make a
 *   second flag next to the one that exists.
 */

export type PvRefusal = {
  kind: 'refused';
  status: number;
  detail: string | null;
  /** The entry has a newer version than the one the person saw. */
  sourceChanged: boolean;
  /** The entry is no longer on the job (or no job) the person saw. */
  jobChanged: boolean;
};

export type PvSendOutcome =
  | { kind: 'created'; pv: PvOut }
  | PvRefusal
  /**
   * The server says something already exists under this key (or answered
   * 409 for a reason this app does not know): the record is kept, and the
   * flag is looked up by the key. Never a new key.
   */
  | { kind: 'conflict' }
  /** The server offers no flag routes right now; the record is kept. */
  | { kind: 'unavailable' }
  | { kind: 'unknown' }
  /** The record could not be written to the phone; nothing was sent. */
  | { kind: 'not_saved' }
  /** The account changed before the request left; nothing was sent. */
  | { kind: 'stale' };

export type PvResolveOutcome =
  | { kind: 'found'; pv: PvOut }
  /** Only when the flag was still out and the server then refused it. */
  | PvRefusal
  | { kind: 'not_found' }
  /** The server offers no flag routes right now; nothing was concluded. */
  | { kind: 'unavailable' }
  | { kind: 'unknown' }
  | { kind: 'stale' };

/**
 * Flags this app has sent and not yet heard back about, by key.
 *
 * Only the app can know that a request it sent is still out: to the server,
 * "not committed yet" and "never arrived" look the same. While a key is
 * here, nobody asks the server about it and nobody sends it again: both
 * wait for this send's own answer.
 */
const inFlight = new Map<string, Promise<PvSendOutcome>>();

/** Still the session the user acted in, and still the record's owner. */
function sameSession(startedUnder: number, owner: string): boolean {
  const auth = useAuthStore.getState();
  return auth.sessionNonce === startedUnder && auth.userId === owner;
}

/** A response the server chose to give: anything below 500. */
function answeredStatus(err: unknown): number | null {
  if (!axios.isAxiosError(err) || !err.response) return null;
  const status = err.response.status;
  return status < 500 ? status : null;
}

function detailOf(err: unknown): string | null {
  if (!axios.isAxiosError(err)) return null;
  const detail = err.response?.data?.detail;
  return typeof detail === 'string' ? detail : null;
}

/**
 * The one 404 the flag API gives on purpose to a create: the entry does not
 * exist, or this account may not read it (backend `PvSourceNotFound`, the
 * same wording as the Site Log and Task APIs). The server answers a key it
 * has already used BEFORE that check, so this 404 means nothing was created
 * under the key. Any other 404 - a server without the flag routes, a proxy,
 * a body that cannot be read - says nothing about the flag: "unavailable".
 */
const SOURCE_NOT_FOUND = 'Site log event not found';

/** The machine-readable start of the server's "the entry changed" refusal. */
const SOURCE_CHANGED_PREFIX = 'source_changed:';
/** ... and of its "the entry's job changed" refusal. */
const JOB_CHANGED_PREFIX = 'job_changed:';
/** The server's refusal of a withdrawn entry (backend `PvSourceWithdrawn`). */
export const WITHDRAWN = 'The source entry is withdrawn';

/** The server refused because the entry has a newer version than the one shown. */
export function isSourceChanged(status: number, detail: string | null): boolean {
  return status === 409 && detail !== null && detail.startsWith(SOURCE_CHANGED_PREFIX);
}

/** The server refused because the entry is not on the job (or no job) shown. */
export function isJobChanged(status: number, detail: string | null): boolean {
  return status === 409 && detail !== null && detail.startsWith(JOB_CHANGED_PREFIX);
}

/**
 * The 409s the server gives on purpose when NOTHING exists under the key:
 * it answers a key it has already used before any of these checks, so each
 * of them means no flag was made with it. Every other 409 - above all "this
 * key was already used for a different flag" - may mean a flag exists under
 * the key: the record is kept and looked up (`conflict`), never dropped for
 * a new key. A 409 whose wording this app does not know lands on that side
 * too: the side that keeps the record.
 */
function nothingUnderKey(status: number, detail: string | null): boolean {
  return detail === WITHDRAWN || isSourceChanged(status, detail) || isJobChanged(status, detail);
}

/** The server answered, but the flag routes were not there to answer. */
function routeMissing(status: number, detail: string | null): boolean {
  return (status === 404 && detail !== SOURCE_NOT_FOUND) || status === 405;
}

/**
 * Answers that say "not now", not "no": a request timeout (408) or too many
 * requests (429), from the server or anything in front of it. The request
 * may still be running, or may never have reached the flag routes; either
 * way nothing was judged, so the record - its key and values - must stay.
 * Treated as a refusal, the record would be dropped and the next save would
 * take a new key while the first request could still commit: two flags.
 */
const NOT_NOW = new Set([408, 429]);

/**
 * What a failed create request means. Refused: the server judged this flag
 * and said no, on purpose - nothing exists under the key (that includes
 * `source_changed` and `job_changed`). Conflict: a 409 that may mean a flag
 * exists under the key (see `nothingUnderKey`). Unavailable: it answered
 * without judging it. Unknown: no usable answer at all (a lost connection,
 * a server error, a "not now" answer - see `NOT_NOW` - or a session that
 * ended - a 401 is the shared client's to handle, and the record stays for
 * the same account's next sign-in).
 */
function classify(
  err: unknown,
): PvRefusal | { kind: 'conflict' } | { kind: 'unavailable' } | { kind: 'unknown' } {
  const status = answeredStatus(err);
  if (status === null || status === 401 || NOT_NOW.has(status)) return { kind: 'unknown' };
  const detail = detailOf(err);
  if (routeMissing(status, detail)) return { kind: 'unavailable' };
  if (status === 409 && !nothingUnderKey(status, detail)) return { kind: 'conflict' };
  return {
    kind: 'refused',
    status,
    detail,
    sourceChanged: isSourceChanged(status, detail),
    jobChanged: isJobChanged(status, detail),
  };
}

/**
 * Record, then send.
 *
 * The pending record is on disk BEFORE the request leaves, so a crash or a
 * kill at any later moment still leaves the key the server may have used.
 * If it cannot be written, nothing is sent. A definite answer removes it;
 * a conflict, unavailable or unknown result keeps it, with its key and
 * values.
 *
 * A key that is already out is not sent twice: the caller shares the
 * answer of the send in progress (see `inFlight`).
 *
 * `onRecorded` is called once the record is on the phone - from then on,
 * leaving the screen loses nothing (only for the call that starts the send).
 */
export function sendPvCreate(
  p: PendingPvCreate,
  startedUnder: number,
  onRecorded?: () => void,
): Promise<PvSendOutcome> {
  const running = inFlight.get(p.create_client_id);
  if (running) return running;
  // Both before the first await: from the tap on, a screen opened for this
  // entry sees this flag (`sending`) and can join its answer (`inFlight`).
  usePvPendingCreates.getState().markSending(p);
  const run = send(p, startedUnder, onRecorded).finally(() => {
    inFlight.delete(p.create_client_id);
    usePvPendingCreates.getState().unmarkSending(p.create_client_id);
  });
  inFlight.set(p.create_client_id, run);
  return run;
}

async function send(
  p: PendingPvCreate,
  startedUnder: number,
  onRecorded: (() => void) | undefined,
): Promise<PvSendOutcome> {
  if (!sameSession(startedUnder, p.user_id)) return { kind: 'stale' };
  try {
    await usePvPendingCreates.getState().putDurable(p);
  } catch {
    return { kind: 'not_saved' };
  }
  onRecorded?.();
  // The write above was an await: check again, immediately before sending.
  if (!sameSession(startedUnder, p.user_id)) return { kind: 'stale' };
  try {
    const pv = await createPotentialVariation(p.site_log_event_id, {
      create_client_id: p.create_client_id,
      title: p.title,
      source_revision_no: p.source_revision_no,
      job_id: p.job_id,
    });
    await forget(p.create_client_id);
    return { kind: 'created', pv };
  } catch (err) {
    const answer = classify(err);
    // Only a refusal says nothing exists under the key; anything else keeps
    // the record - the same key and values - for the next check.
    if (answer.kind === 'refused') await forget(p.create_client_id);
    return answer;
  }
}

/**
 * Find out what happened to an unresolved flag - before anything is resent.
 *
 * If this app still has it out, that send's own answer is awaited instead of
 * asking the server; a send that ends in a conflict or unknown is followed
 * by the question below, as on any other return.
 *
 * Found: the record is done with. Not found: the record stays, and the
 * caller offers to send the SAME values again under the SAME key. The
 * server offers no flag routes: unavailable; nothing changes. Asking failed
 * otherwise: still unknown; nothing changes. Asked under another account:
 * nothing is concluded.
 */
export async function resolvePvPending(
  p: PendingPvCreate,
  startedUnder: number,
): Promise<PvResolveOutcome> {
  if (!sameSession(startedUnder, p.user_id)) return { kind: 'stale' };
  const running = inFlight.get(p.create_client_id);
  if (running) {
    const sent = await running;
    if (!sameSession(startedUnder, p.user_id)) return { kind: 'stale' };
    // The send has already removed the record for a definite answer.
    if (sent.kind === 'created') return { kind: 'found', pv: sent.pv };
    if (sent.kind === 'refused') return sent;
  }
  let pv: PvOut | null;
  try {
    pv = await findMyPvCreate(p.create_client_id);
  } catch (err) {
    // The lookup never refuses on purpose: a 404/405 here is the routes
    // being absent, never "no such flag" (that is an empty list).
    const status = answeredStatus(err);
    if (status === 404 || status === 405) return { kind: 'unavailable' };
    return { kind: 'unknown' };
  }
  if (!sameSession(startedUnder, p.user_id)) return { kind: 'stale' };
  if (!pv) return { kind: 'not_found' };
  await forget(p.create_client_id);
  return { kind: 'found', pv };
}

/**
 * The user gives up on an unresolved flag. Asked first by the screen.
 * True once the record is gone; false if the phone's storage could not be
 * written, and the record - with its key - stays.
 */
export async function discardPvPending(createClientId: string): Promise<boolean> {
  try {
    await usePvPendingCreates.getState().removeDurable(createClientId);
    return true;
  } catch {
    return false;
  }
}

/**
 * Remove a record whose fate is now known. If the phone's storage cannot be
 * written at this moment the record simply stays, and the next visit asks
 * the server again - which answers "found" - so nothing is duplicated.
 */
async function forget(createClientId: string): Promise<void> {
  try {
    await usePvPendingCreates.getState().removeDurable(createClientId);
  } catch {
    // Kept on purpose; see above.
  }
}
