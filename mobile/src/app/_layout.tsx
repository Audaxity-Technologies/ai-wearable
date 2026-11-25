import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Stack, useRouter } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useEffect, useMemo, useRef } from 'react';
import { ActivityIndicator, View } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import {
  configureNotificationHandler,
  lastResponse,
  onNotificationTapped,
  readIntent,
} from '@/notifications/push';
import { useHydrateSession, useIsSignedIn } from '@/store/session';
import { useHydrateSettings } from '@/store/settings';
import { useWakeStore } from '@/store/wake';
import { colors } from '@/ui/theme';

configureNotificationHandler();

export default function RootLayout() {
  const settingsReady = useHydrateSettings();
  const sessionReady = useHydrateSession();
  const signedIn = useIsSignedIn();
  const router = useRouter();
  const armWake = useWakeStore((s) => s.arm);
  const handled = useRef(false);

  const client = useMemo(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: 1,
            staleTime: 5000,
            refetchOnWindowFocus: true,
          },
        },
      }),
    [],
  );

  // A notification tapped while the app was closed is delivered here.
  useEffect(() => {
    if (!settingsReady || !sessionReady || handled.current) return;
    handled.current = true;
    void (async () => {
      const intent = readIntent(await lastResponse());
      if (!intent) return;
      if (intent.armRecording) {
        // Opens Record with the reason shown. It does NOT start recording:
        // the user's tap on the record button is the consent.
        armWake(intent.reason ?? 'Someone asked you to start recording.');
        router.replace('/');
      } else if (intent.route) {
        router.replace(intent.route as never);
      }
    })();
  }, [settingsReady, sessionReady, router, armWake]);

  // And one tapped while the app is already open.
  useEffect(() => {
    return onNotificationTapped((intent) => {
      if (intent.armRecording) {
        armWake(intent.reason ?? 'Someone asked you to start recording.');
        router.navigate('/');
      } else if (intent.route) {
        router.navigate(intent.route as never);
      }
    });
  }, [router, armWake]);

  if (!settingsReady || !sessionReady) {
    return (
      <View
        style={{ flex: 1, backgroundColor: colors.bg, justifyContent: 'center' }}
      >
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <SafeAreaProvider>
      <QueryClientProvider client={client}>
        <StatusBar style="light" />
        <Stack
          screenOptions={{
            headerStyle: { backgroundColor: colors.bg },
            headerTintColor: colors.text,
            headerTitleStyle: { fontWeight: '600' },
            contentStyle: { backgroundColor: colors.bg },
          }}
        >
          <Stack.Protected guard={signedIn}>
            <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
            <Stack.Screen
              name="conversation/[id]"
              options={{ title: 'Conversation' }}
            />
            <Stack.Screen name="memory" options={{ title: 'Memory' }} />
            <Stack.Screen
              name="loops"
              options={{ title: 'Open loops' }}
            />
            <Stack.Screen name="people" options={{ title: 'People' }} />
            <Stack.Screen name="recall" options={{ title: 'Recall' }} />
            <Stack.Screen
              name="changes"
              options={{ title: 'What keeps changing' }}
            />
          </Stack.Protected>

          <Stack.Protected guard={!signedIn}>
            <Stack.Screen name="sign-in" options={{ headerShown: false }} />
          </Stack.Protected>

          {/* Settings stays reachable signed out: you need it to point the
              app at a server before you can sign in at all. */}
          <Stack.Screen name="settings" options={{ title: 'Settings' }} />
        </Stack>
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}
