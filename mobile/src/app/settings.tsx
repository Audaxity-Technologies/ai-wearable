import { useState } from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  Switch,
  TextInput,
  View,
} from 'react-native';

import {
  useApi,
  useGoogleStatus,
  useHealth,
  useVoiceStatus,
} from '@/api/hooks';
import { useSession } from '@/store/session';
import { AudaxityApi } from '@/api/client';
import { requestPlacePermission } from '@/recording/place';
import { normaliseBaseUrl, useSettings } from '@/store/settings';
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Row,
  Screen,
} from '@/ui/components';
import { GoogleCard } from '@/ui/GoogleCard';
import { VoiceCard } from '@/ui/VoiceCard';
import { colors, radius, space, type } from '@/ui/theme';

export default function SettingsScreen() {
  const settings = useSettings();
  const health = useHealth();
  const api = useApi();
  const account = useSession((s) => s.account);
  const signOut = useSession((s) => s.signOut);
  const google = useGoogleStatus();
  const voice = useVoiceStatus();
  const [briefingTime, setBriefingTime] = useState('07:00');
  const [wakeResult, setWakeResult] = useState<string | null>(null);

  const [baseUrl, setBaseUrl] = useState(settings.baseUrl);
  const [userId, setUserId] = useState(settings.userId);
  const [segment, setSegment] = useState(String(settings.segmentSeconds));
  const [placeNote, setPlaceNote] = useState<string | null>(null);

  // Permission is requested HERE, from a deliberate tap - never from the
  // recorder. A system dialog appearing as someone starts recording is how
  // you earn a denial.
  const togglePlace = async (next: boolean) => {
    if (!next) {
      await settings.set({ capturePlace: false });
      setPlaceNote('Off. New recordings will have no place attached.');
      return;
    }
    const { granted, detail } = await requestPlacePermission();
    // Storing `true` after a refusal would show an on switch that captures
    // nothing, which is the dishonesty this app avoids elsewhere.
    await settings.set({ capturePlace: granted });
    setPlaceNote(detail);
  };
  const [probe, setProbe] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  const [saved, setSaved] = useState(false);

  async function save() {
    const seconds = Math.min(300, Math.max(10, Number(segment) || 45));
    await settings.set({
      baseUrl: normaliseBaseUrl(baseUrl),
      userId: userId.trim() || 'dev-user',
      segmentSeconds: seconds,
    });
    setSegment(String(seconds));
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
    void health.refetch();
  }

  /** Check the address before saving, so a typo is obvious immediately. */
  async function testConnection() {
    setProbing(true);
    setProbe(null);
    try {
      const url = normaliseBaseUrl(baseUrl);
      if (!url) {
        setProbe('Enter an address first.');
        return;
      }
      const result = await new AudaxityApi(url, userId, 8000).health();
      setProbe(
        `Reached it. speech=${result.transcription_provider}` +
          `${result.transcription_ready ? '' : ' (not ready)'}, ` +
          `model=${result.llm_provider}` +
          `${result.llm_ready ? '' : ' (not ready)'}`,
      );
    } catch (error) {
      setProbe(error instanceof Error ? error.message : String(error));
    } finally {
      setProbing(false);
    }
  }

  return (
    <Screen>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView contentContainerStyle={styles.container}>
          <Card>
            <Text style={type.heading}>Server address</Text>
            <Text style={[type.dim, { marginTop: space.xs }]}>
              Your phone cannot reach &ldquo;localhost&rdquo;. Use the
              computer&rsquo;s address on this Wi-Fi network, or a tunnel URL.
              Start the backend with --host 0.0.0.0 so it accepts connections
              from the network.
            </Text>
            <TextInput
              value={baseUrl}
              onChangeText={setBaseUrl}
              placeholder="http://192.168.1.20:8000"
              placeholderTextColor={colors.textFaint}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              style={styles.input}
            />
            <Row style={{ marginTop: space.md, gap: space.sm }}>
              <Button
                title="Test connection"
                variant="secondary"
                onPress={() => void testConnection()}
                loading={probing}
                style={{ flex: 1 }}
              />
              <Button
                title={saved ? 'Saved' : 'Save'}
                onPress={() => void save()}
                style={{ flex: 1 }}
              />
            </Row>
            {probe ? (
              <Text style={[type.dim, { marginTop: space.md }]}>{probe}</Text>
            ) : null}
          </Card>

          <Card>
            <Text style={type.heading}>Recording</Text>
            <Text style={[type.dim, { marginTop: space.xs }]}>
              Audio is uploaded in segments. Shorter segments start processing
              sooner and survive a dropped connection better; longer ones use
              less battery.
            </Text>
            <TextInput
              value={segment}
              onChangeText={setSegment}
              keyboardType="number-pad"
              style={styles.input}
            />
            <Text style={type.tiny}>seconds per segment (10–300)</Text>
          </Card>


          <Card>
            <Row style={{ justifyContent: 'space-between' }}>
              <Text style={type.heading}>Remember places</Text>
              <Switch
                value={settings.capturePlace}
                onValueChange={(next) => void togglePlace(next)}
              />
            </Row>
            <Text style={[type.dim, { marginTop: space.xs }]}>
              Attaches a rough place to each recording, so you can later ask
              what was said at the office or at a cafe.
            </Text>
            <Text style={[type.tiny, { marginTop: space.sm }]}>
              Kept to about 110 metres - the neighbourhood, not the doorstep.
              Your exact position never leaves the phone, and a place is taken
              only while you are actually recording. Audaxity never keeps a
              record of where you go otherwise.
            </Text>
            {/* Said plainly, because it is the one thing that cannot be
                undone by changing your mind later. */}
            <Text style={[type.tiny, { marginTop: space.sm }]}>
              Turning this on later will not fill in past recordings. Where
              you were is the one thing that cannot be worked out afterwards
              from the audio.
            </Text>
            {placeNote ? (
              <Text style={[type.tiny, { marginTop: space.sm }]}>
                {placeNote}
              </Text>
            ) : null}
          </Card>

          <Card>
            <Text style={type.heading}>Account</Text>
            <Text style={[type.body, { marginTop: space.sm }]}>
              {account?.email ?? 'Not signed in'}
            </Text>
            <Text style={[type.dim, { marginTop: space.xs }]}>
              Timezone: {settings.tz}
            </Text>
            {account ? (
              <Button
                title="Sign out"
                variant="danger"
                onPress={() => void signOut()}
                style={{ marginTop: space.md }}
              />
            ) : null}
          </Card>

          <GoogleCard
            api={api}
            status={google.data}
            loading={google.isLoading}
            onChanged={() => void google.refetch()}
          />

          <VoiceCard
            status={voice.data}
            loading={voice.isLoading}
            onChanged={() => void voice.refetch()}
          />

          <Card>
            <Text style={type.heading}>Morning briefing</Text>
            <Text style={[type.dim, { marginTop: space.xs }]}>
              A summary each morning of what is due, what is overdue and what
              still needs a date — in your own timezone.
            </Text>
            <TextInput
              value={briefingTime}
              onChangeText={setBriefingTime}
              placeholder="07:00"
              placeholderTextColor={colors.textFaint}
              style={styles.input}
            />
            <Row style={{ marginTop: space.md, gap: space.sm }}>
              <Button
                title="Save time"
                variant="secondary"
                onPress={() =>
                  void api?.updateMe({ briefing_time: briefingTime })
                }
                style={{ flex: 1 }}
              />
              <Button
                title="Turn off"
                variant="ghost"
                onPress={() =>
                  void api?.updateMe({ briefing_enabled: false })
                }
                style={{ flex: 1 }}
              />
            </Row>
          </Card>

          <Card>
            <Text style={type.heading}>Wake-up call</Text>
            <Text style={[type.dim, { marginTop: space.xs }]}>
              Anything that can reach your server can ask you to start
              recording — a cron job, a calendar hook, another device. It
              sends a notification; recording still starts only when you press
              the button. Nothing listens without that tap.
            </Text>
            <Button
              title="Send one to this phone"
              variant="secondary"
              onPress={async () => {
                try {
                  const result = await api?.requestWake('Test wake-up call');
                  setWakeResult(
                    result?.sent
                      ? `Sent to ${result.sent} device(s).`
                      : (result?.detail ?? 'Nothing sent.'),
                  );
                } catch (error) {
                  setWakeResult(String(error));
                }
              }}
              style={{ marginTop: space.md }}
            />
            {wakeResult ? (
              <Text style={[type.dim, { marginTop: space.sm }]}>{wakeResult}</Text>
            ) : null}
          </Card>

          <Card>
            <Row style={{ justifyContent: 'space-between' }}>
              <Text style={type.heading}>Status</Text>
              <Badge
                label={
                  health.isError
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
            {health.data ? (
              <Text style={[type.dim, { marginTop: space.sm }]}>
                speech: {health.data.transcription_detail}
                {'\n'}model: {health.data.llm_detail}
                {'\n'}queued jobs: {health.data.pipeline_pending_jobs}
              </Text>
            ) : null}
          </Card>

          <Card>
            <Text style={type.heading}>Privacy</Text>
            <Text style={[type.dim, { marginTop: space.sm }]}>
              Recording is always visible while it runs, and stopping is one
              tap. Deleting a conversation removes its audio, its transcript
              and anything derived only from it.
            </Text>
            <Text style={[type.dim, { marginTop: space.sm }]}>
              Tell the people you are with that you are recording. In many
              places that is also the law.
            </Text>
          </Card>

          <View style={{ height: space.xxl }} />
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  container: { padding: space.lg },
  input: {
    marginTop: space.md,
    color: colors.text,
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: space.md,
    paddingVertical: space.md,
    fontSize: 15,
  },
});
