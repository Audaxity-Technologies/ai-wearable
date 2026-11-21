/**
 * Segmented recording.
 *
 * Audio is captured in fixed-length segments rather than one long file. The
 * reasons are in the architecture doc: bounded memory on the wearable, a
 * resumable upload on flaky networks, and the backend can start transcribing
 * segment 1 while segment 5 is still being recorded.
 *
 * Each segment gets a client-generated UUID that doubles as the server's
 * idempotency key, so retrying an upload can never duplicate audio.
 */

import {
  AudioModule,
  RecordingPresets,
  setAudioModeAsync,
  useAudioRecorder,
} from 'expo-audio';
import * as Crypto from 'expo-crypto';
import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';

import { AudaxityApi } from '../api/client';
import { useSettings } from '../store/settings';
import * as outbox from './outbox';
import { capturePlace, hasPlacePermission } from './place';

export type RecorderState = 'idle' | 'starting' | 'recording' | 'stopping';

export interface RecorderStatus {
  state: RecorderState;
  sessionId: string | null;
  elapsedSeconds: number;
  segmentsCaptured: number;
  pendingUploads: number;
  error: string | null;
  /** False when the foreground service is unavailable (Expo Go). */
  backgroundCapture: boolean;
}

function uuid(): string {
  return Crypto.randomUUID();
}

/**
 * Recording failures used to land only in UI state, which meant a phone that
 * captured nothing gave no clue why. These go to the Metro console too.
 */
function log(message: string): void {
  console.log(`[recorder] ${message}`);
}

function logError(message: string, error: unknown): void {
  console.error(`[recorder] ${message}:`, error);
}

