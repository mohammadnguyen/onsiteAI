import { type LeaveQuestion, useLeaveGuard } from '../siteLog/useLeaveGuard';

const UNSAVED: LeaveQuestion = {
  title: 'tasks.new.leave_title',
  body: 'tasks.new.leave_body',
  confirm: 'tasks.new.leave_confirm',
  cancel: 'tasks.new.leave_cancel',
};

const STILL_SAVING: LeaveQuestion = {
  title: 'tasks.new.saving_leave_title',
  body: 'tasks.new.saving_leave_body',
  confirm: 'tasks.new.saving_leave_confirm',
  cancel: 'tasks.new.saving_leave_cancel',
};

/**
 * Ask before leaving a new task that would be lost (the capture screen's
 * guard, PR #22, in this screen's words).
 *
 * Lost means: the user changed the title, chose an assignee or a due date,
 * or typed a due date; or tapped Save and the record has not reached the
 * phone - still being written, the write failed, or the server refused it
 * and its values are back in the form.
 *
 * Once the record is on the phone, leaving loses nothing and is not asked
 * about: the create goes on without the screen, and until its answer is
 * known the entry shows it as not confirmed (the recovery rules). Neither
 * question says leaving cancels anything on the server - it cannot. While
 * the first write is still in flight, the question says the task may still
 * be created.
 */
export function useTaskLeaveGuard(state: {
  /** Title, assignee or due date changed by the user. */
  edited: boolean;
  /** Save tapped, its answer not in yet: picks which question is asked. */
  saving: boolean;
  /** This attempt's record is on the phone. */
  recorded: boolean;
  /** Save was tapped; until its record is on the phone, its values are only here. */
  attempted: boolean;
}): void {
  const { edited, saving, recorded, attempted } = state;
  // Save sets `attempted` as it starts, so a write in flight counts too.
  const wouldLose = !recorded && (edited || attempted);
  useLeaveGuard(wouldLose, saving && !recorded ? STILL_SAVING : UNSAVED);
}
