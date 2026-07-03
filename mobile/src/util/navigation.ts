import { useRef } from 'react';
import { useRouter, type Href } from 'expo-router';

/**
 * One-shot guarded back for pushed screens' custom back chevrons.
 *
 * Under the root Stack (back-nav fix), a second GO_BACK after this
 * screen has already popped is NOT a no-op: the root stack can't
 * handle it, so it bubbles into the tab navigator (backBehavior
 * 'firstRoute') and yanks the user onto the first tab (Expenses).
 * Two real double-fire windows exist on native-stack pops:
 *   - the ~300ms pop animation keeps the chevron tappable, so a
 *     double-tap dispatches GO_BACK twice (under the old Slot root
 *     this was impossible — the screen unmounted instantly);
 *   - async flows (save/delete → back) can fire after a manual back
 *     already happened.
 * First call wins; later calls are no-ops for this screen instance.
 *
 * The fallback replace covers deep-link / cold-launch entry where
 * there is no history to pop.
 */
export function useOneShotBack(fallbackHref: Href): () => void {
  const router = useRouter();
  const firedRef = useRef(false);
  return () => {
    if (firedRef.current) return;
    firedRef.current = true;
    if (router.canGoBack()) router.back();
    else router.replace(fallbackHref);
  };
}
