/**
 * forey design tokens (design_handoff_forey_redesign/README.md §Design
 * Tokens, translated 1:1 for React Native). Supersedes the UI-kit v2
 * palette — key NAMES kept stable so the whole app re-skins by value.
 *
 * Rules (from the design spec):
 *  - Solid `primary` is for ACTIONS ONLY (FAB / CTA / links) — at most
 *    one solid-primary CTA per screen.
 *  - Selected states are always tonal: sel / selText / selBorder.
 *  - Colour discipline: amber = pending/missing-data ONLY, green =
 *    done/healthy ONLY, blue = action/selected ONLY, red = destructive
 *    ONLY.
 *  - `ok`/`warn`/`bad` are TEXT-safe deep shades (badges, labels).
 *  - `okFill` (buttons/dots) and `warnFill` (BARS ONLY) are the
 *    saturated fills. warnFill #F59E0B fails AA as a glyph stroke —
 *    icons/chevrons use `warnMid`. `bad` serves as both text and fill.
 */
export const tokens = {
  // surfaces
  bg: '#F4F5F7', // app ground (cards are white, ground is not)
  surface: '#FFFFFF', // cards / sheets
  surfaceSub: '#F8F9FB', // sheet base / input wells / nested blocks

  // action
  primary: '#2563EB',
  primaryDeep: '#1D4ED8', // pressed

  // selected (tonal)
  sel: '#EFF6FF',
  selText: '#1D4ED8',
  selBorder: '#BFDBFE',

  // data status — text-safe deep shades
  ok: '#0E7A46', // green badge text / healthy percentages
  okFill: '#12B76A', // "approve" buttons / saved dots
  okBg: '#EAF7F0',
  okBorder: '#BCE8D2',
  warn: '#8A5A0B', // amber badge text
  warnMid: '#B45309', // amber explanatory text / hot percentages
  warnFill: '#F59E0B', // budget warning bars / missing-data bars
  warnBg: '#FFF7E8',
  warnBorder: '#F6DFAE',
  bad: '#B42318', // reject / delete / destructive text
  badDeep: '#8B2C24', // duplicate-warning body text
  badBg: '#FEF1F0',
  badBorder: '#F5C6C2',

  // neutrals
  ink: '#101828',
  ink2: '#475467',
  ink3: '#667085',
  muted: '#98A2B3', // auxiliary / placeholders
  disabled: '#D0D5DD', // unticked boxes / disabled
  line: '#E7EAEF', // card borders (1px)
  lineSoft: '#F2F4F7', // in-card dividers
  inputBorder: '#EEF1F5', // nested hint blocks
  // Control tint that must stay visible ON the grey ground (#F4F5F7):
  // segmented tracks, the login language pill. lineSoft #F2F4F7 is
  // within 2 RGB points of the ground and vanishes there.
  segTrack: '#EAECEF',
  barTrack: '#EEF1F5',

  // categorical palette (cost composition; per the forey spec 材料
  // deliberately shares the action blue — supersedes the v2 rule)
  cat1: '#2563EB', // 材料 materials
  cat2: '#38BDF8', // 人工 labour
  cat3: '#F59E0B', // 分包 subbies
  cat4: '#E4E7EC', // 其他 other
} as const;

export type Tone = 'ok' | 'warn' | 'bad';

// NOTE: no budgetTone() helper here on purpose (review finding): the
// ONLY banding source of truth is jobs.tsx bandFor(), which uses the
// SERVER-computed per-job effective thresholds + the overspend
// override. A helper with spec-default 70/90 would be a second,
// drift-prone source. If a future batch needs shared banding, it must
// take the server thresholds as REQUIRED params.

export const toneText: Record<Tone, string> = {
  ok: tokens.ok,
  warn: tokens.warnMid,
  bad: tokens.bad,
};

export const toneFill: Record<Tone, string> = {
  ok: tokens.okFill,
  warn: tokens.warnFill,
  bad: tokens.bad,
};
