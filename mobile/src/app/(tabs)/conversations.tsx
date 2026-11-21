import { useRouter } from 'expo-router';
import {
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
} from 'react-native';

import { useSessions } from '@/api/hooks';
import type { SessionSummary } from '@/api/types';
import {
  Badge,
  Card,
  EmptyState,
  ErrorBanner,
  Loading,
  Row,
  Screen,
} from '@/ui/components';
import {
  colors,
  formatDate,
  formatDuration,
  formatTime,
  space,
  statusLabel,
  type,
} from '@/ui/theme';

function statusColor(status: string): string {
  if (status === 'ready') return colors.success;
  if (status === 'failed') return colors.danger;
  if (status === 'analyzed_degraded') return colors.warning;
  return colors.accent;
}

/** Group by day so a scroll reads like a diary rather than a log. */
function groupByDay(sessions: SessionSummary[]) {
  const groups = new Map<string, SessionSummary[]>();
  for (const session of sessions) {
    const key = formatDate(session.started_at) || 'Undated';
    const list = groups.get(key) ?? [];
    list.push(session);
    groups.set(key, list);
  }
  return [...groups.entries()];
}

export default function ConversationsScreen() {
  const sessions = useSessions();
  const router = useRouter();

  if (sessions.isLoading) return <Loading label="Loading conversations…" />;

  const data = sessions.data ?? [];

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={styles.container}
        refreshControl={
          <RefreshControl
            refreshing={sessions.isFetching}
            onRefresh={() => void sessions.refetch()}
            tintColor={colors.textDim}
          />
        }
      >
        {sessions.isError ? (
          <ErrorBanner message={String(sessions.error)} />
        ) : null}

        {data.length === 0 ? (
          <EmptyState
            title="No conversations yet"
            detail="Record one from the Record tab and it will appear here once it has been transcribed."
          />
        ) : (
          groupByDay(data).map(([day, items]) => (
            <Fragment key={day} day={day} items={items} router={router} />
          ))
        )}
      </ScrollView>
    </Screen>
  );
}

function Fragment({
  day,
  items,
  router,
}: {
  day: string;
  items: SessionSummary[];
  router: ReturnType<typeof useRouter>;
}) {
  return (
    <>
      <Text style={[type.dim, styles.dayHeading]}>{day}</Text>
      {items.map((session) => (
        <Pressable
          key={session.id}
          onPress={() => router.push(`/conversation/${session.id}`)}
          accessibilityRole="button"
        >
          <Card>
            <Row style={{ justifyContent: 'space-between' }}>
              <Text style={type.dim}>{formatTime(session.started_at)}</Text>
              <Badge
                label={statusLabel(session.status)}
                color={statusColor(session.status)}
              />
            </Row>
            <Text style={[type.heading, { marginTop: space.xs }]}>
              {session.title ?? 'Untitled conversation'}
            </Text>
            <Text style={[type.dim, { marginTop: space.xs }]}>
              {formatDuration(session.duration_s)} · {session.segment_count}{' '}
              segment{session.segment_count === 1 ? '' : 's'}
              {session.language ? ` · ${session.language}` : ''}
            </Text>
            {session.status === 'failed' && session.error ? (
              <Text style={[type.dim, { marginTop: space.xs, color: colors.danger }]}>
                {session.failed_stage}: {session.error}
              </Text>
            ) : null}
          </Card>
        </Pressable>
      ))}
    </>
  );
}

const styles = StyleSheet.create({
  container: { padding: space.lg, paddingBottom: space.xxl },
  dayHeading: {
    textTransform: 'uppercase',
    letterSpacing: 1,
    marginBottom: space.sm,
    marginTop: space.sm,
  },
});
