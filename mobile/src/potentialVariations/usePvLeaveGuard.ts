import { type LeaveQuestion, useLeaveGuard } from '../siteLog/useLeaveGuard';

const UNSAVED: LeaveQuestion = {
  title: 'potentialVariations.new.leave_title',
  body: 'potentialVariations.new.leave_body',
  confirm: 'potentialVariations.new.leave_confirm',
  cancel: 'potentialVariations.new.leave_cancel',
};

const STILL_SAVING: LeaveQuestion = {
  title: 'potentialVariations.new.saving_leave_title',
  body: 'potentialVariations.new.saving_leave_body',
  confirm: 'potentialVariations.new.saving_leave_confirm',
  cancel: 'potentialVariations.new.saving_leave_cancel',
};

/**
 * Ask before leaving a flag that would be lost - the task screen's rule
 * (src/tasks/useTaskLeaveGuard.ts), in this screen's words.
 *
 * Lost means: the user changed the title; or tapped Save and the record has
 * not reached the phone - still being written, the write failed, or the
 * server refused it and its values are back in the form.
 *
 * Once the record is on the phone, leaving loses nothing and is not asked
 * about: the flag goes on without the screen, and until its answer is known
 * the entry shows it as not confirmed. Neither question says leaving
 * cancels anything on the server - it cannot.
 */
export function usePvLeaveGuard(state: {
  /** The title differs from the entry's first line. */
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
