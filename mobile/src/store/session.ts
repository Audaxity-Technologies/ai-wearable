/**
 * Signed-in state.
 *
 * The token is kept in AsyncStorage rather than SecureStore for now — a
 * deliberate, documented trade: SecureStore is unavailable on web, which is
 * where this app is previewed, and the backend is a personal server on a LAN.
 * Move to expo-secure-store before this is used on anything shared.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { useEffect, useState } from 'react';
import { create } from 'zustand';

const STORAGE_KEY = 'audaxity.session.v1';

export interface Account {
  userId: string;
  email: string;
  displayName: string;
  tz: string;
}

interface SessionState {
  token: string | null;
  account: Account | null;
  hydrated: boolean;
  signIn: (token: string, account: Account) => Promise<void>;
  signOut: () => Promise<void>;
  hydrate: () => Promise<void>;
}

export const useSession = create<SessionState>((set, get) => ({
  token: null,
  account: null,
  hydrated: false,

  hydrate: async () => {
    try {
      const raw = await AsyncStorage.getItem(STORAGE_KEY);
      if (raw) {
        const stored = JSON.parse(raw) as {
          token: string;
          account: Account;
        };
        set({ token: stored.token, account: stored.account, hydrated: true });
        return;
      }
    } catch {
      // A corrupt store must not lock the user out of signing in again.
    }
    set({ hydrated: true });
  },

  signIn: async (token, account) => {
    set({ token, account });
    try {
      await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify({ token, account }));
    } catch {
      // Keep the in-memory session; it just will not survive a restart.
    }
  },

  signOut: async () => {
    set({ token: null, account: null });
    try {
      await AsyncStorage.removeItem(STORAGE_KEY);
    } catch {
      // Nothing useful to do; the in-memory session is already cleared.
    }
  },
}));

export function useIsSignedIn(): boolean {
  return useSession((s) => s.hydrated && !!s.token);
}

export function useHydrateSession(): boolean {
  const hydrated = useSession((s) => s.hydrated);
  const hydrate = useSession((s) => s.hydrate);
  const [started, setStarted] = useState(false);

  useEffect(() => {
    if (!started) {
      setStarted(true);
      void hydrate();
    }
  }, [started, hydrate]);

  return hydrated;
}

/** Keep `get()` referenced so the store is not tree-shaken in web builds. */
export const peekToken = (): string | null => useSession.getState().token;
