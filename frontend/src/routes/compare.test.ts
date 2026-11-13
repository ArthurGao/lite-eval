import { expect, test } from 'vitest'
import type { CaseChange } from '@/client/types.gen'
import { word } from './compare'

const change = (over: Partial<CaseChange>): CaseChange => ({
  case_id: 'q1',
  metric: 'mrr@10',
  kind: 'changed',
  baseline: 0.5,
  candidate: 0.2,
  direction: 'worse',
  ...over,
})

test("a case's change reads in words; which way is worse comes from the API", () => {
  expect(word(change({ kind: 'regressed' }))).toBe('regressed')
  expect(word(change({ kind: 'fixed', direction: 'better' }))).toBe('fixed')
  expect(word(change({ direction: 'worse' }))).toBe('lower')
  // the API's direction wins over the raw values (a lower-is-better metric reads "higher" here)
  expect(word(change({ direction: 'better' }))).toBe('higher')
})
