/**
 * Phase 3 Lite++ — total budget field with auto/manual radio toggle.
 *
 * Replaces the bare ``<GstAmountInput>`` for total_budget on both the
 * New Job modal (`Jobs.tsx`) and the Job Settings form
 * (`JobDetail.tsx`). Encapsulates the auto/manual mode state machine so
 * the parent forms only see a controlled (value, onChange) pair —
 * exactly the same surface as the previous bare input.
 *
 * Behaviour summary (frozen by docs/phase-3-lite-plus-plus-plan.md):
 *
 * * Radio buttons appear when ``targetProfitRatioPct`` is set:
 *     ◉ Auto-calculate from target profit  ◯ Manual budget
 *   Default selection on mount: see ``initialMode`` resolution below.
 * * **Auto mode**: budget input is locked (disabled). The displayed
 *   value is ``contract × (1 − target/100)``, recomputed live on every
 *   change of contract or target. The component calls ``onChange``
 *   upward whenever the auto-calc value changes so the parent's
 *   submit body stays in sync without an extra effect in the parent.
 * * **Manual mode**: input is editable; the user types whatever they
 *   want. An inline message under the input shows the delta against
 *   ``target_cost_limit_ex_gst`` plus the implied effective margin —
 *   the warning surface the operator review specified.
 * * **Mode flips**:
 *     - Manual → Auto: the input snaps to the auto-calc value; any
 *       previously typed manual draft is discarded silently. The
 *       radio click is the user's explicit consent; Save is the only
 *       commit point.
 *     - Auto → Manual: input becomes editable with the auto-calc
 *       value preserved as the starting draft.
 * * **Sibling clearing**: if contract or target becomes empty while
 *   the user is in auto mode, the component flips itself to manual
 *   mode (auto-calc is no longer possible) but **preserves the last-
 *   displayed value as a draft**. The "Auto" radio becomes disabled
 *   with a hint; once contract + target are both set again, the user
 *   can flip back to auto.
 * * **No initial-mode flicker after mount**: ``initialMode`` is read
 *   exactly once on first render (via ``useState`` initialiser); it
 *   does not re-derive from later prop changes.
 *
 * GST canonicalization: this component never sees inc-GST. Both the
 * ``contractValueExGst`` prop and the ``value`` prop are expected to
 * be canonical ex-GST Decimal-strings — the parent's
 * ``<GstAmountInput>`` for contract value already converts inc → ex
 * before emitting. The auto-calc therefore always operates on ex-GST,
 * matching the operator constraint.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { GstAmountInput } from './GstAmountInput'
import {
  budgetsMatchToCent,
  calcEffectiveMarginPct,
  calcTargetCostLimit,
  formatMoney,
  formatPercent,
} from '../lib/budget'

export type TotalBudgetMode = 'auto' | 'manual'

type Props = {
  /** Canonical ex-GST contract value as a Decimal-string ("" for none). */
  contractValueExGst: string
  /** Target profit ratio as a percent string ("15.00"); "" for none. */
  targetProfitRatioPct: string
  /** Canonical ex-GST budget value as a Decimal-string ("" for none). */
  value: string
  /** Called with the canonical ex-GST budget on every change. */
  onChange: (exGst: string) => void
  /**
   * Initial mode on mount. Used by `JobSettingsForm` to honour
   * existing stored data (`'auto'` if budget matches calc; `'manual'`
   * if it differs or target is null). When omitted, the component
   * picks `'auto'` if both contract+target are set on mount,
   * `'manual'` otherwise — matches the New Job modal default.
   */
  initialMode?: TotalBudgetMode
}

