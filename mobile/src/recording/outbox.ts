/**
 * Offline upload queue.
 *
 * Audio is written to disk and queued locally the moment a segment finishes,
 * then uploaded opportunistically. Recording must never depend on having a
 * network: the whole premise is that you wear this through a day, and days
 * include lifts, trains and dead zones.
 *
 * Safe to flush repeatedly because ingest is idempotent on `segment_id` —
 * a duplicate upload returns `duplicate: true` and creates nothing.
 */

import * as SQLite from 'expo-sqlite';

import type { CapturedPlace } from './place';

export type OutboxStatus = 'pending' | 'uploaded' | 'failed';

export interface OutboxSegment {
  id: string;
  session_id: string;
  seq: number;
  uri: string;
  started_at: string;
  duration_s: number;
  status: OutboxStatus;
  attempts: number;
  last_error: string | null;
  /** Coarse place, already rounded on the device. Null is normal. */
  place_lat: number | null;
  place_lon: number | null;
  place_accuracy_m: number | null;
  place_label: string | null;
  place_locality: string | null;
  place_region: string | null;
  place_country: string | null;
}

let dbPromise: Promise<SQLite.SQLiteDatabase> | null = null;

/**
 * Schema version. Bumped whenever the table changes.
 *
 * `CREATE TABLE IF NOT EXISTS` is a no-op on a device that already has the
 * table, so new columns need an explicit `ALTER TABLE`. Without this, place
 * capture would work on a fresh install and fail on every existing one -
 * which is the worst possible shape of bug, because it only appears for
 * people who have been using the app.
 */
const SCHEMA_VERSION = 2;

const PLACE_COLUMNS: [string, string][] = [
  ['place_lat', 'REAL'],
  ['place_lon', 'REAL'],
  ['place_accuracy_m', 'REAL'],
  ['place_label', 'TEXT'],
  ['place_locality', 'TEXT'],
  ['place_region', 'TEXT'],
  ['place_country', 'TEXT'],
];

async function migrate(database: SQLite.SQLiteDatabase): Promise<void> {
  const row = await database.getFirstAsync<{ user_version: number }>(
    'PRAGMA user_version',
  );
  const current = row?.user_version ?? 0;
  if (current >= SCHEMA_VERSION) return;

  if (current < 2) {
    for (const [name, sqlType] of PLACE_COLUMNS) {
      try {
        await database.execAsync(
          `ALTER TABLE segments ADD COLUMN ${name} ${sqlType}`,
        );
      } catch {
        // Already present. SQLite has no "ADD COLUMN IF NOT EXISTS", and a
        // half-applied migration from an interrupted launch must not brick
        // the queue - so each column is attempted independently.
      }
    }
  }

  await database.execAsync(`PRAGMA user_version = ${SCHEMA_VERSION}`);
}

async function db(): Promise<SQLite.SQLiteDatabase> {
  if (!dbPromise) {
    dbPromise = SQLite.openDatabaseAsync('audaxity-outbox.db').then(
      async (database) => {
        await database.execAsync(`
          PRAGMA journal_mode = WAL;
          CREATE TABLE IF NOT EXISTS segments (
            id          TEXT PRIMARY KEY NOT NULL,
            session_id  TEXT NOT NULL,
            seq         INTEGER NOT NULL,
            uri         TEXT NOT NULL,
            started_at  TEXT NOT NULL,
            duration_s  REAL NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            attempts    INTEGER NOT NULL DEFAULT 0,
            last_error  TEXT
          );
          CREATE INDEX IF NOT EXISTS idx_segments_status
            ON segments (status, seq);
        `);
        await migrate(database);
        return database;
      },
    );
  }
  return dbPromise;
}

export async function enqueue(segment: {
  id: string;
  sessionId: string;
  seq: number;
  uri: string;
  startedAt: string;
  durationS: number;
  place?: CapturedPlace | null;
}): Promise<void> {
  const database = await db();
  const place = segment.place ?? null;
  // INSERT OR IGNORE: re-queuing a segment id is a no-op, matching the
  // server's own idempotency.
  await database.runAsync(
    `INSERT OR IGNORE INTO segments
       (id, session_id, seq, uri, started_at, duration_s, status, attempts,
        place_lat, place_lon, place_accuracy_m,
        place_label, place_locality, place_region, place_country)
     VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?, ?, ?)`,
    segment.id,
    segment.sessionId,
    segment.seq,
    segment.uri,
    segment.startedAt,
    segment.durationS,
    place?.lat ?? null,
    place?.lon ?? null,
    place?.accuracyM ?? null,
    place?.label ?? null,
    place?.locality ?? null,
    place?.region ?? null,
    place?.country ?? null,
  );
}

export async function pending(limit = 20): Promise<OutboxSegment[]> {
  const database = await db();
  return database.getAllAsync<OutboxSegment>(
    `SELECT * FROM segments
      WHERE status IN ('pending', 'failed')
      ORDER BY seq ASC, started_at ASC
      LIMIT ?`,
    limit,
  );
}

export async function pendingCount(): Promise<number> {
  const database = await db();
  const row = await database.getFirstAsync<{ count: number }>(
    `SELECT COUNT(*) AS count FROM segments WHERE status IN ('pending','failed')`,
  );
  return row?.count ?? 0;
}

export async function markUploaded(id: string): Promise<void> {
  const database = await db();
  await database.runAsync(
    `UPDATE segments SET status = 'uploaded', last_error = NULL WHERE id = ?`,
    id,
  );
}

export async function markFailed(id: string, error: string): Promise<void> {
  const database = await db();
  await database.runAsync(
    `UPDATE segments
        SET status = 'failed', attempts = attempts + 1, last_error = ?
      WHERE id = ?`,
    error.slice(0, 500),
    id,
  );
}

/** Segment ids still queued for a session, used to decide when to close it. */
export async function pendingForSession(sessionId: string): Promise<number> {
  const database = await db();
  const row = await database.getFirstAsync<{ count: number }>(
    `SELECT COUNT(*) AS count FROM segments
      WHERE session_id = ? AND status IN ('pending','failed')`,
    sessionId,
  );
  return row?.count ?? 0;
}

/** Drop uploaded rows older than a day to keep the local table small. */
export async function pruneUploaded(): Promise<void> {
  const database = await db();
  await database.runAsync(
    `DELETE FROM segments
      WHERE status = 'uploaded'
        AND started_at < datetime('now', '-1 day')`,
  );
}
