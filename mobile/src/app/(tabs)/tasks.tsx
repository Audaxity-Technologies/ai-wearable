import { useState } from 'react';
import {
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { useCompleteTask, useTasks } from '@/api/hooks';
import type { TaskResponse } from '@/api/types';
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
} from '@/ui/components';
import { colors, radius, space, type } from '@/ui/theme';

type Filter = 'pending' | 'done';

export default function TasksScreen() {
  const [filter, setFilter] = useState<Filter>('pending');
  const tasks = useTasks(filter);
  const complete = useCompleteTask();
  const [expanded, setExpanded] = useState<string | null>(null);

  const data = tasks.data ?? [];
  const overdue = data.filter((t) => t.is_overdue);
  const unclear = data.filter((t) => t.needs_clarification);

  return (
    <Screen>
      <View style={styles.filters}>
        {(['pending', 'done'] as Filter[]).map((value) => (
          <Pressable
            key={value}
            onPress={() => setFilter(value)}
            style={[styles.filter, filter === value && styles.filterActive]}
          >
            <Text
              style={[
                type.dim,
                filter === value && { color: colors.text, fontWeight: '600' },
              ]}
            >
              {value === 'pending' ? 'Open' : 'Done'}
            </Text>
          </Pressable>
        ))}
      </View>

      {tasks.isLoading ? (
        <Loading />
      ) : (
        <ScrollView
          contentContainerStyle={styles.container}
          refreshControl={
            <RefreshControl
              refreshing={tasks.isFetching}
              onRefresh={() => void tasks.refetch()}
              tintColor={colors.textDim}
            />
          }
        >
          {tasks.isError ? <ErrorBanner message={String(tasks.error)} /> : null}

          {filter === 'pending' && (overdue.length > 0 || unclear.length > 0) ? (
            <Text style={[type.dim, { marginBottom: space.sm }]}>
              {overdue.length > 0 ? `${overdue.length} overdue` : ''}
              {overdue.length > 0 && unclear.length > 0 ? ' · ' : ''}
              {unclear.length > 0
                ? `${unclear.length} with an unclear deadline`
                : ''}
            </Text>
          ) : null}

          {data.length === 0 ? (
            <EmptyState
              title={filter === 'pending' ? 'Nothing open' : 'Nothing completed'}
              detail={
                filter === 'pending'
                  ? 'Commitments picked up from your conversations appear here. Suggestions never become tasks.'
                  : undefined
              }
            />
          ) : (
            data.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                expanded={expanded === task.id}
                onToggleExpand={() =>
                  setExpanded(expanded === task.id ? null : task.id)
                }
                onComplete={() =>
                  complete.mutate({
                    id: task.id,
                    done: task.status !== 'done',
                  })
                }
              />
            ))
          )}
        </ScrollView>
      )}
    </Screen>
  );
}

function TaskCard({
  task,
  expanded,
  onToggleExpand,
  onComplete,
}: {
  task: TaskResponse;
  expanded: boolean;
  onToggleExpand: () => void;
  onComplete: () => void;
}) {
  const done = task.status === 'done';
  return (
    <Card>
      <Row style={{ alignItems: 'flex-start' }}>
        <Pressable
          accessibilityRole="checkbox"
          accessibilityState={{ checked: done }}
          accessibilityLabel={done ? 'Mark as not done' : 'Mark as done'}
          onPress={onComplete}
          style={[styles.checkbox, done && styles.checkboxDone]}
        >
          {done ? <Text style={styles.check}>✓</Text> : null}
        </Pressable>

        <Pressable style={{ flex: 1 }} onPress={onToggleExpand}>
          <Text
            style={[
              type.body,
              { fontWeight: '600' },
              done && styles.struck,
            ]}
          >
            {task.title}
          </Text>

          <Row style={{ marginTop: space.sm, flexWrap: 'wrap' }}>
            <ActBadge act={task.act} />
            {task.owner && task.owner !== 'unknown' ? (
              <Badge label={task.owner} />
            ) : (
              <Badge label="owner unclear" color={colors.textFaint} />
            )}
            {task.is_overdue ? (
              <Badge label="overdue" color={colors.danger} />
            ) : null}
            {task.needs_clarification ? (
              <Badge label="date unclear" color={colors.warning} />
            ) : null}
          </Row>

          <Text style={[type.dim, { marginTop: space.sm }]}>
            {task.due_date
              ? `Due ${task.due_date}${task.due_time ? ` at ${task.due_time}` : ''}`
              : task.due_raw
                ? `Due “${task.due_raw}” — needs confirming`
                : 'No deadline'}
            {task.project ? ` · ${task.project}` : ''}
          </Text>

          {expanded ? (
            <View style={{ marginTop: space.sm }}>
              {task.source_quotes.map((quote, index) => (
                <Quote key={index} text={quote} />
              ))}
            </View>
          ) : task.source_quotes.length > 0 ? (
            <Text style={[type.tiny, { marginTop: space.sm }]}>
              Tap to see where this came from
            </Text>
          ) : null}
        </Pressable>
      </Row>
    </Card>
  );
}

const styles = StyleSheet.create({
  container: { padding: space.lg, paddingBottom: space.xxl },
  filters: {
    flexDirection: 'row',
    gap: space.sm,
    paddingHorizontal: space.lg,
    paddingTop: space.md,
  },
  filter: {
    paddingVertical: space.sm,
    paddingHorizontal: space.lg,
    borderRadius: radius.pill,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
  },
  filterActive: { backgroundColor: colors.surfaceAlt, borderColor: colors.accent },
  checkbox: {
    width: 24,
    height: 24,
    borderRadius: 6,
    borderWidth: 2,
    borderColor: colors.border,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 2,
  },
  checkboxDone: { backgroundColor: colors.success, borderColor: colors.success },
  check: { color: '#fff', fontSize: 14, fontWeight: '800' },
  struck: { textDecorationLine: 'line-through', color: colors.textDim },
});
