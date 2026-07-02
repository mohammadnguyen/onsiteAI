import { create } from 'zustand';
import storage from '../i18n/storage';

/**
 * O3 (U5): in-app font-size preference.
 *
 * The operator's device audit showed the app does not follow the OS
 * text-size setting in practice, and the field users who need larger
 * text are exactly the ones least likely to configure iOS Dynamic
 * Type — so the setting lives in the app, persisted with the same
 * storage shim as the language preference.
 *
 * The store holds a LEVEL, not a multiplier: the numeric scale per
 * level lives in `src/ui/type.ts` so type maths stays in one place.
 * `hydrate()` is called once from the root layout alongside auth/i18n
 * hydration; until it resolves the app renders at 'standard' (no
 * flash risk — standard is the historical size).
 */
export type FontScaleLevel = 'standard' | 'large' | 'xlarge';

const KEY = 'sitetracker_font_scale';
const LEVELS: FontScaleLevel[] = ['standard', 'large', 'xlarge'];

type State = {
  level: FontScaleLevel;
  hydrate: () => Promise<void>;
  setLevel: (level: FontScaleLevel) => Promise<void>;
};

export const useFontScaleStore = create<State>((set) => ({
  level: 'standard',
  hydrate: async () => {
    try {
      const saved = (await storage.getItem(KEY)) as FontScaleLevel | null;
      if (saved && LEVELS.includes(saved)) set({ level: saved });
    } catch {
      // Persistence failure is non-fatal — default size applies.
    }
  },
  setLevel: async (level) => {
    set({ level });
    try {
      await storage.setItem(KEY, level);
    } catch {
      // Keep the in-session choice even if persisting failed.
    }
  },
}));
