/**
 * Phase 3 Lite+ — global GST display preference (per browser).
 *
 * Three modes, frozen by ``docs/phase-3-lite-plus-plan.md``:
 *
 * * `'ex'` (default) — budget tiles show only the ex-GST primary value.
 *   Matches Phase 3 Lite behaviour exactly.
 * * `'both'` — budget tiles show ex-GST primary + a small secondary
 *   line "= $X inc GST".
 * * `'inc'` — budget tiles show inc-GST as primary (label flips to
 *   "Budget inc GST" / "Remaining inc GST"); ex-GST is the secondary.
 *
 * Spent tiles already show inc + ex side by side per Phase 3 Lite —
 * this preference does not touch them.
 *
 * Persisted with Zustand's `persist` middleware (same pattern as
 * `useAuthStore`) so the choice survives reloads. Storage is per
 * browser; the operator narrow-scope decision deliberately defers
 * cross-device user-pref to a future phase.
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type GstDisplayMode = 'ex' | 'both' | 'inc'

type State = {
  mode: GstDisplayMode
  setMode: (mode: GstDisplayMode) => void
}

export const useGstDisplay = create<State>()(
  persist(
    (set) => ({
      mode: 'ex',
      setMode: (mode) => set({ mode }),
    }),
    { name: 'sitetracker-admin-gst-display' },
  ),
)
