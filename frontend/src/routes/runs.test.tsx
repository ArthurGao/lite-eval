import { render, screen, within } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import { runRow as row } from '@/test/factories'
import type { DataSource } from '@/data/source'
import { App } from '../App'
import { comparePair } from './index'

describe('comparePair', () => {
  test("the suite's baseline goes first, else the older run", () => {
    const old = row({ run_id: 'old', started_at: '2026-11-13T00:00:00Z' })
    const recent = row({ run_id: 'new', started_at: '2026-11-14T00:00:00Z' })
    expect(comparePair(recent, old).map((r) => r.run_id)).toEqual(['old', 'new'])
    const base = { ...recent, baseline: true }
    expect(comparePair(old, base).map((r) => r.run_id)).toEqual(['new', 'old'])
  })
})

test('the Runs page marks every doubt a run carries', async () => {
  const rows = [
    row({ run_id: 'r1', config: 'cand', status: 'partial', warnings: 2, target_errors: 1 }),
    row({ run_id: 'r2', config: 'base', baseline: true, synthetic: true }),
  ]
  const source = {
    mode: 'live',
    listRuns: async () => ({ items: rows, total: 2, offset: 0, limit: 50 }),
  } as unknown as DataSource
  render(<App source={source} initial="/" />)

  const cand = (await screen.findByRole('link', { name: 'cand' })).closest('tr')!
  expect(within(cand).getByText('partial')).toBeInTheDocument()
  expect(within(cand).getByText('2 warnings')).toBeInTheDocument()
  expect(within(cand).getByText('· 1 err')).toBeInTheDocument()
  const base = screen.getByRole('link', { name: 'base' }).closest('tr')!
  expect(within(base).getByText('baseline')).toBeInTheDocument()
  expect(within(base).getByText('synthetic')).toBeInTheDocument()
  // one column per metric when every run reports the same metrics
  expect(screen.getByRole('columnheader', { name: 'exact_match' })).toBeInTheDocument()
  expect(within(cand).getAllByText('0.750')).toHaveLength(1)
})
