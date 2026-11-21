/**
 * People: the identities behind the names in your conversations.
 *
 * Two things this screen is for, and the second is the valuable one.
 *
 * **1. Fixing duplicates.** Whisper hears "Pulkit" as "Pull kit", and until
 * phase B those were two people in a product whose whole claim is who
 * committed to what. Most of that is now automatic; what is left is offered
 * as a suggestion and applied only on a tap. A wrong merge silently
 * reassigns someone's commitments and is nearly impossible to notice
 * afterwards, so the server never decides one on its own.
 *
 * **2. Saying which one is you.** One tap fixes the own/owed split in the
 * open-loops ledger for every past and future task. It is information only
 * the user has, so it is never inferred from a name - and it is far more
 * reliable than matching the account's display name, which fails on a
 * nickname, a surname, or any recording where nobody is named aloud.
 */

import { useCallback, useState } from 'react';
import { Alert, ScrollView, Text, View } from 'react-native';

import {
  useMergePeople,
  usePeople,
  useUpdatePerson,
  useVoiceStatus,
} from '../api/hooks';
import type { MergeSuggestion, PersonSummary } from '../api/types';
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
import { colors, radius, space, type } from '../ui/theme';

function PersonRow({
  person,
  onMarkSelf,
  busy,
}: {
  person: PersonSummary;
  onMarkSelf: (person: PersonSummary) => void;
  busy: boolean;
}) {
  return (
    <View
      style={{
        paddingVertical: space.md,
        borderTopWidth: 1,
        borderTopColor: colors.border,
      }}
    >
      <Row style={{ justifyContent: 'space-between', gap: space.sm }}>
        <Text style={[type.body, { flex: 1 }]}>{person.canonical_name}</Text>
        {person.is_self ? (
          <Badge label="you" color={colors.success} />
        ) : (
          <Button
            title="This is me"
            variant="ghost"
            onPress={() => onMarkSelf(person)}
            disabled={busy}
          />
        )}
      </Row>

      <Text style={[type.tiny, { marginTop: space.xs }]}>
        {person.mention_count} mention{person.mention_count === 1 ? '' : 's'}
        {person.session_count > 0
          ? ` · ${person.session_count} conversation${
              person.session_count === 1 ? '' : 's'
            }`
          : ''}
      </Text>

      {/* The alternative spellings we have learned. Shown because they are
          how the user can tell a mis-hearing was absorbed correctly rather
          than silently attached to the wrong person. */}
      {person.aliases.length > 0 ? (
        <Text style={[type.mono, { marginTop: space.xs }]}>
          also heard as: {person.aliases.join(', ')}
        </Text>
      ) : null}
    </View>
  );
}

function SuggestionCard({
  suggestion,
  onMerge,
  busy,
}: {
  suggestion: MergeSuggestion;
  onMerge: (suggestion: MergeSuggestion) => void;
  busy: boolean;
}) {
  return (
    <View
      style={{
        marginTop: space.md,
        padding: space.md,
        borderRadius: radius.md,
        backgroundColor: colors.surfaceAlt,
      }}
    >
      <Text style={type.body}>
        {suggestion.primary_name} and {suggestion.other_name}
      </Text>
      {/* The reason, not just a score - and it can argue AGAINST merging. */}
      <Text style={[type.tiny, { marginTop: space.xs }]}>
        {suggestion.reason}
      </Text>
      <Row style={{ marginTop: space.md, gap: space.sm }}>
        <Button
          title="Same person"
          onPress={() => onMerge(suggestion)}
          disabled={busy}
        />
        <Badge label={`${Math.round(suggestion.similarity * 100)}% alike`} />
      </Row>
    </View>
  );
}

