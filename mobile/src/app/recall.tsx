/**
 * Recall: ask a question, see the filter it became, read the sentences.
 *
 * This is the screen the whole memory plan exists for, and it is deliberately
 * not the Ask tab. Ask composes prose with citations; Recall shows **the plan
 * and the evidence**:
 *
 *   "what did we decide at Blue Tokai yesterday"
 *     -> decision; at Blue Tokai; yesterday
 *     -> the three sentences actually spoken there
 *
 * ## Why showing the filter is the feature
 *
 * Every competitor returns an answer. None of them shows you what it
 * understood, so a wrong answer is indistinguishable from a right one. Here,
 * the resolved filter is on screen above the results: if Recall misread the
 * question you can see that it misread it, rather than wondering whether
 * your memory or the product is wrong.
 *
 * ## Clarification is an interaction, not an error
 *
 * When a question is ambiguous the backend refuses to run it and returns the
 * options. Tapping one **rewrites the question** with the explicit value and
 * re-asks - so "that day" becomes "on 21 November" in the user's own words,
 * and they can see what they committed to. A guessed range would have
 * returned real sentences from the wrong moment, which reads as an answer.
 */

import { useCallback, useState } from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  Text,
  TextInput,
  View,
} from 'react-native';

import { useAskQuestion } from '../api/hooks';
import type {
  Clarification,
  QuestionAnswer,
  ResolvedQuestion,
  UtteranceRow,
} from '../api/types';
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
} from '../ui/components';
import { colors, radius, space, type } from '../ui/theme';

/** Shaped to show off what the filter can express, not to flatter it. */
const EXAMPLES = [
  'what did we decide yesterday',
  'what do I owe',
  'what did I commit to at the office',
  'did I ever mention the exam',
];

function FilterCard({ resolved }: { resolved: ResolvedQuestion }) {
  const chips: { label: string; color?: string }[] = [];
  for (const act of resolved.acts) chips.push({ label: act });
  if (resolved.actionable_only && resolved.acts.length === 0) {
    chips.push({ label: 'anything owed' });
  }
  if (resolved.about_self) chips.push({ label: 'by you' });
  for (const name of resolved.person_names) {
    // "with", not "by": a participant label records presence, not who spoke
    // which sentence, and the wording must not overstate it.
    chips.push({ label: `with ${name}`, color: colors.accent });
  }
  if (resolved.time_range) {
    chips.push({ label: resolved.time_range.label, color: colors.accent });
  }
  if (resolved.place) {
    chips.push({ label: `at ${resolved.place}`, color: colors.accent });
  }
  for (const term of resolved.terms) chips.push({ label: `"${term}"` });

  return (
    <Card>
      <Row style={{ justifyContent: 'space-between' }}>
        <Text style={type.dim}>What I searched for</Text>
        <Badge label={resolved.kind.replace('_', ' ')} />
      </Row>
      {chips.length > 0 ? (
        <Row style={{ marginTop: space.sm, gap: space.xs, flexWrap: 'wrap' }}>
          {chips.map((chip) => (
            <Badge key={chip.label} label={chip.label} color={chip.color} />
          ))}
        </Row>
      ) : (
        <Text style={[type.tiny, { marginTop: space.sm }]}>
          Nothing to search on — try naming a person, a day, a place, or what
          was decided.
        </Text>
      )}
    </Card>
  );
}

function ClarifyCard({
  clarification,
  onChoose,
}: {
  clarification: Clarification;
  onChoose: (option: { value: string; label: string }) => void;
}) {
  return (
    <Card>
      <Text style={type.heading}>{clarification.question}</Text>
      <Text style={[type.dim, { marginTop: space.xs }]}>
        I would rather ask than guess — picking one would show you real
        sentences from the wrong {clarification.kind === 'time' ? 'day' : 'person'}.
      </Text>
      <Row style={{ marginTop: space.md, gap: space.sm, flexWrap: 'wrap' }}>
        {clarification.options.map((option) => (
          <Button
            key={option.value}
            title={option.label}
            variant="secondary"
            onPress={() => onChoose(option)}
          />
        ))}
      </Row>
      {clarification.options.length === 0 ? (
        <Text style={[type.tiny, { marginTop: space.sm }]}>
          Say which one you mean in the question itself — a date, or a full
          name.
        </Text>
      ) : null}
    </Card>
  );
}

