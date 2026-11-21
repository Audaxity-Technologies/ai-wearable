/**
 * React Query bindings.
 *
 * Polling exists because the pipeline is asynchronous: a session moves
 * through transcribing -> analyzing -> ready over tens of seconds, and the
 * user is watching. Polling stops once nothing is in flight.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useMemo } from 'react';

// Aliased: this module already exports a useSession() for conversations.
import { useSession as useAuthSession } from '../store/session';
import { useSettings } from '../store/settings';
import { AudaxityApi } from './client';
import type { MemoryKind, SessionStatus, TaskStatus } from './types';

/** Terminal states: nothing more will change on its own. */
const SETTLED: SessionStatus[] = ['ready', 'failed'];

export function useApi(): AudaxityApi | null {
  const baseUrl = useSettings((s) => s.baseUrl);
  const token = useAuthSession((s) => s.token);
  const signOut = useAuthSession((s) => s.signOut);
  return useMemo(
    () =>
      baseUrl
        ? new AudaxityApi(baseUrl, token, undefined, () => {
            // A token the server rejects is not a token. Clearing it sends
            // the app to the sign-in screen instead of leaving every button
            // failing with "Invalid credentials".
            void signOut();
          })
        : null,
    [baseUrl, token, signOut],
  );
}

export function useHealth() {
  const api = useApi();
  return useQuery({
    queryKey: ['health', api ? 'on' : 'off'],
    queryFn: () => api!.health(),
    enabled: !!api,
    retry: false,
    refetchInterval: 30000,
  });
}

/**
 * The Google connection, for Settings.
 *
 * No polling: it changes only when the user connects or disconnects, both of
 * which invalidate it explicitly. It does refetch on focus, because the
 * consent step finishes in a *different app* (the browser), so coming back to
 * Audaxity is exactly the moment the answer may have changed.
 */
export function useGoogleStatus() {
  const api = useApi();
  return useQuery({
    queryKey: ['google-status', api ? 'on' : 'off'],
    queryFn: () => api!.googleStatus(),
    enabled: !!api,
    retry: false,
    refetchOnWindowFocus: true,
    staleTime: 10000,
  });
}

export function useSessions() {
  const api = useApi();
  return useQuery({
    queryKey: ['sessions'],
    queryFn: async () => (await api!.listSessions()).sessions,
    enabled: !!api,
    // Keep polling only while something is still being processed.
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      return data.some((s) => !SETTLED.includes(s.status)) ? 4000 : false;
    },
  });
}

export function useSession(sessionId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ['session', sessionId],
    queryFn: () => api!.getSession(sessionId),
    enabled: !!api && !!sessionId,
    refetchInterval: (query) =>
      query.state.data && SETTLED.includes(query.state.data.status)
        ? false
        : 3000,
  });
}

export function useTranscript(sessionId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ['transcript', sessionId],
    queryFn: () => api!.getTranscript(sessionId),
    enabled: !!api && !!sessionId,
  });
}

export function useAnalysis(sessionId: string, status?: SessionStatus) {
  const api = useApi();
  const ready = status === 'ready' || status === 'analyzed';
  return useQuery({
    queryKey: ['analysis', sessionId],
    queryFn: () => api!.getAnalysis(sessionId),
    // 404 until the pipeline reaches analysis, so don't ask before then.
    enabled: !!api && !!sessionId && ready,
    retry: false,
  });
}

/**
 * The capture receipt for one session.
 *
 * Unlike `useAnalysis`, this is enabled at EVERY status, including `failed`.
 * A receipt you can only see once the pipeline succeeded cannot answer the
 * question it exists for.
 */
export function useReceipt(sessionId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ['receipt', sessionId],
    queryFn: () => api!.getReceipt(sessionId),
    enabled: !!api && !!sessionId,
  });
}

export function useOpenLoops() {
  const api = useApi();
  return useQuery({
    queryKey: ['open-loops'],
    queryFn: () => api!.openLoops(),
    enabled: !!api,
  });
}

