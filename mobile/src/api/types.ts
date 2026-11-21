/**
 * Types mirroring the backend's v1 DTOs.
 *
 * Field names are snake_case because that is what the API returns; converting
 * them would add a translation layer whose only job is to drift.
 */

export type SessionStatus =
  | 'open'
  | 'queued'
  | 'transcribing'
  | 'transcribed'
  | 'analyzing'
  | 'analyzed'
  | 'analyzed_degraded'
  | 'memory_written'
  | 'actions_proposed'
  | 'ready'
  | 'failed';

export type SpeechAct =
  | 'commitment'
  | 'assignment'
  | 'suggestion'
  | 'decision'
  | 'requirement'
  | 'question'
  | 'discussion';

export type DateResolution = 'confident' | 'ambiguous' | 'none';

export interface SessionSummary {
  id: string;
  user_id: string;
  status: SessionStatus;
  tz: string;
  title: string | null;
  started_at: string;
  closed_at: string | null;
  duration_s: number;
  segment_count: number;
  language: string | null;
  updated_at: string;
  failed_stage?: string | null;
  error?: string | null;
  /** Where it happened, busiest first. Empty when capture is off. */
  places?: SessionPlace[];
}

export interface TranscriptSpan {
  start_s: number;
  end_s: number;
  text: string;
}

export interface SegmentTranscript {
  segment_id: string;
  seq: number;
  started_at: string;
  duration_s: number;
  status: string;
  text: string | null;
  language: string | null;
  spans: TranscriptSpan[];
  error?: string | null;
  place?: CoarsePlace | null;
}

export interface TranscriptResponse {
  session_id: string;
  status: SessionStatus;
  language: string | null;
  transcript: string | null;
  segments: SegmentTranscript[];
}

export interface DueDate {
  raw: string | null;
  date: string | null;
  time: string | null;
  resolution: DateResolution;
}

export interface AnalysisItem {
  title: string;
  act: SpeechAct;
  owner: string;
  source_quote: string;
  due: DueDate;
  project: string | null;
  confidence: number;
  supersedes: string | null;
}

export interface AnalysisResponse {
  session_id: string;
  title: string;
  summary: string;
  participants: { name: string; speaking: boolean }[];
  projects: string[];
  topics: string[];
  items: AnalysisItem[];
  actionable_count: number;
  needs_clarification_count: number;
  dropped: { title: string; reason: string; source_quote: string | null }[];
  degraded: boolean;
  degraded_reason: string | null;
  provider: string;
  model: string;
  repair_attempted: boolean;
}

export type MemoryKind = 'person' | 'project' | 'fact' | 'preference';

export interface MemoryResponse {
  id: string;
  kind: MemoryKind;
  name: string;
  content: string;
  attributes: Record<string, string>;
  confidence: number;
  mention_count: number;
  first_seen: string | null;
  last_seen: string | null;
  source_session_ids: string[];
  source_quotes: string[];
  conflicts_with: string[];
  has_conflict: boolean;
  is_cross_conversation: boolean;
  score?: number | null;
  matched_terms?: string[] | null;
}

export type TaskStatus = 'pending' | 'done' | 'cancelled';

export interface TaskResponse {
  id: string;
  title: string;
  owner: string;
  act: SpeechAct;
  status: TaskStatus;
  project: string | null;
  tz: string;
  due_date: string | null;
  due_time: string | null;
  due_raw: string | null;
  due_resolution: DateResolution;
  needs_clarification: boolean;
  is_overdue: boolean;
  source_session_ids: string[];
  source_quotes: string[];
  edited: boolean;
  completed_at: string | null;
}

export type ActionType = 'reminder' | 'calendar_event' | 'task' | 'email_draft';
export type ActionStatus =
  | 'proposed'
  | 'approved'
  | 'executing'
  | 'executed'
  | 'rejected'
  | 'failed';

export interface ActionResponse {
  id: string;
  type: ActionType;
  title: string;
  status: ActionStatus;
  reason: string;
  payload: Record<string, string>;
  project: string | null;
  needs_clarification: boolean;
  source_session_id: string;
  source_quote: string;
  task_id: string | null;
  edited: boolean;
  decided_at: string | null;
  executed_at: string | null;
  rejection_reason: string | null;
  execution_result: string | null;
  history: { at: string; event: string; detail: string | null }[];
}

export interface AskSource {
  label: number;
  kind: 'conversation' | 'memory' | 'task';
  id: string;
  session_id: string | null;
  title: string;
  occurred_at: string | null;
  quotes: string[];
}

export interface AskResult {
  question: string;
  answer: string;
  grounded: boolean;
  confident: boolean;
  sources: AskSource[];
  retrieved_count: number;
  dropped_citations: number[];
  provider: string;
  model: string;
  degraded: boolean;
  degraded_reason: string | null;
}

export interface HealthResponse {
  status: string;
  app_env: string;
  transcription_provider: string;
  transcription_ready: boolean;
  transcription_detail: string;
  llm_provider: string;
  llm_ready: boolean;
  llm_detail: string;
  pipeline_pending_jobs: number;
}

