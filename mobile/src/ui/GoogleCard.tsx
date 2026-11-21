/**
 * The Google connection card in Settings.
 *
 * Two things this screen has to get right, both of which are honesty
 * problems rather than UI problems:
 *
 * 1. **It must say what approval will actually do.** Before connecting,
 *    approving a calendar event records it in Audaxity and nothing more. The
 *    card says that, rather than letting the user assume their real calendar
 *    is involved.
 * 2. **It must be accurate about email.** This card previously told the user
 *    "It can never send email", on the strength of the backend requesting
 *    `gmail.compose` rather than `gmail.send`. That was false: Google
 *    documents `gmail.compose` as "Manage drafts and send emails", and both
 *    send methods accept it.
 *
 *    The backend now requests **no mail scope at all** by default, which
 *    makes the promise true for a different and better reason - there is no
 *    token that could send. When Gmail is explicitly enabled, this card says
 *    the weaker, honest thing instead: the permission allows sending,
 *    Audaxity does not, and that is a property of the code rather than of
 *    the permission.
 *
 * The consent page opens in the system browser via `openBrowserAsync`, not a
 * webview. Google rejects OAuth in embedded webviews (`disallowed_useragent`)
 * because a webview can read what you type into Google's login form.
 */

import { useCallback, useState } from 'react';
import { Linking, Text, View } from 'react-native';

import type { AudaxityApi } from '../api/client';
import { Badge, Button, Card, Row } from './components';
import { colors, space, type } from './theme';

type Status = Awaited<ReturnType<AudaxityApi['googleStatus']>>;

interface Props {
  api: AudaxityApi | null;
  status: Status | undefined;
  loading: boolean;
  onChanged: () => void;
}

export function GoogleCard({ api, status, loading, onChanged }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [opened, setOpened] = useState(false);

  const connect = useCallback(async () => {
    if (!api) return;
    setBusy(true);
    setError(null);
    try {
      const { authorization_url } = await api.googleConnect();
      // Deliberately the system browser. See the note at the top.
      const supported = await Linking.canOpenURL(authorization_url);
      if (!supported) {
        setError('No browser available to open the Google sign-in page.');
        return;
      }
      await Linking.openURL(authorization_url);
      setOpened(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [api]);

  const disconnect = useCallback(async () => {
    if (!api) return;
    setBusy(true);
    setError(null);
    try {
      await api.googleDisconnect();
      setOpened(false);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [api, onChanged]);

  const connected = status?.connected === true;
  const needsReconnect = status?.needs_reconnect === true;

  return (
    <Card>
      <Row style={{ justifyContent: 'space-between' }}>
        <Text style={type.heading}>Google Calendar</Text>
        <Badge
          label={
            loading
              ? 'checking'
              : needsReconnect
                ? 'reconnect'
                : connected
                  ? 'connected'
                  : 'not connected'
          }
          color={
            needsReconnect
              ? colors.warning
              : connected
                ? colors.success
                : colors.border
          }
        />
      </Row>

      <Text style={[type.dim, { marginTop: space.sm }]}>
        {status?.detail ??
          'Checking whether this account can write to your calendar.'}
      </Text>

      {connected && status?.email ? (
        <Text style={[type.mono, { marginTop: space.xs }]}>{status.email}</Text>
      ) : null}

      {/* The promise, stated where the user grants the permission. */}
      <View
        style={{
          marginTop: space.md,
          padding: space.sm,
          borderRadius: 8,
          backgroundColor: colors.surfaceAlt,
        }}
      >
        <Text style={type.tiny}>
          Approving an action can add an event to your calendar.
        </Text>
        {status?.can_draft_email ? (
          <>
            <Text style={[type.tiny, { marginTop: space.xs }]}>
              It can also write an email draft. Audaxity never sends
              email — it only ever creates drafts, and the send is always
              your click inside Gmail.
            </Text>
            {/* The honest caveat, not buried: the permission Google offers
                for drafting also permits sending. There is no draft-only
                Gmail permission to ask for. */}
            <Text
              style={[
                type.tiny,
                { marginTop: space.xs, color: colors.warning },
              ]}
            >
              Google has no draft-only permission, so the access you granted
              does allow sending. Audaxity never uses it that way, but that
              is a promise about this app rather than a limit Google
              enforces.
            </Text>
          </>
        ) : (
          <Text style={[type.tiny, { marginTop: space.xs, fontWeight: '700' }]}>
            It cannot touch your email at all. Audaxity asks Google for no
            access to Gmail, so there is nothing it could read, draft or
            send.
          </Text>
        )}
      </View>

      {!connected && !needsReconnect ? (
        <Text style={[type.tiny, { marginTop: space.sm }]}>
          Until you connect, approved reminders and events are recorded in
          Audaxity only — nothing reaches your real calendar.
        </Text>
      ) : null}

      {opened && !connected ? (
        <Text style={[type.dim, { marginTop: space.sm }]}>
          Finish signing in, in the browser, then come back and press Refresh.
        </Text>
      ) : null}

      {error ? (
        <Text style={[type.tiny, { color: colors.danger, marginTop: space.sm }]}>
          {error}
        </Text>
      ) : null}

      <Row style={{ marginTop: space.md, gap: space.sm }}>
        {connected && !needsReconnect ? (
          <Button
            title="Disconnect"
            variant="secondary"
            onPress={() => void disconnect()}
            disabled={busy}
          />
        ) : (
          <Button
            title={needsReconnect ? 'Reconnect Google' : 'Connect Google'}
            onPress={() => void connect()}
            disabled={busy || !api}
          />
        )}
        <Button
          title="Refresh"
          variant="ghost"
          onPress={onChanged}
          disabled={busy}
        />
      </Row>
    </Card>
  );
}
