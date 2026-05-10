/**
 * Phase 3 Lite+ — GST conversion helpers (UI-only).
 *
 * Storage stays ex-GST canonical per A2-lite (Path A operator decision,
 * 2026-05-10). The backend never sees an inc-GST budget figure; the UI
 * accepts whichever basis the user prefers and converts to ex before
 * submitting. This module is the **single source** of that conversion
 * — `<GstAmountInput>` and any other caller that needs to flip a money
 * value between bases must go through these functions, never inline its
 * own ÷1.1.
 *
 * Conversion rule (frozen by the plan):
 *
 * * AU 10% GST. `inc = ex × 1.1`, `ex = inc ÷ 1.1`.
 * * Every conversion result quantizes to two decimal places via
 *   `Number(...).toFixed(2)`. Results come back as Decimal-strings so
 *   the rest of the app can treat them the same way as backend
 *   Decimal serialisation.
 * * This is an aggregate-planning conversion — it is NOT subject to
 *   the cash-payment GST rule (which applies per expense row in
 *   Phase 2; budgets and contracts have no payment method).
 *
 * Precision: JS Number has 15–16 significant digits, which is enough
 * for amounts up to ~$10,000,000.00 with two-decimal output. Beyond
 * that, accumulated float error could push the second decimal off by
 * one cent. SiteTracker contract values are well below that bound;
 * if we ever store nine-figure amounts we'll need a Decimal library
 * in the browser.
 *
 * Round-trip is **not** exact. `exToInc(incToEx(x))` may differ from
 * `x` by up to one cent because each step quantizes to 0.01
 * independently. The component never chains conversions during a
 * single user action — only when the user explicitly toggles basis.
 *
 * Empty input handling: an empty string in returns an empty string out.
 * Lets the caller pass through "no value entered yet" without forcing
 * a special-case branch.
 */

const GST_FACTOR = 1.1

/** Format a Decimal-style string to 2 decimals. Returns "" for invalid / empty. */
function toMoneyString(n: number): string {
  if (!Number.isFinite(n)) return ""
  return n.toFixed(2)
}

function parseMoney(input: string): number | null {
  const trimmed = input.trim()
  if (trimmed === "") return null
  const n = Number(trimmed)
  return Number.isFinite(n) ? n : null
}

/**
 * Convert an inc-GST string to an ex-GST string. Quantize to 0.01.
 *
 * Worked examples:
 *
 * * `incToEx("0")` → `"0.00"`
 * * `incToEx("0.01")` → `"0.01"` (`0.01 / 1.1 = 0.00909…` → rounds to `0.01`)
 * * `incToEx("100")` → `"90.91"` (`100 / 1.1 = 90.9090…` → rounds to `0.01`)
 * * `incToEx("110")` → `"100.00"` (exact)
 * * `incToEx("1100")` → `"1000.00"` (exact)
 * * `incToEx("207900")` → `"189000.00"` (exact; matches the user's
 *   common pattern of entering inc on a $189k ex contract)
 * * `incToEx("")` → `""` (pass-through for empty input)
 */
export function incToEx(inc: string): string {
  const n = parseMoney(inc)
  if (n === null) return ""
  return toMoneyString(n / GST_FACTOR)
}

/**
 * Convert an ex-GST string to an inc-GST string. Quantize to 0.01.
 *
 * Worked examples:
 *
 * * `exToInc("0")` → `"0.00"`
 * * `exToInc("90.91")` → `"100.00"` (`90.91 × 1.1 = 100.001` → rounds to `0.01`).
 *   **Note the round-trip drift:** `incToEx("100")` returns `"90.91"`,
 *   `exToInc("90.91")` returns `"100.00"` — back to the original by
 *   coincidence, but `incToEx("100.50")` then `exToInc(...)` may not be.
 *   The component does not chain conversions during a single user action.
 * * `exToInc("100")` → `"110.00"` (exact)
 * * `exToInc("1000")` → `"1100.00"` (exact)
 * * `exToInc("189000")` → `"207900.00"` (exact)
 * * `exToInc("")` → `""` (pass-through)
 */
export function exToInc(ex: string): string {
  const n = parseMoney(ex)
  if (n === null) return ""
  return toMoneyString(n * GST_FACTOR)
}

/**
 * GST basis selector — `'ex'` is the canonical basis used everywhere
 * else in the system; `'inc'` is the user-facing input convenience.
 */
export type GstBasis = "ex" | "inc"

/** Default basis on every form, frozen by the plan. */
export const DEFAULT_GST_BASIS: GstBasis = "ex"