export interface IngestAccepted {
  segment_id: string;
  session_id: string;
  status: string;
  duplicate: boolean;
}

// --- capture integrity (phase 11) ---

export interface TimelineGap {
  start_s: number;
  end_s: number;
  at: string;
  duration_s: number;
}

export interface SuppressedSpan {
  quote: string;
  reason: string;
}

/**
 * What was captured for a session, and what was not.
 *
 * The three numbers that matter are deliberately separate, because a
 * progress bar would flatten them: `captured_s` is audio we hold, `gap_s` is
 * time nothing covers (our failure), and `silent_s` is audio with no speech
 * in it (a quiet room). A gap and a silence look identical on a timeline and
 * mean opposite things.
 */
export interface CaptureReceipt {
  session_id: string;
  status: SessionStatus;
  segments_total: number;
  segments_transcribed: number;
  segments_failed: number;
  segments_pending: number;
  wall_clock_s: number;
  captured_s: number;
  silent_s: number;
  gaps: TimelineGap[];
  suppressed: SuppressedSpan[];
  dropped_other: number;
  analysis_present: boolean;
  analysis_degraded: boolean;
  analysis_degraded_reason: string | null;
  /** Derived server-side so the client cannot drift from it. */
  gap_s: number;
  coverage: number;
  is_complete: boolean;
  headline: string;
}

// --- open loops (phase 11) ---

export type LoopSide = 'mine' | 'theirs' | 'shared' | 'unattributed';

export interface OpenLoop {
  task_id: string;
  title: string;
  side: LoopSide;
  act: SpeechAct;
  counterparty: string | null;
  project: string | null;
  opened_at: string;
  last_mentioned_at: string;
  mention_sessions: number;
  quote: string | null;
  due_date: string | null;
  due_resolution: DateResolution;
  is_overdue: boolean;
  days_open: number;
  raised_again: boolean;
  /** Old and never mentioned again. A fact about our records, not about the
   * person - the wording shown to the user must not imply they forgot. */
  gone_quiet: boolean;
}

export interface OpenLoopLedger {
  owed_by_me: OpenLoop[];
  owed_to_me: OpenLoop[];
  shared: OpenLoop[];
  /** No clear owner. Surfaced, never silently assigned to the user. */
  unattributed: OpenLoop[];
  total: number;
  quiet_count: number;
  overdue_count: number;
}

// --- owner voice enrolment (phase 11) ---

export type SpeakerVerdict = 'owner' | 'other' | 'uncertain' | 'not_enrolled';

export interface VoiceStatus {
  enrolled: boolean;
  clip_count: number;
  total_seconds: number;
  enrolled_at: string | null;
  model_id: string | null;
  provider_ready: boolean;
  /** False while no real speaker model is wired up. */
  verification_trustworthy: boolean;
  detail: string;
  others_enrolled: number;
}

export interface SpeakerMatch {
  verdict: SpeakerVerdict;
  similarity: number | null;
  model_id: string | null;
  detail: string;
  trustworthy: boolean;
}

// --- coarse place (memory phase A) ---

/**
 * Roughly where a recording happened. Never precisely: coordinates are
 * rounded to ~110m on the device and again on the server.
 *
 * Show `label`/`locality`, never the coordinates - they are an index, not an
 * answer. Nobody asks what they said at 28.524, 77.186.
 */
export interface CoarsePlace {
  lat: number | null;
  lon: number | null;
  label: string | null;
  locality: string | null;
  region: string | null;
  country: string | null;
  source: string;
  reported_accuracy_m: number | null;
  captured_at: string | null;
}

export interface SessionPlace {
  place: CoarsePlace;
  seconds: number;
  segment_count: number;
}

/** The name to show for a place, or a neutral fallback. Never coordinates. */
export function placeName(place: CoarsePlace): string {
  return (
    place.label ??
    place.locality ??
    place.region ??
    place.country ??
    'an unnamed place'
  );
}

// --- people (memory phase B) ---

export interface PersonSummary {
  id: string;
  canonical_name: string;
  aliases: string[];
  /** The account holder. Set by the user, never inferred from a name. */
  is_self: boolean;
  mention_count: number;
  session_count: number;
  first_seen: string | null;
  last_seen: string | null;
  has_voiceprint: boolean;
}

/**
 * Two people who might be one.
 *
 * Offered, never applied: a wrong merge silently reassigns someone's
 * commitments and is close to impossible to notice afterwards, while an
 * unmerged duplicate is visible and one tap to fix.
 */
export interface MergeSuggestion {
  primary_id: string;
  primary_name: string;
  other_id: string;
  other_name: string;
  similarity: number;
  /** Appearing in one conversation is evidence AGAINST a merge. */
  shared_sessions: number;
  reason: string;
}

export interface PeopleList {
  people: PersonSummary[];
  suggestions: MergeSuggestion[];
}
