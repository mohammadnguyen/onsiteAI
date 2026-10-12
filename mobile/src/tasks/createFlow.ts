import axios from 'axios';

import { createTask, findMyCreate, type TaskOut } from '../api/tasks';
import { useAuthStore } from '../store/auth';
import {
  useTaskPendingCreates,
  type PendingTaskCreate,
} from '../store/taskPendingCreates';

/**
 * Sending a task create, and finding out what happened to one whose answer
 * was lost (plan v2 §5). No screen state here: the screens call these and
 * show the outcome.
 *
 * The rule both follow: a create is either ANSWERED (the task, or a refusal
 * the server gave on purpose) or UNKNOWN. Unknown keeps the pending record;
 * nothing is retried in the background, and nothing is sent again until the
 * server has been asked whether the first one went through.
 *
 * Every request is made for the session the user acted in - `startedUnder`,
 * the auth store's session nonce read BEFORE the first await - and only for
 * a record that belongs to the signed-in account. Saving the record is an
 * await; a sign-out and someone else's sign-in can land inside it, and the
 * request would then carry the other person's credentials and create the
 * first person's task as theirs.
 */

export type SendOutcome =
  | { kind: 'created'; task: TaskOut }
  | { kind: 'refused'; status: number; detail: string | null }
  | { kind: 'unknown' }
  /** The record could not be written to the phone; nothing was sent. */
  | { kind: 'not_saved' }
  /** The account changed before the request left; nothing was sent. */
  | { kind: 'stale' };

export type ResolveOutcome =
  | { kind: 'found'; task: TaskOut }
  | { kind: 'not_found' }
  | { kind: 'unknown' }
  | { kind: 'stale' };

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
 * Record, then send.
 *
 * The pending record is on disk BEFORE the request leaves, so a crash or a
 * kill at any later moment still leaves the key the server may have used.
 * If it cannot be written, nothing is sent. A definite answer removes it;
 * an unknown result keeps it.
 *
 * A 401 is not an answer about the task: the shared client refreshes and
 * replays it, and if it still ends in 401 the session is over - the record
 * stays for the same account's next sign-in.
 */
export async function sendCreate(
  p: PendingTaskCreate,
  startedUnder: number,
): Promise<SendOutcome> {
  if (!sameSession(startedUnder, p.user_id)) return { kind: 'stale' };
  try {
    await useTaskPendingCreates.getState().putDurable(p);
  } catch {
    return { kind: 'not_saved' };
  }
  // The write above was an await: check again, immediately before sending.
  if (!sameSession(startedUnder, p.user_id)) return { kind: 'stale' };
  try {
    const task = await createTask(p.site_log_event_id, {
      create_client_id: p.create_client_id,
      title: p.title,
      assignee_user_id: p.assignee_user_id,
      due_date: p.due_date,
    });
    await forget(p.create_client_id);
    return { kind: 'created', task };
  } catch (err) {
    const status = answeredStatus(err);
    if (status !== null && status !== 401) {
      await forget(p.create_client_id);
      return { kind: 'refused', status, detail: detailOf(err) };
    }
    return { kind: 'unknown' };
  }
}

/**
 * Ask the server about an unresolved create - before anything is resent.
 *
 * Found: the record is done with. Not found: the record stays, and the
 * caller offers to send the SAME values again under the SAME key. Asking
 * failed: still unknown; nothing changes. Asked under another account (the
 * lookup only ever finds the asker's own creates): nothing is concluded.
 */
export async function resolvePending(
  p: PendingTaskCreate,
  startedUnder: number,
): Promise<ResolveOutcome> {
  if (!sameSession(startedUnder, p.user_id)) return { kind: 'stale' };
  let task: TaskOut | null;
  try {
    task = await findMyCreate(p.create_client_id);
  } catch {
    return { kind: 'unknown' };
  }
  if (!sameSession(startedUnder, p.user_id)) return { kind: 'stale' };
  if (!task) return { kind: 'not_found' };
  await forget(p.create_client_id);
  return { kind: 'found', task };
}

/** The user gives up on an unresolved create. Asked first by the screen. */
export async function discardPending(createClientId: string): Promise<void> {
  await forget(createClientId);
}

/**
 * Remove a record whose fate is now known. If the phone's storage cannot be
 * written at this moment the record simply stays, and the next visit asks
 * the server again - which answers "found" - so nothing is duplicated.
 */
async function forget(createClientId: string): Promise<void> {
  try {
    await useTaskPendingCreates.getState().removeDurable(createClientId);
  } catch {
    // Kept on purpose; see above.
  }
}
