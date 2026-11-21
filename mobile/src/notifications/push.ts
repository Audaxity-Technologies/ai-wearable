/**
 * Push notifications.
 *
 * Three jobs: ask permission, register this device's token with the backend,
 * and route a tapped notification to the right screen.
 *
 * A **wake request** is the one that needs care. It arrives as an
 * interrupting notification and carries `arm_recording`, but tapping it only
 * opens the Record screen with the reason shown — it never starts the
 * microphone. Starting capture from a remote trigger would be covert
 * recording, which this product does not do, and which Android blocks from
 * the background anyway.
 */

import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';

import type { AudaxityApi } from '../api/client';

export const WAKE_CHANNEL = 'wake';

/**
 * Notifications need the native module, which web does not have. Guarding on
 * this rather than try/catch at each call site, because an unguarded call
 * throws an UnavailabilityError that takes the whole app down.
 */
export const pushSupported = Platform.OS !== 'web';

/** Set once at startup: how a notification behaves while the app is open. */
export function configureNotificationHandler(): void {
  if (!pushSupported) return;
  try {
    Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
      }),
    });
  } catch (error) {
    console.warn('[push] notification handler unavailable:', error);
  }
}

async function ensureAndroidChannels(): Promise<void> {
  if (Platform.OS !== 'android') return;

  // Expo Go on Android (SDK 53+) rejects channel creation with a native
  // NullPointerException. Channels are a delivery nicety, not a requirement,
  // so a failure here must never stop the app - least of all stop recording.
  try {
    await Notifications.setNotificationChannelAsync('default', {
      name: 'Updates',
      importance: Notifications.AndroidImportance.DEFAULT,
    });
    // Wake requests are meant to interrupt, so they get their own channel the
    // user can mute independently of everything else.
    await Notifications.setNotificationChannelAsync(WAKE_CHANNEL, {
      name: 'Wake-up calls',
      importance: Notifications.AndroidImportance.MAX,
      vibrationPattern: [0, 250, 250, 250],
      sound: 'default',
      enableVibrate: true,
    });
  } catch (error) {
    console.warn('[push] notification channels unavailable:', error);
  }
}

export interface PushRegistration {
  token: string | null;
  granted: boolean;
  reason?: string;
}

/**
 * Request permission and return an Expo push token.
 *
 * Returns `granted: false` rather than throwing: notifications are a
 * convenience, and refusing them must not stop the app working.
 */
export async function obtainPushToken(
  projectId?: string,
): Promise<PushRegistration> {
  if (!pushSupported) {
    return { token: null, granted: false, reason: 'Not supported on web.' };
  }
  await ensureAndroidChannels();

  if (!Device.isDevice) {
    // Simulators and web cannot receive push; say so plainly.
    return { token: null, granted: false, reason: 'Needs a physical device.' };
  }

  const existing = await Notifications.getPermissionsAsync();
  let status = existing.status;
  if (status !== 'granted') {
    status = (await Notifications.requestPermissionsAsync()).status;
  }
  if (status !== 'granted') {
    return { token: null, granted: false, reason: 'Permission denied.' };
  }

  try {
    const token = await Notifications.getExpoPushTokenAsync(
      projectId ? { projectId } : undefined,
    );
    return { token: token.data, granted: true };
  } catch (error) {
    return {
      token: null,
      granted: true,
      reason: error instanceof Error ? error.message : String(error),
    };
  }
}

/** Register this install with the backend so it can be notified. */
export async function registerDevice(
  api: AudaxityApi,
  deviceId: string,
  pushToken: string | null,
): Promise<void> {
  await api.registerDevice({
    device_id: deviceId,
    platform: Platform.OS === 'ios' ? 'ios' : Platform.OS === 'android' ? 'android' : 'web',
    name: Device.deviceName ?? Platform.OS,
    push_token: pushToken,
  });
}

export interface NotificationIntent {
  kind: string;
  route: string;
  /** True for a wake request: open Record and explain why, but do not start. */
  armRecording: boolean;
  reason?: string;
}

export function readIntent(
  response: Notifications.NotificationResponse | null,
): NotificationIntent | null {
  const data = response?.notification?.request?.content?.data as
    | Record<string, string>
    | undefined;
  if (!data?.kind) return null;
  return {
    kind: data.kind,
    route: data.route || '/',
    armRecording: data.arm_recording === 'true',
    reason: data.reason,
  };
}


/** The notification that launched the app, if any. Null where unsupported. */
export async function lastResponse(): Promise<Notifications.NotificationResponse | null> {
  if (!pushSupported) return null;
  try {
    return await Notifications.getLastNotificationResponseAsync();
  } catch {
    return null;
  }
}

/** Subscribe to taps. Returns a no-op unsubscribe where unsupported. */
export function onNotificationTapped(
  handler: (intent: NotificationIntent) => void,
): () => void {
  if (!pushSupported) return () => {};
  const sub = Notifications.addNotificationResponseReceivedListener(
    (response) => {
      const intent = readIntent(response);
      if (intent) handler(intent);
    },
  );
  return () => sub.remove();
}
