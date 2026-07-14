import React from 'react';
import { Pressable, Text, View, StyleSheet } from 'react-native';
import { useTranslation } from 'react-i18next';
import { tokens, toneFill, toneText, type Tone } from './tokens';
import { useScaledStyles } from './type';

/**
 * UI-kit v2 — React Native translation of docs/design/ui-kit-v2/
 * (the confirmed v2 preview). Design rules enforced here:
 *  - Solid primary = actions only, at most one per screen.
 *  - Selected states are tonal (sel), never solid blue.
 *  - ok/warn/bad = data status only.
 *
 * PRESENTATION-ONLY components: all money/threshold/margin values are
 * computed by the backend (or by the pre-existing call-site display
 * logic) and passed in — nothing here re-derives financial figures.
 */

/* ================= StatusBadge ================= */

/** Known status → tonal style. UNKNOWN statuses (a newer backend enum)
 *  degrade to a neutral grey badge — C-04 discipline, never crash. */
const BADGE_STYLES: Record<string, { bg: string; fg: string; border: string }> = {
  pending: { bg: tokens.warnBg, fg: tokens.warn, border: tokens.warnBorder },
  reviewed: { bg: tokens.okBg, fg: tokens.ok, border: tokens.okBorder },
  active: { bg: tokens.okBg, fg: tokens.ok, border: tokens.okBorder },
  rejected: { bg: tokens.badBg, fg: tokens.bad, border: tokens.badBorder },
  completed: { bg: tokens.lineSoft, fg: tokens.ink2, border: tokens.line },
};
const BADGE_FALLBACK = {
  bg: tokens.lineSoft,
  fg: tokens.ink2,
  border: tokens.line,
};

export function StatusBadge({
  status,
  label,
  testID,
}: {
  /** Backend enum value — used only to pick the tonal style. */
  status: string;
  /** Localized text, resolved by the CALLER (keeps the existing
   *  t(..., { defaultValue }) fallback discipline at call sites). */
  label: string;
  testID?: string;
}) {
  const s = useScaledStyles(base);
  const c = BADGE_STYLES[status] ?? BADGE_FALLBACK;
  return (
    <View
      style={[s.badge, { backgroundColor: c.bg, borderColor: c.border }]}
      testID={testID}
    >
      <Text style={[s.badgeText, { color: c.fg }]} numberOfLines={1}>
        {label}
      </Text>
    </View>
  );
}

/* ================= BudgetBar ================= */

/**
 * Threshold-coloured budget bar. `tone` comes from the CALLER (the
 * existing bandFor() uses the server-computed per-job effective
 * thresholds — operator decision: per-job values, spec's 70/90 only
 * as bandFor's own server-side fallback). `leftText` is the caller's
 * localized "$X left" / "Over by $X" line.
 */
export function BudgetBar({
  pctUsed,
  tone,
  leftText,
  testID,
}: {
  pctUsed: number;
  tone: Tone;
  leftText: string;
  testID?: string;
}) {
  const s = useScaledStyles(base);
  const { t } = useTranslation();
  // NaN-safe: corrupt server pct must not surface "NaN% used".
  const safePct = Number.isFinite(pctUsed) ? pctUsed : 0;
  // Label shows the RAW pct (an overspent job reads "143% used");
  // only the FILL width clamps to the track.
  const width = Math.min(100, Math.max(0, safePct));
  return (
    <View testID={testID}>
      <View style={s.barTrack}>
        <View
          style={[
            s.barFill,
            { width: `${width}%`, backgroundColor: toneFill[tone] },
          ]}
        />
      </View>
      <View style={s.barRow}>
        <Text style={s.barLeftText} numberOfLines={1}>
          {leftText}
        </Text>
        <Text style={[s.barPctText, { color: toneText[tone] }]}>
          {t('ui.pct_used', { pct: Math.round(safePct) })}
        </Text>
      </View>
    </View>
  );
}

/* ================= Chip ================= */

