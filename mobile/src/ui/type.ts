import { useMemo } from 'react';
import { useFontScaleStore, type FontScaleLevel } from '../store/fontScale';

/**
 * O3 (U5): app-wide type scaling without a typography refactor.
 *
 * Screens keep their existing `StyleSheet.create` blocks (renamed to a
 * `base` constant) and components read `const s = useScaledStyles(base)`
 * — a memoized copy whose `fontSize` / `lineHeight` values are
 * multiplied by the user's chosen level. Non-text properties are
 * untouched, `scale === 1` returns the original object (zero cost for
 * the default), and layout containers keep their fixed paddings so the
 * scale changes READABILITY, not structure.
 *
 * v1 coverage (highest-read field surfaces): capture, jobs (+merged
 * header/rows/detail modal), labour summary, capture result card,
 * expense detail, settings. Remaining screens adopt the same two-line
 * pattern in later passes.
 */
export const FONT_SCALE: Record<FontScaleLevel, number> = {
  standard: 1,
  large: 1.15,
  xlarge: 1.3,
};

export function useFontScale(): number {
  return FONT_SCALE[useFontScaleStore((s) => s.level)];
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function scaleStyles<T extends Record<string, any>>(
  base: T,
  scale: number,
): T {
  if (scale === 1) return base;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const out: Record<string, any> = {};
  for (const key of Object.keys(base)) {
    const style = base[key];
    if (
      style &&
      typeof style === 'object' &&
      (typeof style.fontSize === 'number' ||
        typeof style.lineHeight === 'number')
    ) {
      out[key] = { ...style };
      if (typeof style.fontSize === 'number') {
        out[key].fontSize = Math.round(style.fontSize * scale);
      }
      if (typeof style.lineHeight === 'number') {
        out[key].lineHeight = Math.round(style.lineHeight * scale);
      }
    } else {
      out[key] = style;
    }
  }
  return out as T;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function useScaledStyles<T extends Record<string, any>>(base: T): T {
  const scale = useFontScale();
  return useMemo(() => scaleStyles(base, scale), [base, scale]);
}
