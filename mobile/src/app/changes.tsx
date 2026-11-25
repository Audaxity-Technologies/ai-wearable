/**
 * What keeps changing.
 *
 * The screen no competitor can build, and the reason is structural rather
 * than a matter of effort: this needs every version of a fact kept, with the
 * interval each one was believed over. A product that stores "the current
 * answer" can tell you the launch date. It cannot tell you the launch date
 * has moved four times, because it threw the first three away.
 *
 * ## The sentence this screen exists to say
 *
 *   "Your launch date has moved 3 times."
 *
 * That is not a to-do. It is the thing a person would want pointed out and
 * would never think to search for - which is why it is a screen rather than
 * a query.
 *
 * ## Why the old versions are shown, not just counted
 *
 * A count is a claim; the chain is the evidence. Tapping a fact shows every
 * version with the window it was believed over, so "we said June in March"
 * is checkable rather than asserted. Nothing is deleted when a fact changes,
 * and this is where that pays off.
 */

import { useState } from 'react';
import { ScrollView, Text, View } from 'react-native';

import { useMemoryChanges, useMemoryHistory } from '../api/hooks';
import type { MemoryChange, MemoryResponse } from '../api/types';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  Loading,
  Row,
  Screen,
} from '../ui/components';
import { colors, space, type } from '../ui/theme';

function day(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

/** The interesting attribute, when there is one. */
function valueOf(memory: MemoryResponse): string | null {
  return (
    memory.attributes?.due_date ??
    memory.attributes?.owner ??
    null
  );
}

function HistoryList({ versions }: { versions: MemoryResponse[] }) {
  return (
    <View style={{ marginTop: space.md }}>
      {versions.map((version, index) => {
        const value = valueOf(version);
        const current = version.is_current !== false;
        return (
          <View
            key={version.id}
            style={{
              paddingVertical: space.sm,
              borderTopWidth: 1,
              borderTopColor: colors.border,
            }}
          >
            <Row style={{ justifyContent: 'space-between', gap: space.sm }}>
              <Text style={[type.body, { flex: 1 }]}>
                {value ?? version.content}
              </Text>
              {current ? (
                <Badge label="now" color={colors.success} />
              ) : (
                <Badge label={`version ${index + 1}`} />
              )}
            </Row>
            <Text style={[type.tiny, { marginTop: space.xs }]}>
              {/* Both axes, in the user's terms: when they were told, and
                  how long it stood. They come apart constantly, and the gap
                  is where the interesting questions live. */}
              heard {day(version.observed_at ?? version.first_seen)}
              {current
                ? ' · still the answer'
                : ` · held until ${day(version.valid_to)}`}
            </Text>
            {version.source_quotes?.[0] ? (
              <Text style={[type.mono, { marginTop: space.xs }]}>
                &ldquo;{version.source_quotes[0]}&rdquo;
              </Text>
            ) : null}
          </View>
        );
      })}
    </View>
  );
}

function ChangeCard({ change }: { change: MemoryChange }) {
  const [open, setOpen] = useState(false);
  const history = useMemoryHistory(open ? change.key : null);

  return (
    <Card>
      <Row style={{ justifyContent: 'space-between', gap: space.sm }}>
        <Text style={[type.heading, { flex: 1 }]}>{change.name}</Text>
        <Badge
          label={`moved ${change.change_count}×`}
          color={change.change_count >= 3 ? colors.warning : colors.accent}
        />
      </Row>

      {change.current ? (
        <Text style={[type.dim, { marginTop: space.xs }]}>
          Now: {valueOf(change.current) ?? change.current.content}
        </Text>
      ) : null}

      <Row style={{ marginTop: space.md }}>
        <Button
          title={open ? 'Hide the history' : 'Show every version'}
          variant="ghost"
          onPress={() => setOpen(!open)}
        />
      </Row>

      {open ? (
        history.isLoading ? (
          <Loading label="Reading the history" />
        ) : history.data ? (
          <HistoryList versions={history.data.memories} />
        ) : null
      ) : null}
    </Card>
  );
}

export default function ChangesScreen() {
  const { data, isLoading, error } = useMemoryChanges();

  if (isLoading) return <Loading label="Looking for what moved" />;
  if (error) {
    return (
      <Screen>
        <ErrorBanner message={(error as Error).message} />
      </Screen>
    );
  }
  if (!data || data.length === 0) {
    return (
      <Screen>
        <EmptyState
          title="Nothing has changed yet"
          detail="When a date, an owner or a plan is revised in a later conversation, both versions are kept and the change appears here."
        />
      </Screen>
    );
  }

  const total = data.reduce((sum, change) => sum + change.change_count, 0);

  return (
    <Screen>
      <ScrollView contentContainerStyle={{ padding: space.lg }}>
        <Text style={type.title}>What keeps changing</Text>
        <Text style={[type.dim, { marginTop: space.xs }]}>
          {data.length} {data.length === 1 ? 'thing' : 'things'} revised ·{' '}
          {total} {total === 1 ? 'change' : 'changes'} in all
        </Text>
        <Text style={[type.tiny, { marginTop: space.sm, marginBottom: space.lg }]}>
          Nothing is deleted when something changes. Every version is kept
          with the window it was believed over, so you can see what you
          thought at the time rather than only what survived.
        </Text>

        {data.map((change) => (
          <ChangeCard key={change.key} change={change} />
        ))}
      </ScrollView>
    </Screen>
  );
}
