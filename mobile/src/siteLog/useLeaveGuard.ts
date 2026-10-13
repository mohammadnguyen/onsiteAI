import { useState } from 'react';
import { useNavigation, usePreventRemove } from '@react-navigation/native';
import { useTranslation } from 'react-i18next';

import { useAuthStore } from '../store/auth';
import { confirmDestructive } from './dialogs';

/**
 * Ask before leaving a capture that has not been saved.
 *
 * Leaving the new-entry screen throws the capture away: its text, files and
 * recording exist only in that screen's state, and its unmount cleanup
 * releases the kept copies. Back, the iOS edge swipe and the Android back
 * button all did that without a word (ops-journal ux-issues #2).
 *
 * The native stack honours this for all three: on iOS it holds the swipe
 * (`preventNativeDismiss`), on Android the hardware back, and every attempt
 * arrives here as the action to replay if the user chooses to discard.
 * Replaying it does not ask again: the navigator marks the action as
 * already offered to this screen.
 *
 * NEVER in the way of a sign-out. The layout clears the whole stack when the
 * session ends (`dismissAll`), and a capture filed under a session that is
 * over cannot be saved by this screen anyway. So once the session this
 * screen opened in has ended, leaving is not guarded and nothing is asked -
 * the same rule the draft screen's Discard follows.
 */
export function useLeaveGuard(hasUnsaved: boolean): void {
  const { t } = useTranslation();
  const navigation = useNavigation();
  const [openedIn] = useState(() => useAuthStore.getState().sessionNonce);
  const sameSession = useAuthStore((s) => s.accessToken !== null && s.sessionNonce === openedIn);

  usePreventRemove(hasUnsaved && sameSession, ({ data }) => {
    // Belt and braces for the sign-out clear-out: should it reach here
    // before the flag above has caught up, it goes through unasked.
    if (!isSession(openedIn)) {
      navigation.dispatch(data.action);
      return;
    }
    confirmDestructive({
      title: t('siteLog.new.leave_title'),
      body: t('siteLog.new.leave_body'),
      confirmLabel: t('siteLog.new.leave_confirm'),
      cancelLabel: t('siteLog.new.leave_cancel'),
      onConfirm: () => {
        // Checked again HERE: the dialog may be answered after the session
        // it was asked in has ended, by which point the layout has already
        // cleared this screen.
        if (!isSession(openedIn)) return;
        navigation.dispatch(data.action);
      },
    });
  });
}

/** Is the signed-in session still the one numbered `nonce`? */
function isSession(nonce: number): boolean {
  const now = useAuthStore.getState();
  return now.accessToken !== null && now.sessionNonce === nonce;
}