export function TotalBudgetField({
  contractValueExGst,
  targetProfitRatioPct,
  value,
  onChange,
  initialMode,
}: Props) {
  const { t } = useTranslation()

  // Whether auto-calc is *possible* right now (both inputs set + numeric).
  const autoCalcValue = calcTargetCostLimit(
    contractValueExGst,
    targetProfitRatioPct,
  )
  const autoPossible = autoCalcValue !== ''

  // Two pieces of state govern the mode:
  //
  // * ``mode`` — what the form is rendering right now ('auto' or 'manual').
  // * ``userPickedMode`` — has the human ever clicked one of the radios?
  //
  // Resolution rules:
  //
  // * If ``initialMode`` is supplied (settings form, with loaded job data),
  //   we treat the parent's choice as a "pick" — the user inherits that
  //   mode and auto-default does not override it.
  // * If ``initialMode`` is NOT supplied (new-job modal), we start in
  //   manual but auto-default kicks in the first time both contract +
  //   target are populated. Once the user clicks a radio explicitly,
  //   auto-default stops firing.
  //
  // Once-per-render: ``mode`` is a controlled state owned by this
  // component; we never re-derive from props directly.
  const [mode, setMode] = useState<TotalBudgetMode>(initialMode ?? 'manual')
  const [userPickedMode, setUserPickedMode] = useState<boolean>(
    initialMode !== undefined,
  )

  // Auto-default for the new-job modal flow: when the user hasn't yet
  // expressed a mode preference and they've now filled in both
  // contract + target, switch to auto mode. This satisfies the
  // operator rule "auto mode is the default" for new jobs without
  // breaking the settings-form case (where ``initialMode`` is set, so
  // ``userPickedMode`` starts true and this effect is a no-op).
  useEffect(() => {
    if (!userPickedMode && autoPossible && mode !== 'auto') {
      setMode('auto')
    }
  }, [userPickedMode, autoPossible, mode])

  // Sibling clearing: if contract or target becomes empty while we
  // are in auto mode, flip to manual (auto-calc is no longer
  // possible). The current input value stays as a draft per the
  // operator constraint — do NOT wipe it. This flip does NOT count
  // as a user pick — it's a forced fallback. Re-entering auto-
  // possible later may flip back to auto if the user hadn't picked.
  useEffect(() => {
    if (!autoPossible && mode === 'auto') {
      setMode('manual')
    }
  }, [autoPossible, mode])

  // In auto mode, keep the parent's value in lockstep with the live
  // computed auto-calc. This runs whenever contract/target change.
  // Guarded by a cents-equality check so we don't fire onChange in a
  // loop (the parent's setter will re-render us with the same value).
  useEffect(() => {
    if (mode !== 'auto') return
    if (!autoPossible) return
    if (!budgetsMatchToCent(value, autoCalcValue)) {
      onChange(autoCalcValue)
    }
  }, [mode, autoPossible, autoCalcValue, value, onChange])

  // Radio click handlers — explicit picks pin ``userPickedMode`` so the
  // auto-default effect stops firing.
  const selectAuto = () => {
    if (!autoPossible) return // disabled
    setMode('auto')
    setUserPickedMode(true)
    // Snap the budget to the auto-calc value (the useEffect above will
    // also do this on the next render, but doing it here avoids a
    // visible one-frame mismatch between the radio click and the
    // input value update).
    if (!budgetsMatchToCent(value, autoCalcValue)) {
      onChange(autoCalcValue)
    }
  }
  const selectManual = () => {
    setMode('manual')
    setUserPickedMode(true)
    // Keep current value as the starting editable draft. No onChange
    // call needed — value already reflects the displayed number.
  }

  // Override-warning content (manual mode only, when both siblings set).
  const showOverrideMessage =
    mode === 'manual' &&
    autoPossible &&
    value.trim() !== ''
  const overrideMessage = (() => {
    if (!showOverrideMessage) return null
    const budget = Number(value)
    const calc = Number(autoCalcValue)
    if (!Number.isFinite(budget) || !Number.isFinite(calc)) return null
    const deltaCents = Math.round(budget * 100) - Math.round(calc * 100)
    if (deltaCents === 0) {
      return { kind: 'match', text: t('budget.override_info_match') }
    }
    const absDelta = formatMoney((Math.abs(deltaCents) / 100).toFixed(2))
    if (deltaCents > 0) {
      // Budget exceeds target cost limit → lower margin than target.
      const effective = calcEffectiveMarginPct(contractValueExGst, value)
      return {
        kind: 'over',
        text: t('budget.override_warning_over', {
          delta: absDelta,
          ratio: formatPercent(effective),
          target: formatPercent(targetProfitRatioPct),
        }),
      }
    }
    return {
      kind: 'under',
      text: t('budget.override_info_under', { delta: absDelta }),
    }
  })()

  const messageClass = (() => {
    if (!overrideMessage) return ''
    if (overrideMessage.kind === 'over')
      return 'text-xs text-amber-700 mt-1'
    if (overrideMessage.kind === 'match')
      return 'text-xs text-emerald-700 mt-1'
    return 'text-xs text-slate-600 mt-1'
  })()

  return (
    <div>
      <label className="block text-sm font-medium text-slate-700 mb-1">
        {t('jobs.total_budget_input')}
      </label>

      {/* Mode selector — radios visible whenever we have a target set, so
          the user can see the 'Auto' option even if it's disabled (waiting
          for contract value to be filled in). */}
      {targetProfitRatioPct.trim() !== '' && (
        <div
          role="radiogroup"
          aria-label={t('jobs.total_budget_input')}
          className="flex flex-wrap gap-3 mb-2 text-sm"
        >
          <label className="inline-flex items-center gap-1.5">
            <input
              type="radio"
              name="budget-mode"
              value="auto"
              checked={mode === 'auto'}
              onChange={selectAuto}
              disabled={!autoPossible}
              className="accent-slate-900"
            />
            <span
              className={
                autoPossible ? 'text-slate-700' : 'text-slate-400'
              }
            >
              {t('budget.mode_auto')}
            </span>
          </label>
          <label className="inline-flex items-center gap-1.5">
            <input
              type="radio"
              name="budget-mode"
              value="manual"
              checked={mode === 'manual'}
              onChange={selectManual}
              className="accent-slate-900"
            />
            <span className="text-slate-700">{t('budget.mode_manual')}</span>
          </label>
        </div>
      )}

      {/* In auto mode the input is purely informational (the user can't
          edit it; the formula hint underneath explains the number). We
          render a plain disabled input styled to match the other money
          fields rather than using <GstAmountInput> here, because the
          GstAmountInput's internal display-state was designed for user
          typing and doesn't sync back when a parent pushes a new value
          via onChange. The GST inc/ex toggle is also irrelevant when
          the input is locked — the value shown is the canonical ex-GST
          number that will be saved.
          In manual mode we use the full <GstAmountInput> so the user
          can enter inc-GST and have it converted on submit. */}
      {mode === 'auto' ? (
        <input
          type="text"
          readOnly
          disabled
          value={value === '' ? '' : formatMoney(value)}
          aria-label={t('jobs.total_budget_input')}
          className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm bg-slate-50 text-slate-700 tabular-nums"
        />
      ) : (
        <GstAmountInput label="" value={value} onChange={onChange} />
      )}

      {/* Auto-calc formula hint (auto mode only). */}
      {mode === 'auto' && autoPossible && (
        <div className="text-xs text-slate-600 mt-1 tabular-nums">
          {t('budget.auto_formula_hint', {
            contract: formatMoney(contractValueExGst),
            target: formatPercent(targetProfitRatioPct),
            result: formatMoney(autoCalcValue),
          })}
        </div>
      )}

      {/* Hint shown when 'Auto' radio is disabled (target set but contract
          missing — the radio is visible so the user knows the option
          exists, but unselectable until contract is filled). */}
      {targetProfitRatioPct.trim() !== '' && !autoPossible && (
        <div className="text-xs text-slate-500 mt-1">
          {t('budget.auto_disabled_hint')}
        </div>
      )}

      {/* Override consequence message (manual mode only). */}
      {overrideMessage && (
        <div className={messageClass}>{overrideMessage.text}</div>
      )}
    </div>
  )
}
