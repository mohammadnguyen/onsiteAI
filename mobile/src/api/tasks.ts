/**
 * Tasks made from Site Log entries: the API surface, typed locally.
 *
 * Mirrors backend/app/schemas/task.py. Like src/api/siteLog.ts, not
 * generated from the OpenAPI types: regenerating them needs a running
 * backend, and these shapes are small.
 *
 * The server decides what a caller may do and says so in `permissions`;
 * screens show controls from that and never restate the rules.
 */
import { api } from './client';

export type TaskStatus = 'to_do' | 'in_progress' | 'blocked' | 'done';
export const TASK_STATUSES: readonly TaskStatus[] = ['to_do', 'in_progress', 'blocked', 'done'];

export type TaskUser = { user_id: string; full_name: string };

/** Job identity only — no money. */
export type TaskJob = {
  job_id: string;
  job_name: string;
  job_code: string | null;
  status: 'active' | 'completed';
};

export type TaskSource = {
  site_log_event_id: string;
  revision_no: number;
  /** False: this user may not open the entry. The app says so. */
  readable: boolean;
  /** The entry's current job, only when `readable`. */
  job_id: string | null;
};

export type TaskPermissions = {
  change_status: boolean;
  edit: boolean;
  assign_others: boolean;
  set_job: boolean;
  change_job: boolean;
};

export type TaskOut = {
  task_id: string;
  title: string;
  status: TaskStatus;
  origin: 'manual';
  /** YYYY-MM-DD, a calendar date with no time. */
  due_date: string | null;
  job: TaskJob | null;
  assignee: TaskUser | null;
  created_by: TaskUser | null;
  source: TaskSource;
  permissions: TaskPermissions;
  created_at: string;
  updated_at: string;
};

export type TaskCreateBody = {
  create_client_id: string;
  title: string;
  assignee_user_id: string | null;
  due_date: string | null;
};

/** Only the fields sent are changed. `job_id` can be set, never cleared. */
export type TaskPatch = Partial<{
  title: string;
  status: TaskStatus;
  assignee_user_id: string | null;
  due_date: string | null;
  job_id: string;
  reason: string;
}>;

export const TASK_PAGE_SIZE = 20;

export type TaskListParams = {
  status?: TaskStatus;
  mine?: 'assigned' | 'created';
  job_id?: string;
  offset?: number;
};

export async function createTask(eventId: string, body: TaskCreateBody): Promise<TaskOut> {
  const r = await api.post<TaskOut>(`/site-log-events/${eventId}/tasks`, body);
  return r.data;
}

export async function listTasks(params: TaskListParams = {}): Promise<TaskOut[]> {
  const r = await api.get<TaskOut[]>('/tasks', {
    params: { ...params, limit: TASK_PAGE_SIZE },
  });
  return r.data;
}

/**
 * The task this account created with this key, if the server has it.
 *
 * How a create whose answer was lost finds out what happened: asked BEFORE
 * anything is sent again, never instead of the original request.
 */
export async function findMyCreate(createClientId: string): Promise<TaskOut | null> {
  const r = await api.get<TaskOut[]>('/tasks', {
    params: { create_client_id: createClientId },
  });
  return r.data.length > 0 ? r.data[0] : null;
}

export async function getTask(taskId: string): Promise<TaskOut> {
  const r = await api.get<TaskOut>(`/tasks/${taskId}`);
  return r.data;
}

export async function patchTask(taskId: string, patch: TaskPatch): Promise<TaskOut> {
  const r = await api.patch<TaskOut>(`/tasks/${taskId}`, patch);
  return r.data;
}

export async function listEventTasks(eventId: string): Promise<TaskOut[]> {
  const r = await api.get<TaskOut[]>(`/site-log-events/${eventId}/tasks`);
  return r.data;
}
