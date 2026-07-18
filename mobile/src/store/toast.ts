import { create } from 'zustand';

/**
 * Strict-parity round: cross-screen toast. The capture SHEET closes on
 * a successful single-item submit and the confirmation must appear on
 * whatever screen is underneath (spec §4: 提交 → 关层 + 顶部 toast).
 * seq forces identical texts to re-fire.
 */
type ToastState = {
  toast: { text: string; seq: number } | null;
  show: (text: string) => void;
  clear: () => void;
};

let seq = 0;

export const useToastStore = create<ToastState>((set) => ({
  toast: null,
  show: (text) => {
    seq += 1;
    set({ toast: { text, seq } });
  },
  clear: () => set({ toast: null }),
}));
