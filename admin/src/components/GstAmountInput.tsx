/**
 * Phase 3 Lite+ — money input with an inc/ex GST basis toggle.
 *
 * Wraps a numeric input + a two-state toggle (`[ex] [inc]`) + a live
 * conversion hint. The component owns the basis + display state
 * internally; the parent only sees the canonical ex-GST string. This
 * matches the operator constraint that storage stays ex-GST and all
 * conversion goes through the single helper in `lib/gst.ts`.
 *
 * Behaviour:
 *
 * * On mount: basis is `'ex'` (frozen default per the plan); the
 *   displayed value equals the parent-supplied `value` prop, which is
 *   the canonical ex-GST string.
 * * User typing in `'ex'` mode: the typed string is the canonical
 *   value; `onChange` fires with it verbatim.
 * * User typing in `'inc'` mode: the typed string is treated as
 *   inc-GST; `onChange` fires with the converted ex-GST string. A
 *   live hint underneath the input shows the conversion result so the
 *   user can sanity-check before submit.
 * * Basis toggle: the displayed number is converted to the new basis
 *   so the user's intent is preserved (`110` typed in inc, then
 *   toggled to ex, displays `100.00`). The canonical ex value emitted
 *   to the parent is kept consistent across the toggle.
 * * Empty input: emits `""` upstream — callers send `null` to the
 *   API for that field.
 *
 * The component is intentionally **not** synced with parent updates
 * after mount — the only writers in Batch 2 are the forms themselves,
 * and a successful PATCH triggers TanStack Query invalidation that
 * remounts the parent form. If a future caller needs external resync,
 * they can pass a `key` prop tied to the canonical value.
 */
import { useState } from "react"
import { useTranslation } from "react-i18next"
import {
  DEFAULT_GST_BASIS,
  exToInc,
  GstBasis,
  incToEx,
} from "../lib/gst"

type Props = {
  /** Canonical ex-GST value as a Decimal-string (`""` for none). */
  value: string
  /** Called with the canonical ex-GST string on every change. `""` for empty. */
  onChange: (exGst: string) => void
  /** Field label (already translated). */
  label: string
  /** Optional input id for label association. */
  id?: string
  /** HTML required flag. */
  required?: boolean
  /** Initial basis on mount. Defaults to `'ex'` per the frozen plan default. */
  defaultBasis?: GstBasis
  /** Disable the input + toggle. */
  disabled?: boolean
}

export function GstAmountInput({
  value,
  onChange,
  label,
  id,
  required,
  defaultBasis = DEFAULT_GST_BASIS,
  disabled,
}: Props) {
  const { t } = useTranslation()
  const [basis, setBasis] = useState<GstBasis>(defaultBasis)
  // The displayed string in the input. On mount this is the parent's
  // ex-GST value (since basis defaults to 'ex'). It diverges from the
  // canonical ex value only when the user is typing in 'inc' mode.
  const [displayValue, setDisplayValue] = useState<string>(value)

  const handleInput = (input: string) => {
    setDisplayValue(input)
    if (input.trim() === "") {
      onChange("")
      return
    }
    onChange(basis === "ex" ? input : incToEx(input))
  }

  const handleBasisToggle = (next: GstBasis) => {
    if (next === basis) return
    if (displayValue.trim() === "") {
      // Nothing to convert — just flip the basis.
      setBasis(next)
      return
    }
    // Preserve the user's intent across the toggle by converting the
    // displayed number into the new basis. The canonical ex value
    // stays consistent (it was always ex underneath).
    if (next === "ex") {
      // Was 'inc' — current display is inc; converted value is ex.
      const converted = incToEx(displayValue)
      setDisplayValue(converted)
      onChange(converted)
    } else {
      // Was 'ex' — current display IS the canonical ex; convert to inc
      // for display purposes only. Canonical value emitted upward stays
      // the original ex.
      setDisplayValue(exToInc(displayValue))
      // onChange not called — canonical value unchanged.
    }
    setBasis(next)
  }

  // Live conversion hint: only when basis is 'inc' and there is a value.
  const hintExValue =
    basis === "inc" && displayValue.trim() !== "" ? incToEx(displayValue) : null

  const toggleBtnBase =
    "px-2 py-1 text-xs font-medium border border-slate-300 first:rounded-l-md last:rounded-r-md focus:outline-none focus:ring-2 focus:ring-slate-500"
  const toggleBtnActive = "bg-slate-900 text-white border-slate-900"
  const toggleBtnIdle = "bg-white text-slate-700 hover:bg-slate-50"

  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-slate-700 mb-1">
        {label}
        {required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="number"
          step="0.01"
          min="0"
          required={required}
          disabled={disabled}
          value={displayValue}
          onChange={(e) => handleInput(e.target.value)}
          className="flex-1 border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-500 disabled:bg-slate-50"
        />
        <div role="group" aria-label={t("gst.basis_toggle_aria")} className="inline-flex">
          <button
            type="button"
            disabled={disabled}
            onClick={() => handleBasisToggle("ex")}
            className={`${toggleBtnBase} ${basis === "ex" ? toggleBtnActive : toggleBtnIdle}`}
            aria-pressed={basis === "ex"}
          >
            {t("gst.basis_ex")}
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={() => handleBasisToggle("inc")}
            className={`${toggleBtnBase} ${basis === "inc" ? toggleBtnActive : toggleBtnIdle}`}
            aria-pressed={basis === "inc"}
          >
            {t("gst.basis_inc")}
          </button>
        </div>
      </div>
      {hintExValue !== null && (
        <div className="text-xs text-slate-600 mt-1 tabular-nums">
          {t("gst.ex_hint", { value: hintExValue })}
        </div>
      )}
    </div>
  )
}
