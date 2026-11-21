/**
 * Memory: what the assistant believes, and why.
 *
 * Conflicts are shown rather than resolved. When two conversations disagree
 * about a deadline, both are kept and surfaced here — that disagreement is
 * usually the thing the user most needs to know.
 */

import { useState } from 'react';
import {
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { useApi, useMemories } from '@/api/hooks';
import type { MemoryKind } from '@/api/types';
import {
  Badge,
  Card,
  EmptyState,
  ErrorBanner,
  Loading,
  Quote,
  Row,
  Screen,
} from '@/ui/components';
import { colors, formatDate, radius, space, type } from '@/ui/theme';

const FILTERS: { label: string; kind?: MemoryKind; conflicts?: boolean }[] = [
  { label: 'All' },
  { label: 'People', kind: 'person' },
  { label: 'Projects', kind: 'project' },
  { label: 'Facts', kind: 'fact' },
  { label: 'Conflicts', conflicts: true },
];

export default function MemoryScreen() {
  const [index, setIndex] = useState(0);
  const filter = FILTERS[index];
  const memories = useMemories(filter.kind, filter.conflicts ?? false);
  const api = useApi();

  function confirmForget(id: string, name: string) {
    Alert.alert('Forget this?', `"${name}" will be removed from memory.`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Forget',
        style: 'destructive',
        onPress: async () => {
          await api?.deleteMemory(id);
          void memories.refetch();
        },
      },
    ]);
  }

  return (
    <Screen>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.filters}
      >
        {FILTERS.map((f, i) => (
          <Pressable
            key={f.label}
            onPress={() => setIndex(i)}
            style={[styles.filter, i === index && styles.filterActive]}
          >
            <Text
              style={[
                type.dim,
                i === index && { color: colors.text, fontWeight: '600' },
              ]}
            >
              {f.label}
            </Text>
          </Pressable>
        ))}
      </ScrollView>

      {memories.isLoading ? (
        <Loading />
      ) : (
        <ScrollView
          contentContainerStyle={styles.container}
          refreshControl={
            <RefreshControl
              refreshing={memories.isFetching}
              onRefresh={() => void memories.refetch()}
              tintColor={colors.textDim}
            />
          }
        >
          {memories.isError ? (
            <ErrorBanner message={String(memories.error)} />
          ) : null}

          {(memories.data ?? []).length === 0 ? (
            <EmptyState
              title={
                filter.conflicts ? 'No contradictions' : 'Nothing remembered yet'
              }
              detail={
                filter.conflicts
                  ? 'When a later conversation changes a deadline or an owner, both versions are kept and shown here.'
                  : 'People, projects and commitments build up here as you record.'
              }
            />
          ) : (
            (memories.data ?? []).map((memory) => (
              <Card key={memory.id}>
                <Row style={{ justifyContent: 'space-between' }}>
                  <Badge label={memory.kind} color={colors.accent} />
                  <Row>
                    {memory.has_conflict ? (
                      <Badge label="conflicting" color={colors.danger} />
                    ) : null}
                    {memory.is_cross_conversation ? (
                      <Badge
                        label={`${memory.mention_count} conversations`}
                        color={colors.success}
                      />
                    ) : null}
                  </Row>
                </Row>

                <Text style={[type.body, { marginTop: space.sm, fontWeight: '600' }]}>
                  {memory.name}
                </Text>
                <Text style={[type.dim, { marginTop: space.xs }]}>
                  {memory.content}
                </Text>

                {memory.attributes.due_date ? (
                  <Text style={[type.dim, { marginTop: space.xs }]}>
                    due {memory.attributes.due_date}
                  </Text>
                ) : null}

                {memory.source_quotes.slice(0, 2).map((quote, i) => (
                  <Quote key={i} text={quote} />
                ))}

                <View style={styles.footer}>
                  <Text style={type.tiny}>
                    last heard {formatDate(memory.last_seen)}
                  </Text>
                  <Pressable onPress={() => confirmForget(memory.id, memory.name)}>
                    <Text style={[type.tiny, { color: colors.danger }]}>
                      Forget
                    </Text>
                  </Pressable>
                </View>
              </Card>
            ))
          )}
        </ScrollView>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  container: { padding: space.lg, paddingBottom: space.xxl },
  filters: { gap: space.sm, paddingHorizontal: space.lg, paddingVertical: space.md },
  filter: {
    paddingVertical: space.sm,
    paddingHorizontal: space.lg,
    borderRadius: radius.pill,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
  },
  filterActive: { backgroundColor: colors.surfaceAlt, borderColor: colors.accent },
  footer: {
    marginTop: space.md,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
});
