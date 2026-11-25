/**
 * One conversation: summary, extracted items, transcript.
 *
 * Every extracted item shows its speech act, its owner and the words it came
 * from. If the AI got something wrong, you can see exactly where it got it.
 */

import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import {
  useAnalysis,
  useDeleteSession,
  useReceipt,
  useSession,
  useSessionUtterances,
  useTranscript,
} from '@/api/hooks';
import {
  ActBadge,
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
import { placeName } from '@/api/types';
import { ParticipantsCard } from '@/ui/ParticipantsCard';
import { ReceiptCard } from '@/ui/ReceiptCard';
import { UtteranceTimeline } from '@/ui/UtteranceTimeline';
import {
  colors,
  formatDate,
  formatDuration,
  formatTime,
  space,
  statusLabel,
  type,
} from '@/ui/theme';

export default function ConversationScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const sessionId = String(id ?? '');
  const router = useRouter();

  const session = useSession(sessionId);
  const analysis = useAnalysis(sessionId, session.data?.status);
  const transcript = useTranscript(sessionId);
  const receipt = useReceipt(sessionId);
  const utterances = useSessionUtterances(sessionId);
  const remove = useDeleteSession();
  const [showTranscript, setShowTranscript] = useState(false);

  if (session.isLoading) return <Loading />;
  if (session.isError) {
    return (
      <Screen>
        <ScrollView contentContainerStyle={styles.container}>
          <ErrorBanner message={String(session.error)} />
        </ScrollView>
      </Screen>
    );
  }

  const data = session.data!;
  const settled = data.status === 'ready' || data.status === 'failed';

  function confirmDelete() {
    Alert.alert(
      'Delete this conversation?',
      'The audio, transcript and anything derived only from it are removed. This cannot be undone.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            await remove.mutateAsync(sessionId);
            router.back();
          },
        },
      ],
    );
  }

  return (
    <Screen>
      <ScrollView contentContainerStyle={styles.container}>
        <Card>
          <Row style={{ justifyContent: 'space-between' }}>
            <Text style={type.dim}>
              {formatDate(data.started_at)} · {formatTime(data.started_at)}
            </Text>
            <Badge
              label={statusLabel(data.status)}
              color={
                data.status === 'ready'
                  ? colors.success
                  : data.status === 'failed'
                    ? colors.danger
                    : colors.accent
              }
            />
          </Row>
          <Text style={[type.title, { marginTop: space.sm, fontSize: 20 }]}>
            {analysis.data?.title || data.title || 'Untitled conversation'}
          </Text>
          <Text style={[type.dim, { marginTop: space.xs }]}>
            {formatDuration(data.duration_s)} · {data.segment_count} segment
            {data.segment_count === 1 ? '' : 's'}
          </Text>

          {/* Names only. Coordinates are an index, not an answer - nobody
              asks what they said at 28.524, 77.186. */}
          {data.places && data.places.length > 0 ? (
            <Text style={[type.dim, { marginTop: space.xs }]}>
              {data.places.map((entry) => placeName(entry.place)).join(' · ')}
            </Text>
          ) : null}

          {!settled ? (
            <Text style={[type.dim, { marginTop: space.md }]}>
              Still processing — this updates on its own.
            </Text>
          ) : null}

          {data.status === 'failed' ? (
            <Text style={[type.dim, { marginTop: space.md, color: colors.danger }]}>
              Failed at {data.failed_stage}: {data.error}
            </Text>
          ) : null}
        </Card>

        <ParticipantsCard
          sessionId={sessionId}
          participants={data.participants ?? []}
        />

        {/* Placed above the summary deliberately: whether to believe the
            analysis is prior to reading it. */}
        {receipt.data ? <ReceiptCard receipt={receipt.data} /> : null}

        {analysis.data?.degraded ? (
          <ErrorBanner
            message={`The transcript was kept, but it could not be analysed: ${
              analysis.data.degraded_reason ?? 'unknown reason'
            }`}
          />
        ) : null}

        {analysis.data?.summary ? (
          <Card>
            <Text style={type.heading}>Summary</Text>
            <Text style={[type.body, { marginTop: space.sm }]}>
              {analysis.data.summary}
            </Text>
            {analysis.data.participants.length > 0 ? (
              <Row style={{ marginTop: space.md, flexWrap: 'wrap' }}>
                {analysis.data.participants.map((p) => (
                  <Badge
                    key={p.name}
                    label={p.speaking ? p.name : `${p.name} (mentioned)`}
                  />
                ))}
              </Row>
            ) : null}
            {analysis.data.projects.length > 0 ? (
              <Row style={{ marginTop: space.sm, flexWrap: 'wrap' }}>
                {analysis.data.projects.map((project) => (
                  <Badge key={project} label={project} color={colors.accent} />
                ))}
              </Row>
            ) : null}
          </Card>
        ) : null}

        {analysis.data && analysis.data.items.length > 0 ? (
          <>
            <Text style={[type.heading, styles.sectionHeading]}>
              What was said
            </Text>
            {analysis.data.items.map((item, index) => (
              <Card key={`${item.title}-${index}`}>
                <Row style={{ justifyContent: 'space-between' }}>
                  <ActBadge act={item.act} />
                  {item.due.resolution === 'ambiguous' ? (
                    <Badge label="date unclear" color={colors.warning} />
                  ) : null}
                </Row>
                <Text style={[type.body, { marginTop: space.sm, fontWeight: '600' }]}>
                  {item.title}
                </Text>
                <Text style={[type.dim, { marginTop: space.xs }]}>
                  {item.owner === 'unknown'
                    ? 'Nobody clearly took this on'
                    : item.owner === 'group'
                      ? 'Agreed between the speakers'
                      : item.owner}
                  {item.due.date
                    ? ` · due ${item.due.date}${item.due.time ? ` at ${item.due.time}` : ''}`
                    : item.due.raw
                      ? ` · “${item.due.raw}”`
                      : ''}
                </Text>
                <Quote text={item.source_quote} />
              </Card>
            ))}
          </>
        ) : settled && analysis.isFetched && !analysis.data?.degraded ? (
          <EmptyState
            title="Nothing worth remembering"
            detail="No commitments or decisions were found in this conversation."
          />
        ) : null}

        {transcript.data?.transcript ? (
          <>
            <Pressable onPress={() => setShowTranscript((v) => !v)}>
              <Text style={[type.heading, styles.sectionHeading]}>
                Transcript {showTranscript ? '▾' : '▸'}
              </Text>
            </Pressable>
            {showTranscript ? (
              <Card>
                {transcript.data.segments.map((segment) => (
                  <View key={segment.segment_id} style={{ marginBottom: space.md }}>
                    <Text style={type.tiny}>
                      segment {segment.seq + 1} ·{' '}
                      {formatDuration(segment.duration_s)}
                    </Text>
                    <Text style={[type.body, { marginTop: space.xs }]}>
                      {segment.text ?? '(no speech detected)'}
                    </Text>
                  </View>
                ))}
              </Card>
            ) : null}
          </>
        ) : null}

        <Button
          title="Delete this conversation"
          variant="danger"
          onPress={confirmDelete}
          loading={remove.isPending}
          style={{ marginTop: space.lg }}
        />
        <Text style={[type.tiny, { marginTop: space.sm, textAlign: 'center' }]}>
          Removes the audio, transcript, and memories only this conversation
          supports.
        </Text>
        {utterances.data ? (
          <UtteranceTimeline utterances={utterances.data.utterances} />
        ) : null}

      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  container: { padding: space.lg, paddingBottom: space.xxl },
  sectionHeading: { marginTop: space.lg, marginBottom: space.sm },
});
