import { describe, expect, test } from 'vitest'
import type { Comparison } from '@/client/types.gen'
import { caseRow, runRow as row } from '@/test/factories'
import { ApiError, embeddedSource, readEmbedded, type ExportPayload } from './source'

const payload: ExportPayload = {
  kind: 'compare',
  generated_at: '2026-11-14T00:00:00Z',
  lite_eval_version: '0.0.0',
  start: '/compare?baseline=demo/r1&candidate=demo/r2',
  runs: {
    'demo/r1': {
      row: row({ run_id: 'r1' }),
      detail: {} as never,
      rows: { items: [caseRow('q1', true), caseRow('q2', false)], total: 2, offset: 0, limit: 2 },
      cases: {},
    },
    'demo/r2': {
      row: row({ run_id: 'r2', config: 'b' }),
      detail: {} as never,
      rows: { items: [], total: 0, offset: 0, limit: 0 },
      cases: {},
    },
  },
  comparison: { baseline_run: 'r1', candidate_run: 'r2' } as Comparison,
}

describe('embeddedSource', () => {
  const source = embeddedSource(payload)

  test('lists every exported run (an export has no filters) and filters cases like the API', async () => {
    expect((await source.listRuns({})).items.map((r) => r.run_id)).toEqual(['r1', 'r2'])
    expect((await source.listRuns({ config: 'b', status: 'failed' })).total).toBe(2)
    expect((await source.listRuns({ offset: 1, limit: 1 })).items.map((r) => r.run_id)).toEqual(['r2'])
    const misses = await source.listCases('demo', 'r1', { missesOnly: true })
    expect([misses.total, misses.items.map((r) => r.case_id)]).toEqual([1, ['q1']])
  })

  test('answers only the comparison it carries, and never writes', async () => {
    expect((await source.compare('demo/r1', 'demo/r2')).candidate_run).toBe('r2')
    await expect(source.compare('demo/r2', 'demo/r1')).rejects.toMatchObject({ code: 'not_exported' })
    await expect(source.getRun('demo', 'r9')).rejects.toBeInstanceOf(ApiError)
    await expect(source.annotate({} as never)).rejects.toMatchObject({ status: 403, code: 'read_only' })
    await expect(source.trend('demo', 'm')).rejects.toMatchObject({ code: 'not_exported' })
  })
})

test('the embedded payload is read from #lite-eval-data, escaped text included', () => {
  const doc = document.implementation.createHTMLDocument()
  expect(readEmbedded(doc)).toBeNull()
  const el = doc.createElement('script')
  el.type = 'application/json'
  el.id = 'lite-eval-data'
  el.textContent = '{"kind":"run","runs":{},"note":"\\u003c/script\\u003e"}'
  doc.body.append(el)
  expect(readEmbedded(doc)).toMatchObject({ kind: 'run', note: '</script>' })
})
