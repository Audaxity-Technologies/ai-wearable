/**
 * Approvals — the product's signature screen.
 *
 * Every card shows what will happen, *why* it was proposed, and the verbatim
 * words it came from. Nothing here has run: these are proposals, and the
 * backend will not execute any of them without the explicit approval this
 * screen sends.
 */

import { useRouter } from 'expo-router';
import { useState } from 'react';
import {
  Alert,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import {
  useApproveAction,
  useEditAction,
  usePendingActions,
  useRejectAction,
} from '@/api/hooks';
import type { ActionResponse } from '@/api/types';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  Loading,
  Quote,
  Row,
  Screen,
} from '@/ui/components';
import { DatePrompt } from '@/ui/DatePrompt';
import { colors, space, type } from '@/ui/theme';

const TYPE_LABEL: Record<string, string> = {
  reminder: 'Reminder',
  calendar_event: 'Calendar event',
  task: 'Task',
  email_draft: 'Email draft',
};

export default function ApprovalsScreen() {
  const pending = usePendingActions();
  const approve = useApproveAction();
  const reject = useRejectAction();
  const edit = useEditAction();
  const [clarifying, setClarifying] = useState<string | null>(null);
  const router = useRouter();
  const [working, setWorking] = useState<string | null>(null);

  async function onApprove(action: ActionResponse) {
    setWorking(action.id);
    try {
      const result = await approve.mutateAsync(action.id);
      if (result.status === 'failed') {
        Alert.alert(
          'Could not complete',
          result.execution_result ?? 'The action failed.',
        );
      }
    } catch (error) {
      Alert.alert('Approval failed', String(error));
    } finally {
      setWorking(null);
    }
  }

  function onReject(action: ActionResponse) {
    Alert.alert(
      'Reject this?',
      `"${action.title}" will not be proposed again.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Reject',
          style: 'destructive',
          onPress: () => {
            setWorking(action.id);
            reject
              .mutateAsync({ id: action.id, reason: 'rejected in app' })
              .finally(() => setWorking(null));
          },
        },
      ],
    );
  }

  if (pending.isLoading) return <Loading label="Loading proposals…" />;

  const actions = pending.data?.actions ?? [];
  const needsClarification = pending.data?.needs_clarification_count ?? 0;

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={styles.container}
        refreshControl={
          <RefreshControl
            refreshing={pending.isFetching}
            onRefresh={() => void pending.refetch()}
            tintColor={colors.textDim}
          />
        }
      >
        {pending.isError ? (
          <ErrorBanner message={String(pending.error)} />
        ) : null}

        {actions.length === 0 ? (
          <EmptyState
            title="Nothing waiting"
            detail="Proposed reminders, calendar events and tasks appear here after a conversation is processed. Nothing is ever carried out without your approval."
          />
        ) : (
          <>
            <Text style={[type.dim, { marginBottom: space.sm }]}>
              {actions.length} awaiting your decision
              {needsClarification > 0
                ? ` · ${needsClarification} need a date confirmed`
                : ''}
            </Text>

            {actions.map((action) => (
              <Card key={action.id}>
                <Row style={{ justifyContent: 'space-between' }}>
                  <Badge
                    label={TYPE_LABEL[action.type] ?? action.type}
                    color={colors.accent}
                  />
                  {action.needs_clarification ? (
                    <Badge label="date unclear" color={colors.warning} />
                  ) : null}
                </Row>

                <Text style={[type.heading, { marginTop: space.sm }]}>
                  {action.title}
                </Text>

                {action.payload.date ? (
                  <Text style={[type.body, { marginTop: space.xs }]}>
                    {action.payload.date}
                    {action.payload.time ? ` at ${action.payload.time}` : ''}
                  </Text>
                ) : action.payload.due_raw ? (
                  <Text style={[type.body, { marginTop: space.xs }]}>
                    &ldquo;{action.payload.due_raw}&rdquo; — needs a real date
                  </Text>
                ) : null}

                {clarifying === action.id ? (
                  <DatePrompt
                    raw={action.payload.due_raw}
                    busy={edit.isPending}
                    onCancel={() => setClarifying(null)}
                    onConfirm={async (date, time) => {
                      await edit.mutateAsync({
                        id: action.id,
                        patch: { date, time },
                      });
                      setClarifying(null);
                    }}
                  />
                ) : null}

                {/* Why this exists. The brief requires every proposal to
                    carry its justification. */}
                <Text style={[type.dim, { marginTop: space.sm }]}>
                  {action.reason}
                </Text>

                <Quote text={action.source_quote} />

                {action.needs_clarification && clarifying !== action.id ? (
                  <Button
                    title="Set a date"
                    variant="secondary"
                    onPress={() => setClarifying(action.id)}
                    style={{ marginTop: space.md }}
                  />
                ) : null}

                <Row style={{ marginTop: space.lg, gap: space.sm }}>
                  <Button
                    title="Approve"
                    onPress={() => void onApprove(action)}
                    loading={working === action.id && approve.isPending}
                    style={{ flex: 1 }}
                  />
                  <Button
                    title="Reject"
                    variant="danger"
                    onPress={() => onReject(action)}
                    loading={working === action.id && reject.isPending}
                    style={{ flex: 1 }}
                  />
                </Row>
                <Button
                  title="See the conversation"
                  variant="ghost"
                  onPress={() =>
                    router.push(`/conversation/${action.source_session_id}`)
                  }
                  style={{ marginTop: space.xs }}
                />
              </Card>
            ))}

            <View style={{ padding: space.md }}>
              <Text style={type.tiny}>
                Approving records the action locally. Calendar and email
                integrations are not connected yet, so nothing leaves this
                network.
              </Text>
            </View>
          </>
        )}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  container: { padding: space.lg, paddingBottom: space.xxl },
});
