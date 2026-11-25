/**
 * Who was in this conversation.
 *
 * The only attribution evidence that is **certain**. A voiceprint is
 * probabilistic and an extracted owner is the model's reading of the words;
 * "Pulkit was here" is a fact only the user has, and it does not degrade with
 * noise, distance or accent.
 *
 * ## The wording matters more than the UI
 *
 * A label records **presence, not authorship**. Three people labelled on a
 * three-person conversation says all three were there and nothing more. So
 * this card never says "said by", and the copy explains what the label buys:
 * questions about that person start searching this conversation.
 *
 * It also works after the fact, which is the whole point - you label it when
 * you next open it, not while you are talking.
 */

import { useCallback, useState } from 'react';
import { Text, TextInput, View } from 'react-native';

import { useAddParticipant, useRemoveParticipant } from '../api/hooks';
import type { Participation } from '../api/types';
import { Badge, Button, Card, Row } from './components';
import { colors, radius, space, type } from './theme';

export function ParticipantsCard({
  sessionId,
  participants,
}: {
  sessionId: string;
  participants: Participation[];
}) {
  const add = useAddParticipant(sessionId);
  const remove = useRemoveParticipant(sessionId);
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setError(null);
    try {
      await add.mutateAsync(trimmed);
      setName('');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [add, name]);

  const busy = add.isPending || remove.isPending;

  return (
    <Card>
      <Row style={{ justifyContent: 'space-between' }}>
        <Text style={type.heading}>Who was here</Text>
        {participants.length > 0 ? (
          <Badge label={String(participants.length)} />
        ) : null}
      </Row>

      {participants.length > 0 ? (
        <Row style={{ marginTop: space.sm, gap: space.xs, flexWrap: 'wrap' }}>
          {participants.map((entry) => (
            <Row key={entry.person_id} style={{ gap: space.xs }}>
              <Badge
                label={entry.role === 'self' ? `${entry.name} (you)` : entry.name}
                color={entry.role === 'self' ? colors.success : colors.accent}
              />
              <Button
                title="×"
                variant="ghost"
                onPress={() => void remove.mutateAsync(entry.person_id)}
                disabled={busy}
              />
            </Row>
          ))}
        </Row>
      ) : (
        <Text style={[type.dim, { marginTop: space.xs }]}>
          Nobody named yet. Saying who was here is what lets you later ask
          what they committed to.
        </Text>
      )}

      <Row style={{ marginTop: space.md, gap: space.sm }}>
        <TextInput
          value={name}
          onChangeText={setName}
          placeholder="Add a name"
          placeholderTextColor={colors.textFaint}
          autoCapitalize="words"
          onSubmitEditing={() => void submit()}
          style={{
            flex: 1,
            color: colors.text,
            fontSize: 15,
            paddingVertical: space.sm,
            paddingHorizontal: space.md,
            borderRadius: radius.sm,
            backgroundColor: colors.surfaceAlt,
          }}
        />
        <Button
          title={add.isPending ? '...' : 'Add'}
          onPress={() => void submit()}
          disabled={busy || !name.trim()}
        />
      </Row>

      {error ? (
        <Text style={[type.tiny, { color: colors.danger, marginTop: space.sm }]}>
          {error}
        </Text>
      ) : null}

      {/* Said plainly, because the easy mistake is to assume a label means
          the app now knows who said each sentence. It does not. */}
      <Text style={[type.tiny, { marginTop: space.md }]}>
        A name records that they were here, not who said which sentence.
        Spelling is forgiving — &ldquo;Pull kit&rdquo; finds the Pulkit you
        already have.
      </Text>
    </Card>
  );
}
