import axios from 'axios';

import { createTask, findMyCreate, type TaskOut } from '../api/tasks';
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
 */

export type SendOutcome =
  | { kind: 'created'; task: TaskOut }
  | { kind: 'refused'; status: number; detail: string | null }
  | { kind: 'unknown' };

export type ResolveOutcome =
  | { kind: 'found'; task: TaskOut }
  | { kind: 'not_found' }
  | { kind: 'unknown' };

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
 * A definite answer removes it; an unknown result keeps it.
 *
 * A 401 is not an answer about the task: the shared client refreshes and
 * replays it, and if it still ends in 401 the session is over - the record
 * stays for the same account's next sign-in.
 */
export async function sendCreate(p: PendingTaskCreate): Promise<SendOutcome> {
  const store = useTaskPendingCreates.getState();
  await store.putDurable(p);
  try {
    const task = await createTask(p.site_log_event_id, {
      create_client_id: p.create_client_id,
      title: p.title,
      assignee_user_id: p.assignee_user_id,
      due_date: p.due_date,
    });
    await useTaskPendingCreates.getState().removeDurable(p.create_client_id);
    return { kind: 'created', task };
  } catch (err) {
    const status = answeredStatus(err);
    if (status !== null && status !== 401) {
      await useTaskPendingCreates.getState().removeDurable(p.create_client_id);
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
 * failed: still unknown; nothing changes.
 */
export async function resolvePending(p: PendingTaskCreate): Promise<ResolveOutcome> {
  let task: TaskOut | null;
  try {
    task = await findMyCreate(p.create_client_id);
  } catch {
    return { kind: 'unknown' };
  }
  if (!task) return { kind: 'not_found' };
  await useTaskPendingCreates.getState().removeDurable(p.create_client_id);
  return { kind: 'found', task };
}

/** The user gives up on an unresolved create. Asked first by the screen. */
export async function discardPending(createClientId: string): Promise<void> {
  await useTaskPendingCreates.getState().removeDurable(createClientId);
}
