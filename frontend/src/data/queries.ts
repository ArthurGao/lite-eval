import { queryOptions } from '@tanstack/react-query'
import type { DataSource, RunQuery } from './source'

/**
 * Every query's key and arguments in one place, so pages that share data share the cache.
 * Run records are immutable (AGENTS rule 3): run-scoped data never goes stale; only an
 * annotation changes it, and the annotation dialog invalidates exactly those keys.
 */
const FOREVER = Infinity
const ALL = 100_000 // every case of a run (the API allows this many)

export const runQuery = (source: DataSource, suite: string, runId: string) =>
  queryOptions({ queryKey: ['run', suite, runId], queryFn: () => source.getRun(suite, runId), staleTime: FOREVER })

export const casesQuery = (source: DataSource, suite: string, runId: string) =>
  queryOptions({
    queryKey: ['cases', suite, runId],
    queryFn: () => source.listCases(suite, runId, { limit: ALL }),
    staleTime: FOREVER,
  })

export const caseQuery = (source: DataSource, suite: string, runId: string, caseId: string) =>
  queryOptions({
    queryKey: ['case', suite, runId, caseId],
    queryFn: () => source.getCase(suite, runId, caseId),
    staleTime: FOREVER,
  })

export const compareQuery = (source: DataSource, baseline: string, candidate: string) =>
  queryOptions({
    queryKey: ['compare', baseline, candidate],
    queryFn: () => source.compare(baseline, candidate),
    staleTime: FOREVER,
  })

/** The runs list changes as new runs land: refreshed after a few seconds. */
export const runsQuery = (source: DataSource, query: RunQuery) =>
  queryOptions({ queryKey: ['runs', query], queryFn: () => source.listRuns(query), staleTime: 5_000 })

/** Every run (pickers on Compare and Trends). */
export const allRunsQuery = (source: DataSource) => runsQuery(source, { limit: 500 })
