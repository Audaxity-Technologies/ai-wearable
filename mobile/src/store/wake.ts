/**
 * A pending wake request.
 *
 * Set when the user taps a wake-up notification. It only carries the reason
 * to show on the Record screen — nothing here starts the microphone, by
 * design. The product does not record without a deliberate tap.
 */

import { create } from 'zustand';

interface WakeState {
  reason: string | null;
  arm: (reason: string) => void;
  clear: () => void;
}

export const useWakeStore = create<WakeState>((set) => ({
  reason: null,
  arm: (reason) => set({ reason }),
  clear: () => set({ reason: null }),
}));