export function Chip({
  selected,
  label,
  onPress,
  disabled,
  testID,
}: {
  selected?: boolean;
  label: string;
  onPress?: () => void;
  disabled?: boolean;
  testID?: string;
}) {
  const s = useScaledStyles(base);
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityState={{ selected: !!selected }}
      style={[s.chip, selected ? s.chipSelected : null, disabled ? s.dim : null]}
      testID={testID}
    >
      <Text
        style={[s.chipText, selected ? s.chipTextSelected : null]}
        numberOfLines={1}
      >
        {label}
      </Text>
    </Pressable>
  );
}

/* ================= Segmented ================= */

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  testID,
}: {
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  onChange: (v: T) => void;
  testID?: string;
}) {
  const s = useScaledStyles(base);
  return (
    <View style={s.segmented} accessibilityRole="tablist" testID={testID}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <Pressable
            key={o.value}
            onPress={() => onChange(o.value)}
            accessibilityRole="tab"
            accessibilityState={{ selected: on }}
            style={[s.segment, on ? s.segmentOn : null]}
          >
            <Text style={[s.segmentText, on ? s.segmentTextOn : null]}>
              {o.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

/* ================= PrimaryButton ================= */

/** The single solid-blue action per screen. */
export function PrimaryButton({
  label,
  onPress,
  disabled,
  testID,
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  testID?: string;
}) {
  const s = useScaledStyles(base);
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      style={({ pressed }) => [
        s.primaryBtn,
        pressed ? s.primaryBtnPressed : null,
        disabled ? s.dim : null,
      ]}
      testID={testID}
    >
      <Text style={s.primaryBtnText}>{label}</Text>
    </Pressable>
  );
}

/* ================= styles ================= */

const base = StyleSheet.create({
  // badge — no alignSelf: vertical centring comes from each host
  // row's alignItems (review finding: flex-start top-pinned the badge
  // in every centred host row).
  badge: {
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  badgeText: { fontSize: 10.5, fontWeight: '700' },

  // budget bar
  barTrack: {
    height: 6,
    borderRadius: 999,
    backgroundColor: tokens.barTrack,
    overflow: 'hidden',
  },
  barFill: { height: '100%', borderRadius: 999 },
  barRow: {
    marginTop: 6,
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
  },
  barLeftText: {
    fontSize: 12.5,
    color: tokens.ink2,
    fontVariant: ['tabular-nums'],
    flexShrink: 1,
  },
  barPctText: {
    fontSize: 12,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
    marginLeft: 8,
  },

  // chip
  chip: {
    borderRadius: 999,
    borderWidth: 1,
    borderColor: tokens.line,
    backgroundColor: '#ffffff',
    paddingHorizontal: 14,
    paddingVertical: 6,
  },
  chipSelected: {
    borderColor: tokens.selBorder,
    backgroundColor: tokens.sel,
  },
  chipText: { fontSize: 12.5, fontWeight: '500', color: tokens.ink2 },
  chipTextSelected: { fontWeight: '600', color: tokens.selText },

  // segmented
  segmented: {
    flexDirection: 'row',
    gap: 2,
    borderRadius: 11,
    backgroundColor: tokens.lineSoft,
    padding: 3,
  },
  // Every segment carries the border (transparent when unselected) so
  // selection never shifts layout by the border width — the web kit
  // uses a non-layout inset ring; this is the RN equivalent.
  segment: {
    flex: 1,
    borderRadius: 8,
    paddingVertical: 6,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'transparent',
  },
  segmentOn: {
    backgroundColor: tokens.sel,
    borderColor: tokens.selBorder,
  },
  segmentText: { fontSize: 12, fontWeight: '600', color: tokens.ink2 },
  segmentTextOn: { color: tokens.selText },

  // primary button
  primaryBtn: {
    width: '100%',
    borderRadius: 12,
    backgroundColor: tokens.primary,
    paddingHorizontal: 16,
    paddingVertical: 12,
    alignItems: 'center',
  },
  primaryBtnPressed: { backgroundColor: tokens.primaryDeep },
  primaryBtnText: { color: '#ffffff', fontSize: 14.5, fontWeight: '700' },

  dim: { opacity: 0.4 },
});
