import type { CaseRow, RunRow } from '@/client/types.gen'

/** A complete RunRow (every required field), overridable per test. */
export function runRow(over: Partial<RunRow> = {}): RunRow {
  return {
    suite: 'demo',
    run_id: 'r',
    config: 'a',
    target: 'fake',
    dataset: 'golden',
    dataset_version: '1',
    dataset_hash: 'h',
    pack: null,
    synthetic: false,
    status: 'complete',
    started_at: '2026-11-14T00:00:00Z',
    finished_at: '2026-11-14T00:00:01Z',
    git_sha: null,
    metrics: { exact_match: 0.75 },
    n_cases: 4,
    case_weight: 0.25,
    calls: 4,
    cost_usd: 0,
    latency_p50_ms: 12,
    latency_p95_ms: 15,
    warnings: 0,
    target_errors: 0,
    scoring_errors: 0,
    baseline: false,
    ...over,
  }
}

export function caseRow(id: string, miss: boolean): CaseRow {
  return { case_id: id, tags: {}, miss, error: null, scores: {}, annotation: null, older_annotation: false }
}
