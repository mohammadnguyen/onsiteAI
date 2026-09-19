import { useCallback, useRef } from 'react';
import { useFocusEffect } from 'expo-router';

/**
 * Is this screen the one the user is looking at, right now?
 *
 * MOUNTED IS NOT THE SAME QUESTION. The app's root Stack keeps a screen
 * mounted underneath whatever is pushed on top of it, and `router` acts on
 * the route that is CURRENT, not on the screen that calls it. A submission
 * started here can therefore finish while the user is filling in a second
 * capture pushed above this one, and a `router.replace` at that moment
 * throws away their new screen - text, attachments and all - while this
 * screen is still perfectly mounted.
 *
 * A ref rather than state on purpose: it is read inside async callbacks
 * that outlive the render they started in, where a captured state value
 * would be the old one.
 */
export function useScreenActive(): { readonly current: boolean } {
  const active = useRef(true);
  useFocusEffect(
    useCallback(() => {
      active.current = true;
      return () => {
        active.current = false;
      };
    }, []),
  );
  return active;
}
