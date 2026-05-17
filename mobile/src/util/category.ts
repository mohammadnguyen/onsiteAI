/**
 * Category-name localization helper for the mobile surface.
 *
 * Mobile Polish slice (Half A): keeps category names readable in
 * Chinese mode without touching the backend ``categories`` table or
 * adding a schema column. The mobile i18n bundle carries a
 * ``category_label.<English Name>`` block; this helper looks up the
 * incoming English ``category_name`` in that table and falls back to
 * the English name unchanged if the translation is missing. That
 * fallback is deliberate: any future category added to the backend
 * seed continues to render (just in English) instead of producing a
 * raw i18n key or a blank string in the UI.
 *
 * Backend coupling: the lookup key IS the canonical English
 * ``category_name`` as seeded by
 * ``backend/app/core/seed.py:BUILDER_CATEGORIES``. If the seed
 * renames a category (e.g. ``"Plumbing"`` → ``"Plumbing & Drainage"``),
 * the i18n table must be updated in the same change set; until it is,
 * the affected category silently degrades to English.
 */

import type { TFunction } from 'i18next';

/**
 * Look up a Chinese label for a builder-category name, falling back
 * to the original English name if no translation is registered.
 *
 * Returns the em-dash placeholder ``"—"`` when the input is null,
 * undefined, or the empty string — mirroring the convention used in
 * ``formatMoney``.
 */
export function localizeCategoryName(
  name: string | null | undefined,
  t: TFunction,
): string {
  if (name === null || name === undefined || name === '') return '—';
  return t(`category_label.${name}`, { defaultValue: name });
}
