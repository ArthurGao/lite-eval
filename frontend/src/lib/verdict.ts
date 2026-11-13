import type { MetricDelta, Span } from '@/client/types.gen'

/**
 * How a metric delta reads. Decided only from what the API says (significant, the sign of the
 * delta): the browser computes no statistics (frontend AGENTS rule 2). Scorer metrics are 0..1,
 * higher is better.
 */
export type Verdict = 'worse' | 'better' | 'not_significant' | 'no_change' | 'incomparable'

export function verdict(d: MetricDelta): Verdict {
  if (d.status === 'incomparable') return 'incomparable'
  if (d.delta == null || (d.delta === 0 && (d.discordant ?? 0) === 0)) return 'no_change'
  if (d.significant !== true) return 'not_significant'
  return d.delta > 0 ? 'better' : 'worse'
}

export const VERDICT_WORD: Record<Verdict, string> = {
  worse: 'worse',
  better: 'better',
  not_significant: 'not significant',
  no_change: 'no change',
  incomparable: 'incomparable',
}

/** The half-width of a shared interval scale: every delta and interval end fits. */
export function intervalDomain(deltas: MetricDelta[]): number {
  let m = 0
  for (const d of deltas) {
    for (const v of [d.delta, d.ci_low, d.ci_high]) if (v != null) m = Math.max(m, Math.abs(v))
  }
  return m === 0 ? 0.05 : m * 1.1
}

export type SpanBar = { span: Span; left: number; width: number; depth: number }

/**
 * Trace spans as bars on one time axis, in percent of the trace's length (its latency, or the
 * end of the last span when longer). A span starting inside an earlier one that is still open
 * is drawn one level deeper, so nested calls read as nested.
 */
export function layoutSpans(spans: Span[], latencyMs?: number | null): { total: number; bars: SpanBar[] } {
  const end = spans.reduce((m, s) => Math.max(m, (s.start_ms ?? 0) + (s.duration_ms ?? 0)), 0)
  const total = Math.max(end, latencyMs ?? 0)
  const open: number[] = [] // end times of the enclosing spans
  const bars = [...spans]
    .sort((a, b) => (a.start_ms ?? 0) - (b.start_ms ?? 0))
    .map((span) => {
      const start = span.start_ms ?? 0
      const stop = start + (span.duration_ms ?? 0)
      while (open.length && (open[open.length - 1] ?? 0) <= start) open.pop()
      const depth = open.length
      open.push(stop)
      return {
        span,
        left: total ? (start / total) * 100 : 0,
        width: total ? Math.max(((stop - start) / total) * 100, 0.5) : 100,
        depth,
      }
    })
  return { total, bars }
}
