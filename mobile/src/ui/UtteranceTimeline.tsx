/**
 * The conversation, sentence by sentence.
 *
 * The transcript view shows one block of text. This shows the grain the
 * product actually reasons about: each sentence with its own timestamp, its
 * place, and the speech act of any item extracted from it.
 *
 * ## Why it is worth a screen
 *
 * It is the only place a user can see **why** an item was extracted, and
 * check it. A commitment card says "send the compliance forms"; this says
 * which sentence that came from, at what time, and that the model read it as
 * a commitment rather than a suggestion. When the extraction is wrong, this
 * is where that becomes visible instead of mysterious.
 *
 * Unlinked sentences are shown too, and shown plainly. Most speech is not a
 * commitment, and hiding the ordinary parts would make the extraction look
 * more certain than it is.
 */

import { useState } from 'react';
import { Text, View } from 'react-native';

import type { UtteranceRow } from '../api/types';
import { ActBadge, Badge, Button, Card, Row } from './components';
import { colors, space, type } from './theme';

const COLLAPSED = 8;

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function UtteranceTimeline({
  utterances,
}: {
  utterances: UtteranceRow[];
}) {
  const [expanded, setExpanded] = useState(false);
  if (utterances.length === 0) return null;

  const shown = expanded ? utterances : utterances.slice(0, COLLAPSED);
  const linked = utterances.filter((row) => row.act !== null).length;
  const placed = utterances.filter((row) => row.place_name).length;

  return (
    <Card>
      <Row style={{ justifyContent: 'space-between' }}>
        <Text style={type.heading}>Sentence by sentence</Text>
        <Badge label={`${utterances.length}`} />
      </Row>
      <Text style={[type.dim, { marginTop: space.xs }]}>
        {linked} carried something worth keeping
        {placed > 0 ? ` · ${placed} know where you were` : ''}
      </Text>

      {shown.map((row) => (
        <View
          key={row.id}
          style={{
            paddingVertical: space.sm,
            borderTopWidth: 1,
            borderTopColor: colors.border,
          }}
        >
          <Row style={{ justifyContent: 'space-between', gap: space.sm }}>
            <Text style={[type.mono, { flex: 1 }]}>
              {clock(row.started_at)}
              {row.duration_s ? `  ${row.duration_s.toFixed(1)}s` : ''}
              {row.place_name ? `  ${row.place_name}` : ''}
            </Text>
            {row.act ? <ActBadge act={row.act} /> : null}
          </Row>
          <Text
            style={[
              type.body,
              { marginTop: space.xs },
              // Ordinary speech is dimmed rather than hidden: most of a
              // conversation is not a commitment, and removing it would make
              // the extraction look more certain than it is.
              row.act ? null : { color: colors.textDim },
            ]}
          >
            {row.text}
          </Text>
          {row.item_title && row.item_title !== row.text ? (
            <Text style={[type.tiny, { marginTop: space.xs }]}>
              became: {row.item_title}
              {row.item_owner && row.item_owner !== 'unknown'
                ? ` (${row.item_owner})`
                : ''}
            </Text>
          ) : null}
        </View>
      ))}

      {utterances.length > COLLAPSED ? (
        <Row style={{ marginTop: space.md }}>
          <Button
            title={
              expanded
                ? 'Show less'
                : `Show all ${utterances.length} sentences`
            }
            variant="ghost"
            onPress={() => setExpanded(!expanded)}
          />
        </Row>
      ) : null}
    </Card>
  );
}
