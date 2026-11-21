/**
 * HTTP client for the Audaxity backend.
 *
 * The base URL is a runtime setting, not a build constant: during development
 * the phone has to reach a laptop by LAN IP, and that address changes. See
 * the Settings screen.
 */

import { File, UploadType } from 'expo-file-system';

import type {
  ActionResponse,
  ActionType,
  AnalysisResponse,
  AskResult,
  CaptureReceipt,
  HealthResponse,
  IngestAccepted,
  MemoryKind,
  MemoryResponse,
  OpenLoopLedger,
  PeopleList,
  PersonSummary,
  SessionSummary,
  TaskResponse,
  TaskStatus,
  TranscriptResponse,
  VoiceStatus,
} from './types';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** Network failure, as opposed to the server rejecting the request. */
export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'NetworkError';
  }
}

export interface ListParams {
  [key: string]: string | number | boolean | undefined | null;
}

function buildUrl(baseUrl: string, path: string, params?: ListParams): string {
  const root = baseUrl.replace(/\/+$/, '');
  const url = new URL(`${root}/v1${path}`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

/**
 * Multipart form fields for a coarse place, or nothing at all.
 *
 * Every field is omitted when absent rather than sent empty: the backend
 * treats a missing place as a first-class state, and a blank string would
 * make a segment look as though a location was attempted.
 */
function placeParameters(
  place:
    | {
        lat: number | null;
        lon: number | null;
        accuracyM: number | null;
        label: string | null;
        locality: string | null;
        region: string | null;
        country: string | null;
      }
    | null
    | undefined,
): Record<string, string> {
  if (!place || place.lat === null || place.lon === null) return {};
  const fields: Record<string, string> = {
    place_lat: String(place.lat),
    place_lon: String(place.lon),
  };
  if (place.accuracyM !== null) {
    fields.place_accuracy_m = String(place.accuracyM);
  }
  if (place.label) fields.place_label = place.label;
  if (place.locality) fields.place_locality = place.locality;
  if (place.region) fields.place_region = place.region;
  if (place.country) fields.place_country = place.country;
  return fields;
}

export class AudaxityApi {
  constructor(
    private readonly baseUrl: string,
    /** Bearer token. Null before sign-in: only /health and auth work then. */
    private readonly token: string | null = null,
    private readonly timeoutMs = 30000,
    /**
     * Called when the server rejects our token.
     *
     * Without this the app holds a token the server will never accept and
     * every screen fails with "Invalid credentials" and no way out - which
     * is what happens whenever the backend's signing secret changes, or the
     * account is served from a different data directory. A dead end the user
     * cannot escape is worse than being signed out.
     *
     * Not called for the auth routes themselves: a failed sign-in is a wrong
     * password, not a stale session.
     */
    private readonly onUnauthorized: (() => void) | null = null,
  ) {}

  private authHeaders(): Record<string, string> {
    return this.token ? { Authorization: `Bearer ${this.token}` } : {};
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    params?: ListParams,
  ): Promise<T> {
    const url = buildUrl(this.baseUrl, path, params);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    let response: Response;
    try {
      response = await fetch(url, {
        ...init,
        headers: { ...this.authHeaders(), ...(init.headers ?? {}) },
        signal: controller.signal,
      });
    } catch (error) {
      // Distinguish "cannot reach the server" from "server said no": the app
      // keeps recording offline in the first case.
      const detail = error instanceof Error ? error.message : String(error);
      throw new NetworkError(
        `Cannot reach ${this.baseUrl}. ${detail}`,
      );
    } finally {
      clearTimeout(timer);
    }

    if (!response.ok) {
      if (
        response.status === 401 &&
        this.token &&
        !path.startsWith('/auth/')
      ) {
        this.onUnauthorized?.();
      }
      let code: string | undefined;
      let message = `Request failed (${response.status})`;
      try {
        const body = await response.json();
        code = body?.code;
        message = body?.message ?? message;
      } catch {
        // Non-JSON error body; keep the status-based message.
      }
      throw new ApiError(message, response.status, code);
    }

    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  // --- auth ---
  register = (body: {
    email: string;
    password: string;
    display_name?: string;
    tz?: string;
  }) =>
    this.request<{ id: string; email: string; display_name: string; tz: string }>(
      '/auth/register',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    );

  login = (email: string, password: string) =>
    this.request<{
      access_token: string;
      user_id: string;
      email: string;
      display_name: string;
      tz: string;
    }>('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });

  me = () =>
    this.request<{
      id: string;
      email: string;
      display_name: string;
      tz: string;
      briefing_time: string;
      briefing_enabled: boolean;
    }>('/auth/me');

  updateMe = (patch: {
    tz?: string;
    display_name?: string;
    briefing_time?: string;
    briefing_enabled?: boolean;
  }) =>
    this.request<{ briefing_time: string; briefing_enabled: boolean }>(
      '/auth/me',
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      },
    );

  // --- integrations (phase 10) ---

  /** What the Settings screen renders. Carries no token material. */
  googleStatus = () =>
    this.request<{
      connected: boolean;
      email: string;
      scopes: string[];
      connected_at: string | null;
      needs_reconnect: boolean;
      detail: string;
      can_create_calendar_events: boolean;
      can_draft_email: boolean;
      token_can_send_email: boolean;
    }>('/integrations/google/status');

  /**
   * Returns a URL to open in the SYSTEM browser.
   *
   * Not a webview: Google rejects OAuth in embedded webviews with
   * `disallowed_useragent`, and rightly so - a webview lets the host app read
   * what the user types into Google's own login form.
   */
  googleConnect = () =>
    this.request<{ authorization_url: string; note: string }>(
      '/integrations/google/connect',
      { method: 'POST' },
    );

  googleDisconnect = () =>
    this.request<void>('/integrations/google', { method: 'DELETE' });

  // --- devices and notifications ---
  registerDevice = (body: {
    device_id: string;
    platform: 'ios' | 'android' | 'web';
    name?: string;
    push_token?: string | null;
  }) =>
    this.request<{ id: string; has_push_token: boolean }>('/devices', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

  /** Ask the backend to send this account a wake-up notification. */
  requestWake = (reason: string) =>
    this.request<{ sent: number; failed: number; detail: string }>('/wake', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason, source: 'app' }),
    });

  briefing = () =>
    this.request<{
      greeting: string;
      headline: string;
      body: string;
      is_empty: boolean;
      due_today: string[];
      overdue: string[];
      needs_clarification: string[];
      pending_approvals: number;
    }>('/briefing');

  // --- health ---
  health = () => this.request<HealthResponse>('/health');

  // --- sessions ---
  openSession = (tz: string, title?: string) =>
    this.request<SessionSummary>('/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tz,
        title,
        started_at: new Date().toISOString(),
      }),
    });

  closeSession = (sessionId: string) =>
    this.request<SessionSummary>(`/sessions/${sessionId}/close`, {
      method: 'POST',
    });

  listSessions = () =>
    this.request<{ sessions: SessionSummary[] }>('/sessions');

  getSession = (sessionId: string) =>
    this.request<SessionSummary>(`/sessions/${sessionId}`);

  getTranscript = (sessionId: string) =>
    this.request<TranscriptResponse>(`/sessions/${sessionId}/transcript`);

  getAnalysis = (sessionId: string) =>
    this.request<AnalysisResponse>(`/sessions/${sessionId}/analysis`);

  deleteSession = (sessionId: string) =>
    this.request<void>(`/sessions/${sessionId}`, { method: 'DELETE' });

  /**
   * Upload one audio segment.
   *
   * Uses expo-file-system's native multipart upload rather than `fetch` with
   * a FormData part. React Native 0.88 rejects the old `{uri, name, type}`
   * FormData shape with "Unsupported FormDataPart implementation", and
   * streaming from disk natively also avoids loading the audio into JS memory.
   *
   * Idempotent on `segmentId`: a retry returns `duplicate: true` and creates
   * nothing, which is what makes the offline outbox safe to flush repeatedly.
   */
  uploadSegment = async (args: {
    segmentId: string;
    sessionId: string;
    seq: number;
    startedAt: string;
    durationS: number;
    uri: string;
    mimeType?: string;
    /** Coarse place, already rounded on the device. Omitted when absent. */
    place?: {
      lat: number | null;
      lon: number | null;
      accuracyM: number | null;
      label: string | null;
      locality: string | null;
      region: string | null;
      country: string | null;
    } | null;
  }): Promise<IngestAccepted> => {
    const url = buildUrl(this.baseUrl, '/ingest/audio');

    let result: { status: number; body: string };
    try {
      const file = new File(args.uri);
      result = await file.upload(url, {
        httpMethod: 'POST',
        headers: this.authHeaders(),
        uploadType: UploadType.MULTIPART,
        fieldName: 'file',
        mimeType: args.mimeType ?? 'audio/m4a',
        parameters: {
          segment_id: args.segmentId,
          session_id: args.sessionId,
          seq: String(args.seq),
          started_at: args.startedAt,
          duration_s: String(args.durationS),
          source: 'phone',
          // Only send what we actually have. An empty string would be
          // parsed as a field the user never consented to supplying.
          ...placeParameters(args.place),
        },
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      throw new NetworkError(`Upload to ${this.baseUrl} failed. ${detail}`);
    }

    if (result.status < 200 || result.status >= 300) {
      let code: string | undefined;
      let message = `Upload failed (${result.status})`;
      try {
        const body = JSON.parse(result.body);
        code = body?.code;
        message = body?.message ?? message;
      } catch {
        // Non-JSON error body; keep the status-based message.
      }
      throw new ApiError(message, result.status, code);
    }

    return JSON.parse(result.body) as IngestAccepted;
  };

  // --- capture integrity ---
  /** Available at any status, including a failed session: the one call whose
   * job is to report our own failures while things are going wrong. */
  getReceipt = (sessionId: string) =>
    this.request<CaptureReceipt>(`/sessions/${sessionId}/receipt`);

  // --- people ---
  listPeople = () => this.request<PeopleList>('/people');

  addAlias = (personId: string, alias: string) =>
    this.request<PersonSummary>(`/people/${personId}/aliases`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ alias }),
    });

  updatePerson = (
    personId: string,
    patch: { name?: string; is_self?: boolean },
  ) =>
    this.request<PersonSummary>(`/people/${personId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });

  mergePeople = (personId: string, otherId: string) =>
    this.request<PersonSummary>(`/people/${personId}/merge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ other_id: otherId }),
    });

  deletePerson = (personId: string) =>
    this.request<void>(`/people/${personId}`, { method: 'DELETE' });

  // --- open loops ---
  openLoops = () => this.request<OpenLoopLedger>('/tasks/open-loops');

  // --- owner voice enrolment ---
  voiceStatus = () => this.request<VoiceStatus>('/voice/status');

  voiceForget = () => this.request<void>('/voice', { method: 'DELETE' });

  /**
   * Enrol the account holder's voice from several clips, in ONE request.
   *
   * Deliberately not an accumulating upload: a half-finished enrolment
   * sitting on the server is voice audio kept for no operational reason.
   * `expo-file-system`'s uploader handles one file per call, so this posts a
   * hand-built multipart body instead.
   */
  voiceEnrol = async (
    clips: { uri: string; durationS: number; mimeType?: string }[],
  ): Promise<VoiceStatus> => {
    const form = new FormData();
    for (const [index, clip] of clips.entries()) {
      form.append('files', {
        uri: clip.uri,
        name: `clip${index}.m4a`,
        type: clip.mimeType ?? 'audio/m4a',
        // React Native's FormData takes this shape rather than a Blob.
      } as unknown as Blob);
    }
    form.append(
      'durations',
      clips.map((clip) => String(clip.durationS)).join(','),
    );

    // No Content-Type header: it must carry the generated multipart
    // boundary, which only fetch knows.
    return this.request<VoiceStatus>('/voice/enrol', {
      method: 'POST',
      body: form,
    });
  };

  // --- memory ---
  listMemories = (kind?: MemoryKind, conflictsOnly = false) =>
    this.request<{ memories: MemoryResponse[]; total: number }>(
      '/memory',
      {},
      { kind, conflicts_only: conflictsOnly || undefined },
    );

  searchMemories = (q: string) =>
    this.request<{ results: MemoryResponse[]; total: number }>(
      '/memory/search',
      {},
      { q },
    );

  deleteMemory = (id: string) =>
    this.request<void>(`/memory/${id}`, { method: 'DELETE' });

  // --- tasks ---
  listTasks = (status?: TaskStatus, overdue = false) =>
    this.request<{ tasks: TaskResponse[]; total: number }>('/tasks', {}, {
      status,
      overdue: overdue || undefined,
    });

  updateTask = (id: string, patch: Record<string, unknown>) =>
    this.request<TaskResponse>(`/tasks/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });

  // --- actions ---
  listPendingActions = () =>
    this.request<{
      actions: ActionResponse[];
      total: number;
      needs_clarification_count: number;
    }>('/actions/pending');

  listActions = () =>
    this.request<{ actions: ActionResponse[]; total: number }>(
      '/actions',
    );

  approveAction = (id: string) =>
    this.request<ActionResponse>(`/actions/${id}/approve`, { method: 'POST' });

  rejectAction = (id: string, reason?: string) =>
    this.request<ActionResponse>(`/actions/${id}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });

  editAction = (
    id: string,
    patch: { title?: string; date?: string; time?: string; type?: ActionType },
  ) =>
    this.request<ActionResponse>(`/actions/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });

  // --- ask ---
  ask = (question: string, tz: string) =>
    this.request<AskResult>('/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, tz, limit: 8 }),
    });
}
