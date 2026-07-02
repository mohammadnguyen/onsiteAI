/**
 * O2-C (U1): CJK detection for bilingual label fallbacks.
 *
 * The operator's real-world data keeps the Chinese job identity in the
 * job CODE ("晶晶") and in aliases created from mobile (which historically
 * carried no language_code). Chip/picker labels use this to prefer any
 * Chinese-looking identity for zh-locale users.
 *
 * Range covers CJK Unified Ideographs + Extension A + the common
 * fullwidth punctuation block — enough for "does this read as Chinese",
 * not a linguistic classifier.
 */
export function hasCJK(value: string | null | undefined): boolean {
  if (!value) return false;
  return /[㐀-䶿一-鿿＀-￯]/.test(value);
}