export default function PeopleScreen() {
  const { data, isLoading, error } = usePeople();
  const voice = useVoiceStatus();
  const update = useUpdatePerson();
  const merge = useMergePeople();
  const [problem, setProblem] = useState<string | null>(null);

  const busy = update.isPending || merge.isPending;

  const markSelf = useCallback(
    (person: PersonSummary) => {
      Alert.alert(
        `Is ${person.canonical_name} you?`,
        'This decides what counts as "you owe" rather than "owed to you", for everything already recorded as well as everything new.',
        [
          { text: 'Cancel', style: 'cancel' },
          {
            text: "Yes, that's me",
            onPress: () => {
              void (async () => {
                try {
                  await update.mutateAsync({
                    personId: person.id,
                    patch: { is_self: true },
                  });
                } catch (e) {
                  setProblem(e instanceof Error ? e.message : String(e));
                }
              })();
            },
          },
        ],
      );
    },
    [update],
  );

  const applyMerge = useCallback(
    (suggestion: MergeSuggestion) => {
      Alert.alert(
        'Merge these two?',
        `Everything recorded against ${suggestion.other_name} moves to ${suggestion.primary_name}. Both spellings keep working, and nothing is deleted.`,
        [
          { text: 'Cancel', style: 'cancel' },
          {
            text: 'Merge',
            onPress: () => {
              void (async () => {
                try {
                  await merge.mutateAsync({
                    personId: suggestion.primary_id,
                    otherId: suggestion.other_id,
                  });
                } catch (e) {
                  setProblem(e instanceof Error ? e.message : String(e));
                }
              })();
            },
          },
        ],
      );
    },
    [merge],
  );

  if (isLoading) return <Loading label="Gathering people" />;
  if (error) {
    return (
      <Screen>
        <ErrorBanner message={(error as Error).message} />
      </Screen>
    );
  }
  if (!data || data.people.length === 0) {
    return (
      <Screen>
        <EmptyState
          title="Nobody yet"
          detail="People named in your conversations appear here, so that one person stays one person even when their name is misheard."
        />
      </Screen>
    );
  }

  const nobodyIsSelf = !data.people.some((person) => person.is_self);

  return (
    <Screen>
      <ScrollView contentContainerStyle={{ padding: space.lg }}>
        <Text style={type.title}>People</Text>
        <Text style={[type.dim, { marginTop: space.xs }]}>
          {data.people.length} known
          {data.suggestions.length > 0
            ? ` · ${data.suggestions.length} possible duplicate${
                data.suggestions.length === 1 ? '' : 's'
              }`
            : ''}
        </Text>

        {problem ? (
          <View style={{ marginTop: space.md }}>
            <ErrorBanner message={problem} />
          </View>
        ) : null}

        {/* The single most useful thing the user can tell us, so it is
            prompted rather than hidden behind a row action. */}
        {nobodyIsSelf ? (
          <View
            style={{
              marginTop: space.lg,
              padding: space.md,
              borderRadius: radius.md,
              backgroundColor: colors.surfaceAlt,
            }}
          >
            <Text style={type.heading}>Which one is you?</Text>
            <Text style={[type.dim, { marginTop: space.xs }]}>
              Until you say, Audaxity cannot tell what you owe from what
              you are owed, and commitments stay under &ldquo;not
              attributed&rdquo; rather than being guessed at.
            </Text>
          </View>
        ) : null}

        {data.suggestions.length > 0 ? (
          <Card style={{ marginTop: space.lg }}>
            <Text style={type.heading}>Possibly the same person</Text>
            <Text style={[type.dim, { marginTop: space.xs }]}>
              Nothing is merged until you say so. Audaxity already joins up
              clear mis-hearings on its own; these are the ones it is not
              sure about.
            </Text>
            {data.suggestions.map((suggestion) => (
              <SuggestionCard
                key={`${suggestion.primary_id}-${suggestion.other_id}`}
                suggestion={suggestion}
                onMerge={applyMerge}
                busy={busy}
              />
            ))}
          </Card>
        ) : null}

        <Card style={{ marginTop: space.lg }}>
          <Text style={type.heading}>Everyone</Text>
          {data.people.map((person) => (
            <PersonRow
              key={person.id}
              person={person}
              onMarkSelf={markSelf}
              busy={busy}
            />
          ))}
        </Card>

        {/* Ties the two Tier 1 features together: enrolment is what will
            let "I'll send it" be attributed without a name being spoken. */}
        {voice.data && !voice.data.enrolled ? (
          <Text style={[type.tiny, { marginBottom: space.xl }]}>
            Names only get this far. Enrolling your own voice in Settings is
            what lets Audaxity tell when the person speaking is you, even
            when nobody says a name.
          </Text>
        ) : null}
      </ScrollView>
    </Screen>
  );
}
