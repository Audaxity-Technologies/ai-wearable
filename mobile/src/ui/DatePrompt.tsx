/**
 * Supply a missing date.
 *
 * The backend deliberately refuses to guess an ambiguous deadline ("next
 * week", "6 pm" with no day), which is right — but it left those cards as
 * dead ends in the app: flagged, and unresolvable. This is the way out.
 *
 * Offers the obvious shortcuts first, because most unclear deadlines are
 * within the next few days, and falls back to typing a date.
 */

import { useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { Button, Row } from './components';
import { colors, radius, space, type } from './theme';

function iso(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function addDays(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return iso(date);
}

function weekdayLabel(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toLocaleDateString(undefined, { weekday: 'short' });
}

export function DatePrompt({
  raw,
  onCancel,
  onConfirm,
  busy,
}: {
  /** The phrase that could not be resolved, e.g. "next week". */
  raw?: string | null;
  onCancel: () => void;
  onConfirm: (date: string, time?: string) => void;
  busy?: boolean;
}) {
  const [date, setDate] = useState(addDays(1));
  const [time, setTime] = useState('');

  const shortcuts = [
    { label: 'Today', value: addDays(0) },
    { label: 'Tomorrow', value: addDays(1) },
    { label: weekdayLabel(2), value: addDays(2) },
    { label: weekdayLabel(3), value: addDays(3) },
    { label: 'Next week', value: addDays(7) },
  ];

  const valid = /^\d{4}-\d{2}-\d{2}$/.test(date);
  const timeValid = time === '' || /^([01]?\d|2[0-3]):[0-5]\d$/.test(time);

  return (
    <View style={styles.sheet}>
      <Text style={type.heading}>When is this due?</Text>
      <Text style={[type.dim, { marginTop: space.xs }]}>
        {raw
          ? `“${raw}” could mean more than one day, so it was left unset rather than guessed.`
          : 'No date was mentioned, so none was invented.'}
      </Text>

      <Row style={{ marginTop: space.md, flexWrap: 'wrap' }}>
        {shortcuts.map((shortcut) => (
          <Pressable
            key={shortcut.label}
            onPress={() => setDate(shortcut.value)}
            style={[
              styles.chip,
              date === shortcut.value && styles.chipActive,
            ]}
          >
            <Text
              style={[
                type.dim,
                date === shortcut.value && {
                  color: colors.text,
                  fontWeight: '600',
                },
              ]}
            >
              {shortcut.label}
            </Text>
          </Pressable>
        ))}
      </Row>

      <Row style={{ marginTop: space.md, gap: space.sm }}>
        <TextInput
          value={date}
          onChangeText={setDate}
          placeholder="YYYY-MM-DD"
          placeholderTextColor={colors.textFaint}
          autoCapitalize="none"
          style={[styles.input, { flex: 2 }]}
        />
        <TextInput
          value={time}
          onChangeText={setTime}
          placeholder="HH:MM"
          placeholderTextColor={colors.textFaint}
          autoCapitalize="none"
          style={[styles.input, { flex: 1 }]}
        />
      </Row>
      {!valid || !timeValid ? (
        <Text style={[type.tiny, { color: colors.warning, marginTop: space.xs }]}>
          {valid ? 'Time must look like 17:30.' : 'Date must look like 2026-11-21.'}
        </Text>
      ) : null}

      <Row style={{ marginTop: space.lg, gap: space.sm }}>
        <Button
          title="Set date"
          onPress={() => onConfirm(date, time || undefined)}
          disabled={!valid || !timeValid}
          loading={busy}
          style={{ flex: 1 }}
        />
        <Button
          title="Cancel"
          variant="secondary"
          onPress={onCancel}
          style={{ flex: 1 }}
        />
      </Row>
    </View>
  );
}

const styles = StyleSheet.create({
  sheet: {
    marginTop: space.md,
    padding: space.md,
    backgroundColor: colors.surfaceAlt,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
  },
  chip: {
    paddingVertical: space.xs,
    paddingHorizontal: space.md,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  chipActive: { borderColor: colors.accent, backgroundColor: colors.bg },
  input: {
    color: colors.text,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
    fontSize: 15,
  },
});
