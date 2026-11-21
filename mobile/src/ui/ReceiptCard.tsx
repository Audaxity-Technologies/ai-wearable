/**
 * The capture-integrity receipt, shown on a conversation.
 *
 * This is the only screen in the app whose job is to report our own
 * failures, and it has one hard rule: **it must not overstate coverage.**
 *
 * The coverage bar therefore renders three distinct things, because a single
 * progress bar would flatten them into a lie:
 *
 *   captured (minus silence)  what we hold and heard        - accent
 *   silence                   held, nothing said            - faint
 *   gap                       nothing covers this time      - warning
 *
 * A gap and a silence occupy the same space on a timeline and mean opposite
 * things. One is our failure; the other is a quiet room.
 *
 * Everything here is served by the backend, including the headline sentence
 * and `coverage`. Deliberately: two implementations of "did we capture this"
 * would eventually disagree, and the one the user reads would be the wrong
 * one.
 */

import { Text, View } from 'react-native';

import type { CaptureReceipt } from '../api/types';
import { Badge, Card, Quote, Row } from './components';
import { colors, radius, space, type } from './theme';

function pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function duration(seconds: number): string {
  const total = Math.round(seconds);
  if (seconds > 0 && total === 0) return 'under a second';
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  if (minutes < 60) return secs === 0 ? `${minutes}m` : `${minutes}m ${secs}s`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return mins === 0 ? `${hours}h` : `${hours}h ${mins}m`;
}

function CoverageBar({ receipt }: { receipt: CaptureReceipt }) {
  const span = Math.max(receipt.wall_clock_s, receipt.captured_s, 1);
  const heard = Math.max(0, receipt.captured_s - receipt.silent_s);
  const parts = [
    { key: 'heard', flex: heard / span, color: colors.accent },
    { key: 'silent', flex: receipt.silent_s / span, color: colors.textFaint },
    { key: 'gap', flex: receipt.gap_s / span, color: colors.warning },
  ].filter((part) => part.flex > 0);

  return (
    <View>
      <View
        style={{
          flexDirection: 'row',
          height: 8,
          borderRadius: radius.pill,
          overflow: 'hidden',
          backgroundColor: colors.surfaceAlt,
          marginTop: space.md,
        }}
      >
        {parts.map((part) => (
          <View
            key={part.key}
            style={{ flex: part.flex, backgroundColor: part.color }}
          />
        ))}
      </View>

      <Row style={{ marginTop: space.sm, gap: space.md, flexWrap: 'wrap' }}>
        <Legend color={colors.accent} label={`${duration(Math.max(0, receipt.captured_s - receipt.silent_s))} heard`} />
        {receipt.silent_s > 0 ? (
          <Legend
            color={colors.textFaint}
            label={`${duration(receipt.silent_s)} silence`}
          />
        ) : null}
        {receipt.gap_s > 0 ? (
          <Legend
            color={colors.warning}
            label={`${duration(receipt.gap_s)} not recorded`}
          />
        ) : null}
      </Row>
    </View>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <Row style={{ gap: space.xs }}>
      <View
        style={{
          width: 8,
          height: 8,
          borderRadius: radius.pill,
          backgroundColor: color,
        }}
      />
      <Text style={type.tiny}>{label}</Text>
    </Row>
  );
}

export function ReceiptCard({ receipt }: { receipt: CaptureReceipt }) {
  const needsAttention = !receipt.is_complete;

  return (
    <Card>
      <Row style={{ justifyContent: 'space-between' }}>
        <Text style={type.heading}>What was captured</Text>
        <Badge
          label={needsAttention ? 'incomplete' : 'complete'}
          color={needsAttention ? colors.warning : colors.success}
        />
      </Row>

      {/* The backend's sentence, not ours. It is the one place the honesty
          rules are implemented, and restating them here would let the two
          drift. */}
      <Text style={[type.body, { marginTop: space.sm }]}>
        {receipt.headline}
      </Text>

      {receipt.segments_total > 0 ? <CoverageBar receipt={receipt} /> : null}

      <Row style={{ marginTop: space.md, gap: space.lg, flexWrap: 'wrap' }}>
        <Stat label="recordings" value={String(receipt.segments_total)} />
        <Stat
          label="transcribed"
          value={String(receipt.segments_transcribed)}
        />
        {receipt.segments_failed > 0 ? (
          <Stat
            label="failed"
            value={String(receipt.segments_failed)}
            color={colors.danger}
          />
        ) : null}
        {receipt.segments_pending > 0 ? (
          <Stat
            label="pending"
            value={String(receipt.segments_pending)}
            color={colors.warning}
          />
        ) : null}
        <Stat label="coverage" value={pct(receipt.coverage)} />
      </Row>

      {receipt.gaps.length > 0 ? (
        <View style={{ marginTop: space.md }}>
          <Text style={type.dim}>
            {receipt.gaps.length === 1 ? 'Gap' : 'Gaps'} in the recording
          </Text>
          {receipt.gaps.map((gap) => (
            <Text
              key={`${gap.start_s}-${gap.end_s}`}
              style={[type.mono, { marginTop: space.xs }]}
            >
              {new Date(gap.at).toLocaleTimeString()} ·{' '}
              {duration(gap.duration_s)} missing
            </Text>
          ))}
          <Text style={[type.tiny, { marginTop: space.xs }]}>
            The microphone was muted, the app was closed, or the phone was
            locked. Anything said then is not recoverable.
          </Text>
        </View>
      ) : null}

      {receipt.suppressed.length > 0 ? (
        <View style={{ marginTop: space.md }}>
          <Text style={type.dim}>
            Heard, but not used ({receipt.suppressed.length})
          </Text>
          <Text style={[type.tiny, { marginTop: space.xs }]}>
            Judged to be a television, an advert or someone else&apos;s
            conversation, so no tasks were taken from it. If any of these were
            actually you, that is a bug worth reporting.
          </Text>
          {receipt.suppressed.slice(0, 4).map((span) => (
            <Quote key={span.quote} text={span.quote} />
          ))}
        </View>
      ) : null}

      {receipt.analysis_degraded ? (
        <View
          style={{
            marginTop: space.md,
            padding: space.sm,
            borderRadius: radius.sm,
            backgroundColor: colors.surfaceAlt,
          }}
        >
          <Text style={[type.tiny, { color: colors.warning }]}>
            The analysis did not complete cleanly
            {receipt.analysis_degraded_reason
              ? `: ${receipt.analysis_degraded_reason}`
              : '.'}{' '}
            Items may be missing even though the audio is intact.
          </Text>
        </View>
      ) : null}

      {!receipt.analysis_present && receipt.segments_total > 0 ? (
        <Text style={[type.tiny, { marginTop: space.md }]}>
          Nothing has been analysed yet, so the audio above is all we hold.
        </Text>
      ) : null}
    </Card>
  );
}

function Stat({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <View>
      <Text style={[type.heading, color ? { color } : null]}>{value}</Text>
      <Text style={type.tiny}>{label}</Text>
    </View>
  );
}
