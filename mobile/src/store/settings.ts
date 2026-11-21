/**
 * App settings, persisted on the device.
 *
 * `baseUrl` is the one that matters in development: the phone cannot reach
 * "localhost", so it must point at the laptop's LAN address (or a Cloudflare
 * tunnel). Defaulting it to localhost would fail confusingly on a real phone,
 * so the app shows an explicit "set your server" state instead.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { useCallback, useEffect, useState } from 'react';
import { create } from 'zustand';

const STORAGE_KEY = 'audaxity.settings.v1';

function newDeviceId(): string {
  return `dev-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}

export interface Settings {
  baseUrl: string;
  userId: string;
  tz: string;
  /** Seconds of audio per uploaded segment. */
  segmentSeconds: number;
  /** Stable per-install id, so re-registering updates one device row. */
  deviceId: string;
  /**
   * Attach a coarse place (~110m) to each recording.
   *
   * Opt-in, and defaulted off deliberately. Location is the most sensitive
   * thing this app could collect after the audio itself, so it is the user's
   * decision rather than ours - and a segment with no place is a normal
   * state everywhere downstream, not a degraded one.
   */
  capturePlace: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
  baseUrl: '',
  userId: 'dev-user',
  tz:
    Intl.DateTimeFormat().resolvedOptions().timeZone?.trim() ||
    'UTC',
  segmentSeconds: 45,
  deviceId: '',
  capturePlace: false,
};

interface SettingsState extends Settings {
  hydrated: boolean;
  set: (patch: Partial<Settings>) => Promise<void>;
  hydrate: () => Promise<void>;
}

export const useSettings = create<SettingsState>((setState, getState) => ({
  ...DEFAULT_SETTINGS,
  hydrated: false,

  hydrate: async () => {
    try {
      const raw = await AsyncStorage.getItem(STORAGE_KEY);
      if (raw) {
        const stored = JSON.parse(raw) as Partial<Settings>;
        const merged = { ...DEFAULT_SETTINGS, ...stored };
        if (!merged.deviceId) merged.deviceId = newDeviceId();
        setState({ ...merged, hydrated: true });
        return;
      }
    } catch {
      // Corrupt or unavailable storage must not block the app from starting.
    }
    setState({ hydrated: true, deviceId: newDeviceId() });
  },

  set: async (patch) => {
    setState(patch);
    const { baseUrl, userId, tz, segmentSeconds, deviceId, capturePlace } =
      getState();
    try {
      await AsyncStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          baseUrl,
          userId,
          tz,
          segmentSeconds,
          deviceId,
          capturePlace,
        }),
      );
    } catch {
      // Keep the in-memory value; it simply will not survive a restart.
    }
  },
}));

/** True once settings are loaded and a server has been configured. */
export function useIsConfigured(): boolean {
  return useSettings((s) => s.hydrated && s.baseUrl.trim().length > 0);
}

export function useHydrateSettings(): boolean {
  const hydrated = useSettings((s) => s.hydrated);
  const hydrate = useSettings((s) => s.hydrate);
  const [started, setStarted] = useState(false);

  useEffect(() => {
    if (!started) {
      setStarted(true);
      void hydrate();
    }
  }, [started, hydrate]);

  return hydrated;
}

/** Normalises what someone types into the server field. */
export function normaliseBaseUrl(input: string): string {
  const trimmed = input.trim().replace(/\/+$/, '');
  if (!trimmed) return '';
  if (!/^https?:\/\//i.test(trimmed)) return `http://${trimmed}`;
  return trimmed;
}

export function useSettingsActions() {
  const set = useSettings((s) => s.set);
  return useCallback(set, [set]);
}
