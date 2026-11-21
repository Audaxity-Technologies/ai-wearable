/**
 * Record.
 *
 * Privacy is a UI requirement here, not a setting: while recording, the screen
 * says so unmistakably and a single tap stops it. The brief is explicit that
 * recording must be deliberate and visible, and that has to be true of the
 * phone before it can be true of the band.
 */

import { Link, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useApi, useHealth } from '@/api/hooks';
import { obtainPushToken, registerDevice } from '@/notifications/push';
import { useRecorder } from '@/recording/useRecorder';
import { useSettings } from '@/store/settings';
import { useWakeStore } from '@/store/wake';
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Row,
  Screen,
} from '@/ui/components';
import { colors, formatDuration, radius, space, type } from '@/ui/theme';

export default function RecordScreen() {
  const api = useApi();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const baseUrl = useSettings((s) => s.baseUrl);
  const segmentSeconds = useSettings((s) => s.segmentSeconds);
  const deviceId = useSettings((s) => s.deviceId);
  const health = useHealth();
  const { status, start, stop, flush } = useRecorder(api);
  const [lastSession, setLastSession] = useState<string | null>(null);
  const wakeReason = useWakeStore((s) => s.reason);
  const clearWake = useWakeStore((s) => s.clear);

  const recording = status.state === 'recording';
  const busy = status.state === 'starting' || status.state === 'stopping';

  // Try to drain the outbox whenever the server becomes reachable again.
  useEffect(() => {
    if (health.isSuccess) void flush();
  }, [health.isSuccess, flush]);

  // Register for notifications once, after a successful connection. Failure
  // is not fatal: notifications are a convenience, not a requirement.
  useEffect(() => {
    if (!api || !health.isSuccess) return;
    let cancelled = false;
    void (async () => {
      const registration = await obtainPushToken();
      if (cancelled) return;
      try {
        await registerDevice(api, deviceId, registration.token);
      } catch {
        // The app works fine without push; nothing to surface here.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [api, health.isSuccess, deviceId]);

  useEffect(() => {
    if (recording && wakeReason) clearWake();
  }, [recording, wakeReason, clearWake]);

  async function onStop() {
    const sessionId = await stop();
    if (sessionId) setLastSession(sessionId);
  }

  if (!baseUrl) {
    return (
      <Screen>
        <ScrollView contentContainerStyle={styles.container}>
          <Card>
            <Text style={type.heading}>Connect to your backend</Text>
            <Text style={[type.dim, { marginTop: space.sm }]}>
              This app talks to the Audaxity backend running on your machine.
              A phone cannot reach &ldquo;localhost&rdquo;, so you need the
              laptop&rsquo;s address on your Wi-Fi — something like
              http://192.168.1.20:8000.
            </Text>
            <Button
              title="Open settings"
              onPress={() => router.push('/settings')}
              style={{ marginTop: space.lg }}
            />
          </Card>
        </ScrollView>
      </Screen>
    );
  }

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={[
          styles.container,
          { paddingBottom: insets.bottom + space.xxl },
        ]}
      >
        {status.error ? <ErrorBanner message={status.error} /> : null}

        {wakeReason && !recording ? (
          <Card style={{ borderColor: colors.warning }}>
            <Row>
              <Badge label="wake-up call" color={colors.warning} />
            </Row>
            <Text style={[type.body, { marginTop: space.sm }]}>
              {wakeReason}
            </Text>
            <Text style={[type.dim, { marginTop: space.xs }]}>
              Nothing is being recorded yet. Press START when you are ready —
              and tell the people with you.
            </Text>
            <Button
              title="Dismiss"
              variant="ghost"
              onPress={clearWake}
              style={{ marginTop: space.sm }}
            />
          </Card>
        ) : null}

        {/* The recording indicator. Deliberately impossible to miss. */}
        <View
          style={[
            styles.stage,
            recording && { borderColor: colors.record, backgroundColor: '#2A1416' },
          ]}
        >
          <Text style={recording ? styles.stageLabelLive : styles.stageLabel}>
            {recording ? 'RECORDING' : 'Not recording'}
          </Text>
          <Text style={styles.timer}>
            {formatDuration(status.elapsedSeconds)}
          </Text>
          <Text style={type.dim}>
            {recording
              ? `${status.segmentsCaptured} segment${
                  status.segmentsCaptured === 1 ? '' : 's'
                } captured · ${segmentSeconds}s each`
              : 'Everyone present should know this is on.'}
          </Text>
          {recording && !status.backgroundCapture ? (
            <Text style={[type.tiny, { color: colors.warning, textAlign: 'center' }]}>
              Keep this screen open — recording stops if the phone locks.
              Background capture needs the full app build.
            </Text>
          ) : null}
        </View>

        <Pressable
          accessibilityRole="button"
          accessibilityLabel={recording ? 'Stop recording' : 'Start recording'}
          onPress={recording ? onStop : start}
          disabled={busy}
          style={({ pressed }) => [
            styles.recordButton,
            {
              backgroundColor: recording ? colors.surfaceAlt : colors.record,
              borderColor: recording ? colors.record : 'transparent',
              opacity: busy ? 0.5 : pressed ? 0.8 : 1,
            },
          ]}
        >
          <Text
            style={[
              styles.recordButtonText,
              { color: recording ? colors.record : '#fff' },
            ]}
          >
            {busy ? '…' : recording ? 'STOP' : 'START'}
          </Text>
        </Pressable>

        {status.pendingUploads > 0 ? (
          <Card>
            <Row>
              <Badge label="offline queue" color={colors.warning} />
            </Row>
            <Text style={[type.body, { marginTop: space.sm }]}>
              {status.pendingUploads} segment
              {status.pendingUploads === 1 ? '' : 's'} waiting to upload.
            </Text>
            <Text style={[type.dim, { marginTop: space.xs }]}>
              Nothing is lost — audio is stored on the device and sent when the
              server is reachable.
            </Text>
            <Button
              title="Retry now"
              variant="secondary"
              onPress={() => void flush()}
              style={{ marginTop: space.md }}
            />
          </Card>
        ) : null}

        {lastSession && !recording ? (
          <Card>
            <Text style={type.heading}>Processing your conversation</Text>
            <Text style={[type.dim, { marginTop: space.xs }]}>
              Transcribing, then extracting tasks and commitments.
            </Text>
            <Button
              title="Open it"
              variant="secondary"
              onPress={() => router.push(`/conversation/${lastSession}`)}
              style={{ marginTop: space.md }}
            />
          </Card>
        ) : null}

        <Card>
          <Row style={{ justifyContent: 'space-between' }}>
            <Text style={type.heading}>Backend</Text>
            <Badge
              label={
                health.isLoading
                  ? 'checking'
                  : health.isError
                    ? 'unreachable'
                    : (health.data?.status ?? 'unknown')
              }
              color={
                health.isError
                  ? colors.danger
                  : health.data?.status === 'ok'
                    ? colors.success
                    : colors.warning
              }
            />
          </Row>
          <Text style={[type.mono, { marginTop: space.sm }]}>{baseUrl}</Text>
          {health.data ? (
            <Text style={[type.dim, { marginTop: space.xs }]}>
              speech: {health.data.transcription_detail}
              {'\n'}model: {health.data.llm_detail}
            </Text>
          ) : null}
          {health.isError ? (
            <Text style={[type.dim, { marginTop: space.xs }]}>
              You can still record — segments queue on the device.
            </Text>
          ) : null}
          <Row style={{ marginTop: space.md, gap: space.sm }}>
            <Link href="/settings" asChild>
              <Button title="Settings" variant="secondary" onPress={() => {}} />
            </Link>
            <Link href="/memory" asChild>
              <Button title="Memory" variant="secondary" onPress={() => {}} />
            </Link>
            <Link href="/loops" asChild>
              <Button
                title="Open loops"
                variant="secondary"
                onPress={() => {}}
              />
            </Link>
            <Link href="/people" asChild>
              <Button title="People" variant="secondary" onPress={() => {}} />
            </Link>
          </Row>
        </Card>
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  container: { padding: space.lg, gap: space.md },
  stage: {
    borderWidth: 2,
    borderColor: colors.border,
    borderRadius: radius.lg,
    backgroundColor: colors.surface,
    padding: space.xl,
    alignItems: 'center',
    gap: space.sm,
  },
  stageLabel: {
    ...type.dim,
    letterSpacing: 2,
    textTransform: 'uppercase',
    fontWeight: '700',
  },
  stageLabelLive: {
    color: colors.record,
    letterSpacing: 3,
    fontWeight: '800',
    fontSize: 13,
  },
  timer: {
    fontSize: 52,
    fontWeight: '200',
    color: colors.text,
    fontVariant: ['tabular-nums'],
  },
  recordButton: {
    height: 96,
    borderRadius: radius.pill,
    borderWidth: 2,
    alignItems: 'center',
    justifyContent: 'center',
  },
  recordButtonText: { fontSize: 22, fontWeight: '800', letterSpacing: 2 },
});