export function usePeople() {
  const api = useApi();
  return useQuery({
    queryKey: ['people'],
    queryFn: () => api!.listPeople(),
    enabled: !!api,
  });
}

/** Any edit to identity can re-bucket the ledger, so both refetch. */
function invalidatePeople(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ['people'] });
  void queryClient.invalidateQueries({ queryKey: ['open-loops'] });
  void queryClient.invalidateQueries({ queryKey: ['tasks'] });
}

export function useUpdatePerson() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: {
      personId: string;
      patch: { name?: string; is_self?: boolean };
    }) => api!.updatePerson(args.personId, args.patch),
    onSuccess: () => invalidatePeople(queryClient),
  });
}

export function useMergePeople() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: { personId: string; otherId: string }) =>
      api!.mergePeople(args.personId, args.otherId),
    onSuccess: () => invalidatePeople(queryClient),
  });
}

export function useAddAlias() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: { personId: string; alias: string }) =>
      api!.addAlias(args.personId, args.alias),
    onSuccess: () => invalidatePeople(queryClient),
  });
}

export function useVoiceStatus() {
  const api = useApi();
  return useQuery({
    queryKey: ['voice-status', api ? 'on' : 'off'],
    queryFn: () => api!.voiceStatus(),
    enabled: !!api,
  });
}

export function useVoiceEnrol() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (clips: { uri: string; durationS: number }[]) =>
      api!.voiceEnrol(clips),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['voice-status'] });
      // Attribution changes once a voiceprint exists, so the ledger's
      // buckets can shift. Refetch rather than show a stale split.
      void queryClient.invalidateQueries({ queryKey: ['open-loops'] });
    },
  });
}

export function useVoiceForget() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api!.voiceForget(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['voice-status'] });
      void queryClient.invalidateQueries({ queryKey: ['open-loops'] });
    },
  });
}

export function usePendingActions() {
  const api = useApi();
  return useQuery({
    queryKey: ['actions', 'pending'],
    queryFn: () => api!.listPendingActions(),
    enabled: !!api,
    refetchInterval: 10000,
  });
}

export function useTasks(status?: TaskStatus) {
  const api = useApi();
  return useQuery({
    queryKey: ['tasks', status ?? 'all'],
    queryFn: async () => (await api!.listTasks(status)).tasks,
    enabled: !!api,
  });
}

export function useMemories(kind?: MemoryKind, conflictsOnly = false) {
  const api = useApi();
  return useQuery({
    queryKey: ['memory', kind ?? 'all', conflictsOnly],
    queryFn: async () =>
      (await api!.listMemories(kind, conflictsOnly)).memories,
    enabled: !!api,
  });
}

export function useApproveAction() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api!.approveAction(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['actions'] });
      void qc.invalidateQueries({ queryKey: ['tasks'] });
    },
  });
}

export function useRejectAction() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      api!.rejectAction(id, reason),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['actions'] }),
  });
}

export function useCompleteTask() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, done }: { id: string; done: boolean }) =>
      api!.updateTask(id, { status: done ? 'done' : 'pending' }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['tasks'] }),
  });
}

export function useDeleteSession() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api!.deleteSession(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['sessions'] });
      void qc.invalidateQueries({ queryKey: ['memory'] });
      void qc.invalidateQueries({ queryKey: ['tasks'] });
      void qc.invalidateQueries({ queryKey: ['actions'] });
    },
  });
}

export function useEditAction() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: string;
      patch: { title?: string; date?: string; time?: string };
    }) => api!.editAction(id, patch),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['actions'] }),
  });
}

export function useBriefing() {
  const api = useApi();
  return useQuery({
    queryKey: ['briefing'],
    queryFn: () => api!.briefing(),
    enabled: !!api,
    staleTime: 60000,
  });
}

export function useAsk() {
  const api = useApi();
  const tz = useSettings((s) => s.tz);
  return useMutation({
    mutationFn: (question: string) => api!.ask(question, tz),
  });
}
