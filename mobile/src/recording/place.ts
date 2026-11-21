/**
 * Coarse place capture on the device.
 *
 * Three decisions here, and each one is a deliberate refusal to collect more
 * than the feature needs.
 *
 * **1. Balanced accuracy, not High.** The server keeps coordinates rounded to
 * ~110m (3dp). Requesting `Accuracy.High` would spin up GPS, drain the
 * battery and ask Android for precise location - and then the precision would
 * be thrown away on write. `Accuracy.Balanced` is roughly 100m, which is the
 * grid we actually keep. On Android this also lets the OS satisfy us with
 * "approximate location" when the user granted only that.
 *
 * **2. Rounded before it leaves the device.** `roundCoord` runs here, so the
 * precise fix never crosses the network. The server rounds again, because a
 * promise that depends on the client being correct is not a promise - but
 * that is the backstop, not the mechanism.
 *
 * **3. One fix per segment, never a watcher.** `watchPositionAsync` would
 * give a continuous track, which is a movement history and a different
 * product. A single reading when a chunk of audio starts answers "where did
 * this conversation happen" and nothing else.
 *
 * Everything degrades to `null`. No permission, no fix, airplane mode, a
 * timeout - all produce no place, and a segment with no place is a normal,
 * first-class state all the way through the backend. A place is NEVER carried
 * over from the previous segment: place is the one axis of the memory index
 * that cannot be re-derived later, so a borrowed value would corrupt it
 * permanently.
 */

import * as Location from 'expo-location';

/** Must match `PLACE_PRECISION_DP` in `backend/app/models/places.py`. */
export const PLACE_PRECISION_DP = 3;

/** Giving up is better than delaying a recording. */
const FIX_TIMEOUT_MS = 8000;

export interface CapturedPlace {
  lat: number;
  lon: number;
  accuracyM: number | null;
  label: string | null;
  locality: string | null;
  region: string | null
  country: string | null;
}

export function roundCoord(value: number, dp = PLACE_PRECISION_DP): number {
  const factor = 10 ** dp;
  return Math.round(value * factor) / factor;
}

function cellKey(lat: number, lon: number): string {
  return `${lat.toFixed(PLACE_PRECISION_DP)},${lon.toFixed(PLACE_PRECISION_DP)}`;
}

/**
 * Reverse-geocode results, keyed by grid cell.
 *
 * Worth caching for two reasons: a long conversation in one room would
 * otherwise geocode the same cell every 45 seconds, and the name of a
 * 110m square does not change during a recording. In-memory only - it is a
 * battery optimisation, not data worth persisting.
 */
const nameCache = new Map<string, Partial<CapturedPlace>>();

/**
 * Ask for location permission. Call this from the settings toggle, never
 * from the recorder: a permission dialog appearing as someone starts
 * recording is how you get a denial.
 */
export async function requestPlacePermission(): Promise<{
  granted: boolean;
  detail: string;
}> {
  try {
    const existing = await Location.getForegroundPermissionsAsync();
    if (existing.granted) return { granted: true, detail: 'Already allowed.' };
    if (!existing.canAskAgain) {
      return {
        granted: false,
        detail:
          'Location is blocked for this app. Turn it on in your phone settings if you want places remembered.',
      };
    }
    const asked = await Location.requestForegroundPermissionsAsync();
    return {
      granted: asked.granted,
      detail: asked.granted
        ? 'Allowed.'
        : 'Not allowed. Recordings will simply have no place attached.',
    };
  } catch (error) {
    return {
      granted: false,
      detail: error instanceof Error ? error.message : String(error),
    };
  }
}

export async function hasPlacePermission(): Promise<boolean> {
  try {
    return (await Location.getForegroundPermissionsAsync()).granted;
  } catch {
    return false;
  }
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T | null> {
  return Promise.race([
    promise,
    new Promise<null>((resolve) => setTimeout(() => resolve(null), ms)),
  ]);
}

/**
 * One coarse fix, or `null`.
 *
 * Never throws and never blocks longer than `FIX_TIMEOUT_MS`: a missing place
 * is a normal outcome, and a recording must not wait on a satellite.
 */
export async function capturePlace(): Promise<CapturedPlace | null> {
  try {
    if (!(await hasPlacePermission())) return null;

    const position = await withTimeout(
      Location.getCurrentPositionAsync({
        // ~100m. Matches the grid the server keeps; see the note at the top.
        accuracy: Location.Accuracy.Balanced,
      }),
      FIX_TIMEOUT_MS,
    );
    if (!position) return null;

    // Rounded here, so full precision never leaves the device.
    const lat = roundCoord(position.coords.latitude);
    const lon = roundCoord(position.coords.longitude);
    const key = cellKey(lat, lon);

    let names = nameCache.get(key);
    if (!names) {
      names = await lookupNames(lat, lon);
      nameCache.set(key, names);
    }

    return {
      lat,
      lon,
      accuracyM:
        position.coords.accuracy === null ||
        position.coords.accuracy === undefined
          ? null
          : Math.round(position.coords.accuracy),
      label: names.label ?? null,
      locality: names.locality ?? null,
      region: names.region ?? null,
      country: names.country ?? null,
    };
  } catch {
    // Any failure at all means no place. Deliberately silent: this runs on
    // the recording path and must never interrupt it.
    return null;
  }
}

/**
 * Best-effort human name for a cell. Geocoded from the ALREADY ROUNDED
 * coordinates, so the lookup itself cannot leak a precise position to the
 * geocoder.
 */
async function lookupNames(
  lat: number,
  lon: number,
): Promise<Partial<CapturedPlace>> {
  try {
    const results = await withTimeout(
      Location.reverseGeocodeAsync({ latitude: lat, longitude: lon }),
      FIX_TIMEOUT_MS,
    );
    const first = results?.[0];
    if (!first) return {};
    return {
      // `name` is often a street or building. Useful as the recognisable
      // label, with district/city behind it.
      label: first.name ?? first.district ?? null,
      locality: first.city ?? first.subregion ?? null,
      region: first.region ?? null,
      country: first.isoCountryCode ?? first.country ?? null,
    };
  } catch {
    return {};
  }
}
