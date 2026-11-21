/** Design tokens. Dark-first: this is an app you glance at, often one-handed. */

export const colors = {
  bg: '#0B0D12',
  surface: '#151922',
  surfaceAlt: '#1D222D',
  border: '#272D3A',
  text: '#E9ECF2',
  textDim: '#9BA3B4',
  textFaint: '#6B7383',

  accent: '#4C8DFF',
  record: '#FF4D4F',
  success: '#34C77B',
  warning: '#F2B035',
  danger: '#FF6B6B',
} as const;

/** One colour per speech act, so the distinction is visible at a glance. */
export const actColors: Record<string, string> = {
  commitment: colors.success,
  assignment: colors.accent,
  requirement: colors.warning,
  decision: '#A78BFA',
  suggestion: colors.textDim,
  question: colors.textDim,
  discussion: colors.textFaint,
};

export const space = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const;

export const radius = { sm: 8, md: 12, lg: 16, pill: 999 } as const;

export const type = {
  title: { fontSize: 26, fontWeight: '700' as const, color: colors.text },
  heading: { fontSize: 18, fontWeight: '600' as const, color: colors.text },
  body: { fontSize: 15, color: colors.text, lineHeight: 21 },
  dim: { fontSize: 13, color: colors.textDim, lineHeight: 18 },
  tiny: { fontSize: 11, color: colors.textFaint },
  mono: { fontSize: 13, color: colors.textDim, fontFamily: 'monospace' as const },
};

export function formatDate(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  });
}

export function formatTime(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

/** Human label for a pipeline status, phrased as progress not jargon. */
export function statusLabel(status: string): string {
  switch (status) {
    case 'open':
      return 'Recording';
    case 'queued':
    case 'transcribing':
      return 'Transcribing…';
    case 'transcribed':
    case 'analyzing':
      return 'Understanding…';
    case 'analyzed':
    case 'memory_written':
    case 'actions_proposed':
      return 'Finishing…';
    case 'analyzed_degraded':
      return 'Partly processed';
    case 'ready':
      return 'Ready';
    case 'failed':
      return 'Failed';
    default:
      return status;
  }
}
