import type { TaskOut, TaskStatus } from '../api/tasks';
import { todayISO } from '../util/dates';

/**
 * Which badge style a task status uses (src/ui/kit StatusBadge keys).
 *
 * Colour discipline from the design tokens: green only for done, amber only
 * for something that needs attention. "Blocked" is the one status that asks
 * for attention; to-do and in-progress stay neutral.
 */
export function taskStatusBadgeKey(status: TaskStatus): string {
  switch (status) {
    case 'done':
      return 'reviewed';
    case 'blocked':
      return 'pending';
    default:
      return status; // neutral fallback style
  }
}

/** Past its due date and not done. Dates compare as YYYY-MM-DD strings. */
export function isOverdue(task: Pick<TaskOut, 'due_date' | 'status'>, today = todayISO()): boolean {
  return task.due_date !== null && task.status !== 'done' && task.due_date < today;
}
