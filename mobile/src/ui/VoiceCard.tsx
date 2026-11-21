/**
 * Owner voice enrolment, in Settings.
 *
 * Three promises are made on this card, and all three are stated where the
 * user actually grants the permission rather than buried in a policy:
 *
 * 1. **Only your voice is enrolled.** There is no route that could enrol
 *    anyone else and no table that could hold them. Enrolling everyone would
 *    be a biometric database of people who never consented - Forth designed
 *    exactly that and then refused to ship it.
 * 2. **The recording is not kept.** The clips become one mathematical
 *    average and are then discarded.
 * 3. **It can be deleted, and deleted means deleted.**
 *
 * The card also has to do something harder: say honestly that matching does
 * not work yet. No speaker model is wired up, so `verification_trustworthy`
 * is false and `SpeakerMatch.is_owner` can never be true. Showing a confident
 * "Enrolled ✓" here would be the exact dishonesty the rest of the app avoids.
 */

import { RecordingPresets, useAudioRecorder } from 'expo-audio';
import { useCallback, useState } from 'react';
import { Alert, Text, View } from 'react-native';

import { useVoiceEnrol, useVoiceForget } from '../api/hooks';
import type { VoiceStatus } from '../api/types';
import { Badge, Button, Card, Row } from './components';
import { colors, radius, space, type } from './theme';

/** Backend requires three clips and 15 seconds in total. */
const CLIPS_NEEDED = 3;
const SECONDS_PER_CLIP = 6;

const PROMPTS = [
  'Say: "This is my voice, and I am setting up Audaxity."',
  'Say what you had for breakfast, in a normal speaking voice.',
  'Count slowly from one to ten.',
];

interface Clip {
  uri: string;
  durationS: number;
}

export function VoiceCard({
  status,
  loading,
  onChanged,
}: {
  status: VoiceStatus | undefined;
  loading: boolean;
  onChanged: () => void;
}) {
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const enrol = useVoiceEnrol();
  const forget = useVoiceForget();

  const [clips, setClips] = useState<Clip[]>([]);
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);

  const startClip = useCallback(async () => {
    setError(null);
    try {
      await recorder.prepareToRecordAsync();
      recorder.record();
      setStartedAt(Date.now());
      setRecording(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [recorder]);

  const stopClip = useCallback(async () => {
    try {
      await recorder.stop();
      const uri = recorder.uri;
      const durationS = Math.max(
        1,
        Math.round((Date.now() - (startedAt ?? Date.now())) / 1000),
      );
      if (!uri) {
        // Same rule as the main recorder: no file means nothing was
        // captured, and that must be said rather than silently swallowed.
        throw new Error('The recorder produced no file. Please try again.');
      }
      setClips((previous) => [...previous, { uri, durationS }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRecording(false);
      setStartedAt(null);
    }
  }, [recorder, startedAt]);

  const submit = useCallback(async () => {
    setError(null);
    try {
      await enrol.mutateAsync(clips);
      setClips([]);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [clips, enrol, onChanged]);

  const remove = useCallback(() => {
    Alert.alert(
      'Delete your voiceprint?',
      'It is removed completely, not archived. You can enrol again at any time.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: () => {
            void (async () => {
              try {
                await forget.mutateAsync();
                onChanged();
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
              }
            })();
          },
        },
      ],
    );
  }, [forget, onChanged]);

  const enrolled = status?.enrolled === true;
  const trusted = status?.verification_trustworthy === true;
  const busy = enrol.isPending || forget.isPending || recording;

  return (
    <Card>
      <Row style={{ justifyContent: 'space-between' }}>
        <Text style={type.heading}>Your voice</Text>
        <Badge
          label={
            loading
              ? 'checking'
              : !enrolled
                ? 'not enrolled'
                : trusted
                  ? 'enrolled'
                  : 'enrolled, not in use'
          }
          color={
            !enrolled
              ? colors.border
              : trusted
                ? colors.success
                : colors.warning
          }
        />
      </Row>

      <Text style={[type.dim, { marginTop: space.sm }]}>
        {status?.detail ??
          'Lets Audaxity tell your voice from other people’s, so "I’ll send it" is filed as yours and not as someone else’s promise.'}
      </Text>

      {/* The promises, where the permission is granted. */}
      <View
        style={{
          marginTop: space.md,
          padding: space.sm,
          borderRadius: radius.sm,
          backgroundColor: colors.surfaceAlt,
        }}
      >
        <Text style={type.tiny}>
          Only your own voice is enrolled. Audaxity never builds a voiceprint
          of anyone else you talk to.
        </Text>
        <Text style={[type.tiny, { marginTop: space.xs }]}>
          The recordings are not kept. They are averaged into a single
          voiceprint and then discarded.
        </Text>
      </View>

      {enrolled && !trusted ? (
        <Text
          style={[type.tiny, { marginTop: space.sm, color: colors.warning }]}
        >
          Voice matching is not switched on yet, so this voiceprint is stored
          but never used to decide who promised something. It will stay unused
          until the matching has been tested on real recordings.
        </Text>
      ) : null}

      {enrolled ? (
        <View style={{ marginTop: space.md }}>
          <Text style={type.tiny}>
            {status?.clip_count ?? 0} clips ·{' '}
            {Math.round(status?.total_seconds ?? 0)}s · others enrolled:{' '}
            {status?.others_enrolled ?? 0}
          </Text>
        </View>
      ) : (
        <View style={{ marginTop: space.md }}>
          <Text style={type.body}>
            {clips.length < CLIPS_NEEDED
              ? PROMPTS[clips.length] ?? PROMPTS[0]
              : 'That is enough. Save it below.'}
          </Text>
          <Text style={[type.tiny, { marginTop: space.xs }]}>
            {clips.length}/{CLIPS_NEEDED} recorded · about{' '}
            {SECONDS_PER_CLIP}s each
          </Text>
        </View>
      )}

      {error ? (
        <Text
          style={[type.tiny, { color: colors.danger, marginTop: space.sm }]}
        >
          {error}
        </Text>
      ) : null}

      <Row style={{ marginTop: space.md, gap: space.sm, flexWrap: 'wrap' }}>
        {enrolled ? (
          <>
            <Button
              title="Delete voiceprint"
              variant="secondary"
              onPress={remove}
              disabled={busy}
            />
            <Button
              title="Re-enrol"
              variant="ghost"
              onPress={remove}
              disabled={busy}
            />
          </>
        ) : (
          <>
            {recording ? (
              <Button title="Stop" onPress={() => void stopClip()} />
            ) : (
              <Button
                title={clips.length === 0 ? 'Record clip' : 'Record another'}
                onPress={() => void startClip()}
                disabled={busy || clips.length >= CLIPS_NEEDED}
              />
            )}
            <Button
              title={enrol.isPending ? 'Saving...' : 'Save voiceprint'}
              onPress={() => void submit()}
              disabled={busy || clips.length < CLIPS_NEEDED}
            />
            {clips.length > 0 && !recording ? (
              <Button
                title="Start over"
                variant="ghost"
                onPress={() => setClips([])}
                disabled={busy}
              />
            ) : null}
          </>
        )}
      </Row>
    </Card>
  );
}
