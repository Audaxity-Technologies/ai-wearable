/**
 * Open loops: what you owe, and what you are owed.
 *
 * The screen no competitor can build, because it needs the speech act and
 * the owner to have been recorded at capture time rather than flattened into
 * a to-do list.
 *
 * Two wording rules are load-bearing here, and both are about not accusing
 * anyone:
 *
 * 1. **"Not attributed" is a bucket, not a default.** An unowned commitment
 *    is never shown under "You owe". Telling someone they promised something
 *    they never said is the costliest error this product can make.
 * 2. **"Gone quiet" describes our records, not a person.** It means we heard
 *    this once, a while ago, and nothing since. It does not mean they forgot,
 *    and nothing on this screen may imply that it does.
 */

import { ScrollView, Text, View } from 'react-native';

import { useOpenLoops, useVoiceStatus } from '../api/hooks';
import type { OpenLoop } from '../api/types';
import {
  ActBadge,
  Badge,
  Card,
  EmptyState,
  ErrorBanner,
  Loading,
  Quote,
  Row,
  Screen,
} from '../ui/components';
import { colors, radius, space, type } from '../ui/theme';

function age(days: number): string {
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 14) return `${days} days ago`;
  if (days < 60) return `${Math.round(days / 7)} weeks ago`;
  return `${Math.round(days / 30)} months ago`;
}

function LoopRow({ loop }: { loop: OpenLoop }) {
  return (
    <View
      style={{
        paddingVertical: space.md,
        borderTopWidth: 1,
        borderTopColor: colors.border,
      }}
    >
      <Row style={{ justifyContent: 'space-between', gap: space.sm }}>
        <Text style={[type.body, { flex: 1 }]}>{loop.title}</Text>
        <ActBadge act={loop.act} />
      </Row>

      <Row style={{ marginTop: space.xs, gap: space.sm, flexWrap: 'wrap' }}>
        {loop.counterparty ? (
          <Text style={type.dim}>{loop.counterparty}</Text>
        ) : null}
        <Text style={type.tiny}>since {age(loop.days_open)}</Text>
        {loop.is_overdue ? (
          <Badge label="overdue" color={colors.danger} />
        ) : null}
        {loop.gone_quiet ? (
          <Badge label="not mentioned since" color={colors.warning} />
        ) : null}
        {loop.raised_again ? (
          <Badge
            label={`raised ${loop.mention_sessions}×`}
            color={colors.accent}
          />
        ) : null}
      </Row>

      {loop.due_date ? (
        <Text style={[type.tiny, { marginTop: space.xs }]}>
          due {loop.due_date}
          {loop.due_resolution === 'ambiguous'
            ? ' (date was never pinned down)'
            : ''}
        </Text>
      ) : null}

      {/* Provenance. The user must always be able to see why we believe it. */}
      {loop.quote ? <Quote text={loop.quote} /> : null}
    </View>
  );
}

function Bucket({
  title,
  blurb,
  loops,
}: {
  title: string;
  blurb: string;
  loops: OpenLoop[];
}) {
  if (loops.length === 0) return null;
  return (
    <Card>
      <Row style={{ justifyContent: 'space-between' }}>
        <Text style={type.heading}>{title}</Text>
        <Badge label={String(loops.length)} />
      </Row>
      <Text style={[type.dim, { marginTop: space.xs }]}>{blurb}</Text>
      {loops.map((loop) => (
        <LoopRow key={loop.task_id} loop={loop} />
      ))}
    </Card>
  );
}

export default function LoopsScreen() {
  const { data, isLoading, error } = useOpenLoops();
  const voice = useVoiceStatus();

  if (isLoading) return <Loading label="Gathering open loops" />;
  if (error) {
    return (
      <Screen>
        <ErrorBanner message={(error as Error).message} />
      </Screen>
    );
  }
  if (!data || data.total === 0) {
    return (
      <Screen>
        <EmptyState
          title="No open loops"
          detail="Commitments picked out of your conversations appear here - what you took on, and what other people took on for you."
        />
      </Screen>
    );
  }

  const unattributed = data.unattributed.length;

  return (
    <Screen>
      <ScrollView contentContainerStyle={{ padding: space.lg }}>
        <Text style={type.title}>Open loops</Text>
        <Text style={[type.dim, { marginTop: space.xs }]}>
          {data.total} outstanding
          {data.overdue_count > 0 ? ` · ${data.overdue_count} overdue` : ''}
          {data.quiet_count > 0
            ? ` · ${data.quiet_count} not mentioned since`
            : ''}
        </Text>

        <View style={{ height: space.lg }} />

        <Bucket
          title="You owe"
          blurb="Things you said you would do."
          loops={data.owed_by_me}
        />
        <Bucket
          title="Owed to you"
          blurb="Things other people took on. Said once and never mentioned again is the kind people forget."
          loops={data.owed_to_me}
        />
        <Bucket
          title="Shared"
          blurb={'Said as "we" or "let\'s" - nobody took it on alone.'}
          loops={data.shared}
        />
        <Bucket
          title="Not attributed"
          blurb="Nobody clearly took these on, so they are not filed against you or anyone else. Guessing an owner would be worse than leaving it open."
          loops={data.unattributed}
        />

        {/* The honest explanation for why the largest bucket is usually this
            one, placed where the user will be wondering about it. */}
        {unattributed > 0 &&
        unattributed >= data.owed_by_me.length &&
        voice.data &&
        !voice.data.enrolled ? (
          <View
            style={{
              padding: space.md,
              borderRadius: radius.md,
              backgroundColor: colors.surfaceAlt,
              marginBottom: space.xl,
            }}
          >
            <Text style={type.dim}>
              Most of these are unattributed because nothing yet says who is
              who. Marking yourself under People fixes the split for
              everything already recorded; enrolling your voice in Settings
              is what will let &ldquo;I&apos;ll send it&rdquo; be filed as
              yours when no name is spoken at all.
            </Text>
          </View>
        ) : null}
      </ScrollView>
    </Screen>
  );
}