function EvidenceRow({ utterance }: { utterance: UtteranceRow }) {
  const when = new Date(utterance.started_at);
  return (
    <View
      style={{
        paddingVertical: space.md,
        borderTopWidth: 1,
        borderTopColor: colors.border,
      }}
    >
      <Row style={{ justifyContent: 'space-between', gap: space.sm }}>
        <Text style={type.tiny}>
          {when.toLocaleDateString(undefined, {
            day: 'numeric',
            month: 'short',
          })}{' '}
          {when.toLocaleTimeString(undefined, {
            hour: '2-digit',
            minute: '2-digit',
          })}
          {utterance.place_name ? ` · ${utterance.place_name}` : ''}
        </Text>
        {utterance.act ? <ActBadge act={utterance.act} /> : null}
      </Row>
      {/* The sentence itself, quoted. This is the citation - the answer is
          the user's own words, not a paraphrase of them. */}
      <Quote text={utterance.text} />
      {utterance.item_owner && utterance.item_owner !== 'unknown' ? (
        <Text style={type.tiny}>owner: {utterance.item_owner}</Text>
      ) : null}
    </View>
  );
}

export default function RecallScreen() {
  const [text, setText] = useState('');
  const ask = useAskQuestion();
  const [result, setResult] = useState<QuestionAnswer | null>(null);

  const submit = useCallback(
    async (question: string) => {
      const trimmed = question.trim();
      if (!trimmed) return;
      setText(trimmed);
      try {
        setResult(await ask.mutateAsync(trimmed));
      } catch {
        setResult(null);
      }
    },
    [ask],
  );

  const clarify = useCallback(
    (clarification: Clarification, option: { label: string }) => {
      // Rewrite the question in the user's own words rather than carrying a
      // hidden resolution, so what was asked stays visible and re-askable.
      const rewritten = clarification.phrase
        ? text.replace(clarification.phrase, option.label)
        : `${text} (${option.label})`;
      void submit(rewritten);
    },
    [submit, text],
  );

  const pending = ask.isPending;

  return (
    <Screen>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          contentContainerStyle={{ padding: space.lg }}
          keyboardShouldPersistTaps="handled"
        >
          <Text style={type.title}>Recall</Text>
          <Text style={[type.dim, { marginTop: space.xs }]}>
            Ask about anything you recorded. You will see exactly what I
            searched for, and the sentences themselves.
          </Text>

          <Card style={{ marginTop: space.lg }}>
            <TextInput
              value={text}
              onChangeText={setText}
              placeholder="what did we decide yesterday"
              placeholderTextColor={colors.textFaint}
              multiline
              style={{
                color: colors.text,
                fontSize: 16,
                minHeight: 52,
                textAlignVertical: 'top',
              }}
              onSubmitEditing={() => void submit(text)}
            />
            <Row style={{ marginTop: space.md, gap: space.sm }}>
              <Button
                title={pending ? 'Looking...' : 'Ask'}
                onPress={() => void submit(text)}
                disabled={pending || !text.trim()}
              />
              {result ? (
                <Button
                  title="Clear"
                  variant="ghost"
                  onPress={() => {
                    setResult(null);
                    setText('');
                  }}
                  disabled={pending}
                />
              ) : null}
            </Row>
          </Card>

          {!result && !pending ? (
            <>
              <Text style={[type.dim, { marginTop: space.lg }]}>Try</Text>
              {EXAMPLES.map((example) => (
                <Button
                  key={example}
                  title={example}
                  variant="ghost"
                  onPress={() => void submit(example)}
                />
              ))}
            </>
          ) : null}

          {ask.error ? (
            <View style={{ marginTop: space.lg }}>
              <ErrorBanner message={(ask.error as Error).message} />
            </View>
          ) : null}

          {pending ? <Loading label="Searching what you recorded" /> : null}

          {result && !pending ? (
            <View style={{ marginTop: space.lg }}>
              <FilterCard resolved={result.resolved} />

              {result.resolved.clarifications.map((clarification) => (
                <ClarifyCard
                  key={clarification.kind + clarification.phrase}
                  clarification={clarification}
                  onChoose={(option) => clarify(clarification, option)}
                />
              ))}

              {/* The verdict is always shown, including on success: it is
                  the backend's own sentence about what it did, and hiding it
                  when results exist would make the empty case look like an
                  error state rather than the same honest report. */}
              <View
                style={{
                  padding: space.md,
                  borderRadius: radius.md,
                  backgroundColor: colors.surfaceAlt,
                  marginBottom: space.md,
                }}
              >
                <Text style={type.body}>{result.verdict}</Text>
              </View>

              {result.utterances.length > 0 ? (
                <Card>
                  <Text style={type.heading}>
                    {result.total} sentence{result.total === 1 ? '' : 's'}
                  </Text>
                  {result.utterances.map((utterance) => (
                    <EvidenceRow key={utterance.id} utterance={utterance} />
                  ))}
                  {result.total > result.utterances.length ? (
                    <Text style={[type.tiny, { marginTop: space.md }]}>
                      Showing {result.utterances.length} of {result.total}. Add
                      a day, a place or a person to narrow it.
                    </Text>
                  ) : null}
                </Card>
              ) : result.resolved.clarifications.length === 0 ? (
                <EmptyState
                  title="Nothing matched"
                  detail="That is an answer too — it means nothing you recorded fits this search, not that the search failed."
                />
              ) : null}
            </View>
          ) : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}
