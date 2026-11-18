import { queryOptions } from '@tanstack/react-query'
import type { DataSource, RunQuery } from './source'

/**
 * Every query's key and arguments in one place, so pages that share data share the cache.
 * Run records are immutable (AGENTS rule 3): run-scoped data never goes stale; only an
 * annotation changes it, and the annotation dialog invalidates exactly those keys.
 */
const FOREVER = Infinity
const ALL = 100_000 // every case of a run (the API allows this many)
const DATASETS_STALE = 5 * 60_000 // the server hashes whole datasets per request

/** The first element of every query key: a family to invalidate as a whole. */
export const keys = {
  run: 'run',
  cases: 'cases',
  case: 'case',
  compare: 'compare',
  runs: 'runs',
  datasets: 'datasets',
  datasetCases: 'dataset-cases',
  trend: 'trend',
} as const

export const runQuery = (source: DataSource, suite: string, runId: string) =>
  queryOptions({ queryKey: [keys.run, suite, runId], queryFn: () => source.getRun(suite, runId), staleTime: FOREVER })

export const casesQuery = (source: DataSource, suite: string, runId: string) =>
  queryOptions({
    queryKey: [keys.cases, suite, runId],
    queryFn: () => source.listCases(suite, runId, { limit: ALL }),
    staleTime: FOREVER,
  })

export const caseQuery = (source: DataSource, suite: string, runId: string, caseId: string) =>
  queryOptions({
    queryKey: [keys.case, suite, runId, caseId],
    queryFn: () => source.getCase(suite, runId, caseId),
    staleTime: FOREVER,
  })

export const compareQuery = (source: DataSource, baseline: string, candidate: string) =>
  queryOptions({
    queryKey: [keys.compare, baseline, candidate],
    queryFn: () => source.compare(baseline, candidate),
    staleTime: FOREVER,
  })

/** The runs list changes as new runs land: refreshed after a few seconds. */
export const runsQuery = (source: DataSource, query: RunQuery) =>
  queryOptions({ queryKey: [keys.runs, query], queryFn: () => source.listRuns(query), staleTime: 5_000 })

/** Every run (pickers on Compare and Trends). */
export const allRunsQuery = (source: DataSource) => runsQuery(source, { limit: 500 })

/**
 * Datasets change only by an edit in git, and listing them hashes them on the server: kept for a
 * while and not refetched on window focus. An annotation save invalidates dataset cases.
 */
export const datasetsQuery = (source: DataSource) =>
  queryOptions({
    queryKey: [keys.datasets],
    queryFn: () => source.listDatasets(),
    staleTime: DATASETS_STALE,
    refetchOnWindowFocus: false,
  })

export const datasetCasesQuery = (source: DataSource, name: string, offset: number, limit: number) =>
  queryOptions({
    queryKey: [keys.datasetCases, name, offset, limit],
    queryFn: () => source.listDatasetCases(name, offset, limit),
    staleTime: DATASETS_STALE,
    refetchOnWindowFocus: false,
  })

/** One metric of one config over a suite's runs; waits until a suite and a metric are chosen. */
export const trendQuery = (source: DataSource, suite: string | undefined, metric: string | undefined, config: string | undefined) =>
  queryOptions({
    queryKey: [keys.trend, suite, metric, config],
    queryFn: () => source.trend(suite!, metric!, config),
    enabled: Boolean(suite && metric),
  })
