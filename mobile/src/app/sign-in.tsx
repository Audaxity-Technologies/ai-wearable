/**
 * Sign in or create an account.
 *
 * Until this exists the backend had no idea who anyone was; `user_id` was a
 * string you typed. Everything is now scoped to the signed-in account.
 */

import { useRouter } from 'expo-router';
import { useState } from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { AudaxityApi } from '@/api/client';
import { normaliseBaseUrl, useSettings } from '@/store/settings';
import { useSession } from '@/store/session';
import { Button, Card, ErrorBanner, Screen } from '@/ui/components';
import { colors, radius, space, type } from '@/ui/theme';

type Mode = 'sign-in' | 'create';

export default function SignInScreen() {
  const router = useRouter();
  const baseUrl = useSettings((s) => s.baseUrl);
  const tz = useSettings((s) => s.tz);
  const signIn = useSession((s) => s.signIn);

  const [mode, setMode] = useState<Mode>('sign-in');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setError(null);
    if (!baseUrl) {
      setError('Set your server address in Settings first.');
      return;
    }
    if (!email.trim() || password.length < 8) {
      setError('Enter an email and a password of at least 8 characters.');
      return;
    }

    setBusy(true);
    try {
      const api = new AudaxityApi(normaliseBaseUrl(baseUrl));
      if (mode === 'create') {
        await api.register({
          email: email.trim(),
          password,
          display_name: name.trim(),
          tz,
        });
      }
      const session = await api.login(email.trim(), password);
      await signIn(session.access_token, {
        userId: session.user_id,
        email: session.email,
        displayName: session.display_name,
        tz: session.tz,
      });
      router.replace('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView contentContainerStyle={styles.container}>
          <Text style={[type.title, { marginBottom: space.xs }]}>Audaxity</Text>
          <Text style={[type.dim, { marginBottom: space.xl }]}>
            Your conversations, remembered.
          </Text>

          {error ? <ErrorBanner message={error} /> : null}

          <Card>
            <View style={styles.tabs}>
              {(['sign-in', 'create'] as Mode[]).map((value) => (
                <Pressable
                  key={value}
                  onPress={() => {
                    setMode(value);
                    setError(null);
                  }}
                  style={[styles.tab, mode === value && styles.tabActive]}
                >
                  <Text
                    style={[
                      type.dim,
                      mode === value && { color: colors.text, fontWeight: '600' },
                    ]}
                  >
                    {value === 'sign-in' ? 'Sign in' : 'Create account'}
                  </Text>
                </Pressable>
              ))}
            </View>

            {mode === 'create' ? (
              <TextInput
                value={name}
                onChangeText={setName}
                placeholder="Your name"
                placeholderTextColor={colors.textFaint}
                style={styles.input}
              />
            ) : null}

            <TextInput
              value={email}
              onChangeText={setEmail}
              placeholder="you@example.com"
              placeholderTextColor={colors.textFaint}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="email-address"
              textContentType="emailAddress"
              style={styles.input}
            />
            <TextInput
              value={password}
              onChangeText={setPassword}
              placeholder="Password (at least 8 characters)"
              placeholderTextColor={colors.textFaint}
              secureTextEntry
              autoCapitalize="none"
              textContentType="password"
              style={styles.input}
            />

            <Button
              title={mode === 'create' ? 'Create account' : 'Sign in'}
              onPress={() => void submit()}
              loading={busy}
              style={{ marginTop: space.lg }}
            />
          </Card>

          <Card>
            <Text style={type.dim}>
              Server: {baseUrl || 'not set'}
            </Text>
            <Button
              title="Change server"
              variant="secondary"
              onPress={() => router.push('/settings')}
              style={{ marginTop: space.md }}
            />
          </Card>
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  container: { padding: space.lg, paddingTop: space.xxl },
  tabs: { flexDirection: 'row', gap: space.sm, marginBottom: space.lg },
  tab: {
    paddingVertical: space.sm,
    paddingHorizontal: space.lg,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: colors.border,
  },
  tabActive: { backgroundColor: colors.surfaceAlt, borderColor: colors.accent },
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
