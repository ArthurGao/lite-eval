/**
 * Display only: these never decide anything (frontend AGENTS rule 2). A metric keeps three
 * decimals so a one-case change (1/n) on a few hundred cases still shows.
 */

/** Three decimals, or two significant digits when three would round a nonzero value to zero. */
function digits(value: number): string {
  const fixed = Math.abs(value).toFixed(3)
  return fixed === '0.000' && value !== 0 ? Math.abs(value).toPrecision(2) : fixed
}

export function metric(value: number | null | undefined): string {
  if (value == null) return '—'
  return `${value < 0 ? '-' : ''}${digits(value)}`
}

export function delta(value: number | null | undefined): string {
  if (value == null) return '—'
  if (value === 0) return '±0.000'
  return `${value > 0 ? '+' : '−'}${digits(value)}`
}

/** How much one case moves a 0..1 metric: never "0.000" on a large run. */
export function weight(caseWeight: number): string {
  return digits(caseWeight)
}

export function pValue(p: number | null | undefined): string {
  if (p == null) return '—'
  if (p < 0.001) return 'p < 0.001'
  return `p = ${p.toFixed(3)}`
}

export function ms(value: number | null | undefined): string {
  if (value == null) return '—'
  return value < 10 ? `${value.toFixed(1)} ms` : `${Math.round(value)} ms`
}

export function usd(value: number): string {
  return value === 0 ? '$0' : value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`
}

/** "2026-11-14 09:05" in the viewer's time zone; the full ISO time goes in the title. */
export function when(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** The distinguishing tail of a run id ("<UTC time>-<config>-<6 hex>"). */
export function runTag(runId: string): string {
  return runId.slice(-6)
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : '—'
}

export function shortHash(hash: string): string {
  return hash.slice(0, 12)
}

/** A run record's config name (the runner records it as `target.config.name`). */
export function configName(record: { target: { name: string; config: unknown } }): string {
  const config = record.target.config as Record<string, unknown> | null
  return String(config?.name ?? record.target.name)
}
