/**
 * The one door to data (frontend AGENTS rule 8). The live Viewer reads the API through the
 * generated client; an exported HTML page (design §18.22) reads the JSON embedded in
 * #lite-eval-data and cannot write. Pages never call the client directly.
 */
import * as sdk from '@/client/sdk.gen'
import type {
  Annotation,
  AnnotationRequest,
  CaseDetail,
  CasePage,
  Comparison,
  DatasetCasePage,
  DatasetSummary,
  ExportPayload,
  RunDetail,
  RunPage,
  RunRow,
  Trend,
} from '@/client/types.gen'

export type RunQuery = {
  suite?: string
  config?: string
  status?: RunRow['status']
  dataset?: string
  baseline?: boolean
  offset?: number
  limit?: number
}

export type CaseQuery = { missesOnly?: boolean; offset?: number; limit?: number }

export interface DataSource {
  /** `embedded`: an exported page; only the runs it carries, nothing written. */
  readonly mode: 'live' | 'embedded'
  listRuns(query: RunQuery): Promise<RunPage>
  getRun(suite: string, runId: string): Promise<RunDetail>
  listCases(suite: string, runId: string, query: CaseQuery): Promise<CasePage>
  getCase(suite: string, runId: string, caseId: string): Promise<CaseDetail>
  compare(baseline: string, candidate: string): Promise<Comparison>
  listDatasets(): Promise<DatasetSummary[]>
  listDatasetCases(name: string, offset: number, limit: number): Promise<DatasetCasePage>
  trend(suite: string, metric: string, config?: string): Promise<Trend>
  annotate(body: AnnotationRequest): Promise<Annotation>
}

/** An error the API (or the export) answered with: status, code and its own message. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

type Result<T> = { data?: T; error?: unknown; response?: Response }

function message(error: unknown): { code: string; text: string } {
  if (error && typeof error === 'object') {
    const body = error as { error?: { message?: string; code?: string }; detail?: unknown }
    if (body.error?.message) return { code: body.error.code ?? 'error', text: body.error.message }
    if (Array.isArray(body.detail)) {
      const first = body.detail[0] as { msg?: string } | undefined
      return { code: 'invalid', text: first?.msg ?? 'the request was not valid' }
    }
  }
  return { code: 'error', text: 'the Viewer could not be reached' }
}

async function unwrap<T>(pending: Promise<Result<T>>): Promise<T> {
  let result: Result<T>
  try {
    result = await pending
  } catch {
    throw new ApiError(0, 'network', 'the Viewer could not be reached; is `lite-eval view` running?')
  }
  if (result.data !== undefined && result.response?.ok) return result.data
  const { code, text } = message(result.error)
  throw new ApiError(result.response?.status ?? 0, code, text)
}

function token(): string {
  return document.querySelector<HTMLMetaElement>('meta[name="lite-eval-token"]')?.content ?? ''
}

export const liveSource: DataSource = {
  mode: 'live',
  listRuns: (q) => unwrap(sdk.listRuns({ query: q })), // RunQuery's names are the API's
  getRun: (suite, runId) => unwrap(sdk.getRun({ path: { suite, run_id: runId } })),
  listCases: (suite, runId, q) =>
    unwrap(
      sdk.listCases({
        path: { suite, run_id: runId },
        query: { misses_only: q.missesOnly, offset: q.offset, limit: q.limit },
      }),
    ),
  getCase: (suite, runId, caseId) =>
    unwrap(sdk.getCase({ path: { suite, run_id: runId, case_id: caseId } })),
  compare: (baseline, candidate) => unwrap(sdk.compareRuns({ query: { baseline, candidate } })),
  listDatasets: () => unwrap(sdk.listDatasets()),
  listDatasetCases: (name, offset, limit) =>
    unwrap(sdk.listDatasetCases({ path: { name }, query: { offset, limit } })),
  trend: (suite, metric, config) => unwrap(sdk.trend({ query: { suite, metric, config } })),
  annotate: (body) =>
    unwrap(sdk.createAnnotation({ body, headers: { 'X-Lite-Eval-Token': token() } })),
}

export type { ExportPayload }

const notInExport = (what: string) =>
  new ApiError(404, 'not_exported', `${what} is not part of this exported page; open it in \`lite-eval view\``)

export function embeddedSource(payload: ExportPayload): DataSource {
  const run = (suite: string, runId: string) => {
    const found = payload.runs[`${suite}/${runId}`]
    if (!found) throw notInExport(`run ${suite}/${runId}`)
    return found
  }
  const page = <T,>(items: T[], offset = 0, limit = items.length) => ({
    items: items.slice(offset, offset + limit),
    total: items.length,
    offset,
    limit,
  })
  return {
    mode: 'embedded',
    // An export holds one or two runs: no filters (the Runs page offers none in an export).
    listRuns: async (q) => page(Object.values(payload.runs).map((r) => r.row), q.offset, q.limit),
    getRun: async (suite, runId) => run(suite, runId).detail,
    listCases: async (suite, runId, q) => {
      const rows = run(suite, runId).rows.items.filter((r) => (q.missesOnly ? r.miss : true))
      return page(rows, q.offset, q.limit)
    },
    getCase: async (suite, runId, caseId) => {
      const found = run(suite, runId).cases[caseId]
      if (!found) throw notInExport(`case ${caseId}`)
      return found
    },
    compare: async (baseline, candidate) => {
      // the payload's runs are keyed "<suite>/<run_id>", baseline first (report/html.py)
      const [a, b] = Object.keys(payload.runs)
      if (!payload.comparison || baseline !== a || candidate !== b) throw notInExport('this comparison')
      return payload.comparison
    },
    listDatasets: async () => {
      throw notInExport('the datasets page')
    },
    listDatasetCases: async () => {
      throw notInExport('the datasets page')
    },
    trend: async () => {
      throw notInExport('the trends page')
    },
    annotate: async () => {
      throw new ApiError(403, 'read_only', 'an exported page is read-only; triage in `lite-eval view`')
    },
  }
}

/** The embedded payload when this page is an export, else null. */
export function readEmbedded(doc: Document = document): ExportPayload | null {
  const el = doc.getElementById('lite-eval-data')
  return el?.textContent ? (JSON.parse(el.textContent) as ExportPayload) : null
}
