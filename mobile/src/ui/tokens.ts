/**
 * UI-kit v2 semantic colour tokens (docs/design/ui-kit-v2/theme.css,
 * translated 1:1 for React Native).
 *
 * Rules (from the design spec):
 *  - Solid `primary` is for ACTIONS ONLY (Submit / Save / +New / the
 *    central ➕) — at most one per screen.
 *  - Selected states are always tonal: sel / selText / selBorder,
 *    never solid blue.
 *  - ok / warn / bad express DATA STATUS only, never interaction.
 *  - cat1–cat4 are for categorical charts (avoid the action blue).
 */
export const tokens = {
  // action
  primary: '#2563EB',
  primaryDeep: '#1D4ED8', // pressed

  // selected (tonal)
  sel: '#EFF6FF',
  selText: '#1D4ED8',
  selBorder: '#BFDBFE',

  // data status
  ok: '#16A34A',
  okBg: '#F0FDF4',
  okBorder: '#BBF7D0',
  warn: '#B45309', // text (AA-safe)
  warnFill: '#D97706', // bars / icons
  warnBg: '#FFFBEB',
  warnBorder: '#FDE68A',
  bad: '#DC2626',
  badBg: '#FEF2F2',
  badBorder: '#FECACA',

  // neutrals
  ink: '#0F172A',
  ink2: '#475569',
  ink3: '#94A3B8',
  line: '#E2E8F0',
  lineSoft: '#F1F5F9',
  barTrack: '#EDF1F6',

  // categorical palette
  cat1: '#16A34A',
  cat2: '#0D9488',
  cat3: '#EA580C',
  cat4: '#7C3AED',
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
  warn: tokens.warn,
  bad: tokens.bad,
};

export const toneFill: Record<Tone, string> = {
  ok: tokens.ok,
  warn: tokens.warnFill,
  bad: tokens.bad,
};