export function useRecorder(api: AudaxityApi | null) {
  const tz = useSettings((s) => s.tz);
  const segmentSeconds = useSettings((s) => s.segmentSeconds);
  const capturePlaceEnabled = useSettings((s) => s.capturePlace);

  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);

  const [status, setStatus] = useState<RecorderStatus>({
    state: 'idle',
    sessionId: null,
    elapsedSeconds: 0,
    segmentsCaptured: 0,
    pendingUploads: 0,
    error: null,
    backgroundCapture: true,
  });

  // Refs hold the live values the interval callbacks need; state alone would
  // capture stale values inside the timers.
  const sessionRef = useRef<string | null>(null);
  const seqRef = useRef(0);
  const elapsedRef = useRef(0);
  //: A session stopped while segments were still queued, to close once they land.
  const awaitingCloseRef = useRef<string | null>(null);
  const backgroundRef = useRef(true);
  const segmentStartRef = useRef<number>(0);
  const rotatingRef = useRef(false);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const flushRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Read through a ref so a toggle mid-recording takes effect on the
  // next segment without re-creating the rotation callback.
  const capturePlaceRef = useRef(false);

  const refreshPending = useCallback(async () => {
    const count = await outbox.pendingCount();
    setStatus((s) => (s.pendingUploads === count ? s : { ...s, pendingUploads: count }));
  }, []);

  /**
   * Close a session that was stopped while uploads were still queued.
   *
   * `stop()` deliberately refuses to close a session with segments still in
   * the outbox, so the backend never analyses half a conversation. Without
   * this, a session stopped offline would stay open forever once the queue
   * finally drained - which is exactly what happened in device testing.
   */
  const closeIfDrained = useCallback(async () => {
    const sessionId = awaitingCloseRef.current;
    if (!api || !sessionId) return;
    if ((await outbox.pendingForSession(sessionId)) > 0) return;
    try {
      await api.closeSession(sessionId);
      awaitingCloseRef.current = null;
      log(`closed session ${sessionId} after its uploads drained`);
    } catch (error) {
      logError('deferred close failed', error);
    }
  }, [api]);

  useEffect(() => {
    capturePlaceRef.current = capturePlaceEnabled;
  }, [capturePlaceEnabled]);

  /** Upload whatever is queued. Safe to call often: ingest is idempotent. */
  const flush = useCallback(async () => {
    if (!api) return;
    const queued = await outbox.pending();
    for (const segment of queued) {
      try {
        await api.uploadSegment({
          segmentId: segment.id,
          sessionId: segment.session_id,
          seq: segment.seq,
          startedAt: segment.started_at,
          durationS: segment.duration_s,
          uri: segment.uri,
          place: {
            lat: segment.place_lat,
            lon: segment.place_lon,
            accuracyM: segment.place_accuracy_m,
            label: segment.place_label,
            locality: segment.place_locality,
            region: segment.place_region,
            country: segment.place_country,
          },
        });
        await outbox.markUploaded(segment.id);
      } catch (error) {
        // Stay queued and try again later. Recording is never blocked by a
        // failed upload.
        logError(`upload failed for segment ${segment.id}`, error);
        await outbox.markFailed(
          segment.id,
          error instanceof Error ? error.message : String(error),
        );
        break;
      }
    }
    await refreshPending();
    await closeIfDrained();
  }, [api, refreshPending, closeIfDrained]);

  /** Stop the current segment, queue it, and immediately start the next. */
  const rotateSegment = useCallback(
    async (final: boolean) => {
      if (rotatingRef.current) return;
      rotatingRef.current = true;
      try {
        const sessionId = sessionRef.current;
        log(`rotate(final=${final}): stopping recorder`);
        await recorder.stop();
        const uri = recorder.uri;
        const durationS = Math.max(
          1,
          Math.round((Date.now() - segmentStartRef.current) / 1000),
        );
        log(`rotate: uri=${uri ?? 'NULL'} duration=${durationS}s session=${sessionId ?? 'NULL'}`);

        if (!uri) {
          // Without a file there is nothing to queue; surface it rather than
          // silently dropping the audio.
          throw new Error(
            'The recorder produced no file. Nothing was captured for this segment.',
          );
        }

        if (uri && sessionId) {
          // One fix per segment, taken as the chunk closes, and never
          // carried over to the next one: place is the only axis of the
          // memory index that cannot be re-derived from stored audio, so a
          // borrowed value would corrupt it permanently. Resolves to null
          // on any failure, which is a normal state end to end.
          const place = capturePlaceRef.current
            ? await capturePlace()
            : null;

          // Say which branch we took. A silent null is indistinguishable
          // between "switched off", "permission refused" and "no fix", and
          // those need three different fixes. Logs WHETHER, never where:
          // a log of coarse points is still a record of where conversations
          // happened, and logs travel further than the database does.
          if (!capturePlaceRef.current) {
            log('rotate: place off (Settings -> Remember places)');
          } else if (!place) {
            const allowed = await hasPlacePermission();
            log(
              allowed
                ? 'rotate: place unavailable (no fix in time)'
                : 'rotate: place unavailable (location permission not granted)',
            );
          } else {
            log(
              `rotate: place captured (accuracy ${
                place.accuracyM ?? 'unknown'
              }m, named: ${place.label || place.locality ? 'yes' : 'no'})`,
            );
          }

          await outbox.enqueue({
            id: uuid(),
            sessionId,
            seq: seqRef.current,
            uri,
            startedAt: new Date(segmentStartRef.current).toISOString(),
            durationS,
            place,
          });
          log(`rotate: queued segment seq=${seqRef.current}`);
          seqRef.current += 1;
          setStatus((s) => ({ ...s, segmentsCaptured: s.segmentsCaptured + 1 }));
          void flush();
        }

        if (!final) {
          segmentStartRef.current = Date.now();
          await recorder.prepareToRecordAsync();
          recorder.record();
          log('rotate: next segment recording');
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        logError('rotate failed', error);
        setStatus((s) => ({ ...s, error: message }));
      } finally {
        rotatingRef.current = false;
      }
    },
    [recorder, flush],
  );

  const start = useCallback(async () => {
    if (!api) {
      setStatus((s) => ({ ...s, error: 'Set your server address in Settings first.' }));
      return;
    }
    setStatus((s) => ({ ...s, state: 'starting', error: null }));

    try {
      log('start: requesting microphone permission');
      const permission = await AudioModule.requestRecordingPermissionsAsync();
      if (!permission.granted) {
        setStatus((s) => ({
          ...s,
          state: 'idle',
          error: 'Microphone permission is required to record.',
        }));
        return;
      }

      // Background capture needs a microphone-type foreground service, which
      // only exists in a dev build. Expo Go has no such service, so asking
      // for it makes prepareToRecordAsync reject outright. Rather than guess
      // the runtime (Constants reports Expo Go and dev builds identically),
      // try it and fall back - the failure is cheap and unambiguous.
      let background = true;
      log('start: setting audio mode (background capture)');
      await setAudioModeAsync({
        allowsRecording: true,
        playsInSilentMode: true,
        allowsBackgroundRecording: true,
      });

      log('start: opening session on the backend');
      const session = await api.openSession(tz);
      sessionRef.current = session.id;
      seqRef.current = 0;
      elapsedRef.current = 0;
      segmentStartRef.current = Date.now();
      log(`start: session ${session.id}; preparing recorder`);

      try {
        await recorder.prepareToRecordAsync();
      } catch (error) {
        background = false;
        logError('background recording unavailable, falling back', error);
        await setAudioModeAsync({
          allowsRecording: true,
          playsInSilentMode: true,
          allowsBackgroundRecording: false,
        });
        // If this throws too, it is a real failure and the outer catch
        // reports it.
        await recorder.prepareToRecordAsync();
      }

      recorder.record();
      backgroundRef.current = background;
      log(`start: recorder running (background capture: ${background})`);

      setStatus({
        state: 'recording',
        sessionId: session.id,
        elapsedSeconds: 0,
        segmentsCaptured: 0,
        backgroundCapture: background,
        pendingUploads: 0,
        error: null,
      });

      // The tick advances a ref and *then* decides whether to rotate.
      // Rotating from inside a setState updater made the segment boundary a
      // side effect of rendering, which React is free to run twice or defer -
      // so a segment could be cut twice or not at all.
      tickRef.current = setInterval(() => {
        elapsedRef.current += 1;
        const elapsed = elapsedRef.current;
        setStatus((s) => ({ ...s, elapsedSeconds: elapsed }));
        if (elapsed > 0 && elapsed % segmentSeconds === 0) {
          log(`tick: ${elapsed}s reached, rotating segment`);
          void rotateSegment(false);
        }
      }, 1000);

      flushRef.current = setInterval(() => void flush(), 15000);
    } catch (error) {
      setStatus((s) => ({
        ...s,
        state: 'idle',
        error: error instanceof Error ? error.message : String(error),
      }));
    }
  }, [api, tz, recorder, segmentSeconds, rotateSegment, flush]);

  const stop = useCallback(async () => {
    setStatus((s) => ({ ...s, state: 'stopping' }));
    if (tickRef.current) clearInterval(tickRef.current);
    if (flushRef.current) clearInterval(flushRef.current);
    tickRef.current = null;
    flushRef.current = null;

    await rotateSegment(true);

    const sessionId = sessionRef.current;
    try {
      if (api && sessionId) {
        await flush();
        const stillQueued = await outbox.pendingForSession(sessionId);
        if (stillQueued === 0) {
          // Closing starts the pipeline, so only close once every segment has
          // actually landed. Otherwise the backend would analyse a partial
          // conversation.
          await api.closeSession(sessionId);
          awaitingCloseRef.current = null;
        } else {
          // Uploads are still queued. Remember to close this session the
          // moment the outbox drains, instead of leaving it open forever.
          awaitingCloseRef.current = sessionId;
          log(`stop: ${stillQueued} segment(s) still queued; close deferred`);
        }
      }
    } catch (error) {
      setStatus((s) => ({
        ...s,
        error: error instanceof Error ? error.message : String(error),
      }));
    }

    // Releasing the session also tears down the foreground service, so the
    // recording notification disappears the moment recording stops.
    await setAudioModeAsync({
      allowsRecording: false,
      allowsBackgroundRecording: false,
    }).catch(() => undefined);
    sessionRef.current = null;
    elapsedRef.current = 0;
    setStatus((s) => ({
      ...s,
      state: 'idle',
      sessionId: null,
      elapsedSeconds: 0,
    }));
    await refreshPending();
    return sessionId;
  }, [api, flush, rotateSegment, refreshPending]);

  // Flush when the app returns to the foreground, which is usually right
  // after the user has walked back into signal.
  useEffect(() => {
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active') void flush();
    });
    return () => sub.remove();
  }, [flush]);

  useEffect(() => {
    void refreshPending();
    void outbox.pruneUploaded();
  }, [refreshPending]);

  useEffect(
    () => () => {
      if (tickRef.current) clearInterval(tickRef.current);
      if (flushRef.current) clearInterval(flushRef.current);
    },
    [],
  );

  return { status, start, stop, flush };
}
