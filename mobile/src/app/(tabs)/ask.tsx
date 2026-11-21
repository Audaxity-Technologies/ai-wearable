/**
 * Ask.
 *
 * The answer always travels with its sources. When the backend has nothing
 * relevant it says so and returns `grounded: false` — the app shows that
 * plainly rather than dressing an empty answer up as a result.
 */

import { useRouter } from 'expo-router';
import { useState } from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { useAsk } from '@/api/hooks';
import type { AskResult } from '@/api/types';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  Quote,
  Row,
  Screen,
} from '@/ui/components';
import { colors, formatDate, radius, space, type } from '@/ui/theme';

const SUGGESTIONS = [
  'What did I promise this week?',
  'What is still pending?',
  'What did we decide about the prototype?',
  'Did I forget to follow up with anyone?',
];

export default function AskScreen() {
  const [question, setQuestion] = useState('');
  const [result, setResult] = useState<AskResult | null>(null);
  const ask = useAsk();
  const router = useRouter();

  async function submit(text: string) {
    const trimmed = text.trim();
    if (!trimmed) return;
    setQuestion(trimmed);
    try {
      setResult(await ask.mutateAsync(trimmed));
    } catch {
      setResult(null);
    }
  }

  return (
    <Screen>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView contentContainerStyle={styles.container}>
          {ask.isError ? <ErrorBanner message={String(ask.error)} /> : null}

          {!result && !ask.isPending ? (
            <>
              <EmptyState
                title="Ask about your conversations"
                detail="Answers come only from what you actually recorded, and always cite their sources."
              />
              <View style={{ gap: space.sm }}>
                {SUGGESTIONS.map((s) => (
                  <Pressable
                    key={s}
                    onPress={() => void submit(s)}
                    style={styles.suggestion}
                  >
                    <Text style={type.body}>{s}</Text>
                  </Pressable>
                ))}
              </View>
            </>
          ) : null}

          {ask.isPending ? (
            <Card>
              <Text style={type.dim}>Searching your conversations…</Text>
            </Card>
          ) : null}

          {result ? <Answer result={result} router={router} /> : null}
        </ScrollView>

        <View style={styles.composer}>
          <TextInput
            value={question}
            onChangeText={setQuestion}
            placeholder="Ask anything about your week…"
            placeholderTextColor={colors.textFaint}
            style={styles.input}
            multiline
            onSubmitEditing={() => void submit(question)}
            returnKeyType="send"
          />
          <Button
            title="Ask"
            onPress={() => void submit(question)}
            loading={ask.isPending}
            disabled={!question.trim()}
          />
        </View>
      </KeyboardAvoidingView>
    </Screen>
  );
}

function Answer({
  result,
  router,
}: {
  result: AskResult;
  router: ReturnType<typeof useRouter>;
}) {
  return (
    <>
      <Card>
        <Text style={type.dim}>{result.question}</Text>
        <Text style={[type.body, { marginTop: space.md, fontSize: 16 }]}>
          {result.answer}
        </Text>

        <Row style={{ marginTop: space.md, flexWrap: 'wrap' }}>
          {!result.grounded ? (
            <Badge label="nothing found" color={colors.warning} />
          ) : !result.confident ? (
            <Badge label="uncertain" color={colors.warning} />
          ) : (
            <Badge label="from your conversations" color={colors.success} />
          )}
          {result.degraded ? (
            <Badge label="partial" color={colors.danger} />
          ) : null}
        </Row>

        {!result.grounded && result.retrieved_count === 0 ? (
          <Text style={[type.tiny, { marginTop: space.sm }]}>
            Nothing in your recordings matched, so no answer was generated.
          </Text>
        ) : null}
      </Card>

      {result.sources.length > 0 ? (
        <>
          <Text style={[type.dim, { marginVertical: space.sm }]}>
            Based on {result.sources.length} source
            {result.sources.length === 1 ? '' : 's'}
          </Text>
          {result.sources.map((source) => (
            <Pressable
              key={`${source.kind}-${source.id}`}
              disabled={!source.session_id}
              onPress={() =>
                source.session_id &&
                router.push(`/conversation/${source.session_id}`)
              }
            >
              <Card>
                <Row style={{ justifyContent: 'space-between' }}>
                  <Badge label={source.kind} color={colors.accent} />
                  <Text style={type.tiny}>
                    {formatDate(source.occurred_at)}
                  </Text>
                </Row>
                <Text style={[type.body, { marginTop: space.xs }]}>
                  {source.title}
                </Text>
                {source.quotes.slice(0, 2).map((quote, index) => (
                  <Quote key={index} text={quote} />
                ))}
              </Card>
            </Pressable>
          ))}
        </>
      ) : null}
    </>
  );
}

const styles = StyleSheet.create({
  container: { padding: space.lg, paddingBottom: space.xl, gap: space.sm },
  suggestion: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: space.md,
  },
  composer: {
    flexDirection: 'row',
    gap: space.sm,
    padding: space.md,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.surface,
    alignItems: 'flex-end',
  },
  input: {
    flex: 1,
    color: colors.text,
    backgroundColor: colors.surfaceAlt,
    borderRadius: radius.md,
    paddingHorizontal: space.md,
    paddingVertical: space.md,
    maxHeight: 110,
    fontSize: 15,
  },
});
