import { describe, expect, test } from 'vitest'
import type { MetricDelta } from '@/client/types.gen'
import { niceAxis } from '@/components/trend'
import { intervalDomain, layoutSpans, verdict } from './verdict'

const d = (over: Partial<MetricDelta>): MetricDelta => ({
  metric: 'hit@5',
  status: 'compared',
  delta: 0.1,
  significant: true,
  discordant: 3,
  ...over,
})

describe('verdict', () => {
  test('only a significant delta is better or worse', () => {
    expect(verdict(d({ delta: 0.1 }))).toBe('better')
    expect(verdict(d({ delta: -0.1 }))).toBe('worse')
    expect(verdict(d({ delta: -0.1, significant: false }))).toBe('not_significant')
    expect(verdict(d({ delta: 0.2, significant: null }))).toBe('not_significant')
  })

  test('no change and incomparable are their own states', () => {
    expect(verdict(d({ delta: 0, discordant: 0, significant: false }))).toBe('no_change')
    expect(verdict(d({ delta: null }))).toBe('no_change')
    expect(verdict(d({ status: 'incomparable', delta: null }))).toBe('incomparable')
    // equal means but cases moved both ways: still a comparison to show, not "no change"
    expect(verdict(d({ delta: 0, discordant: 4, significant: false }))).toBe('not_significant')
  })
})

test('the interval scale holds every delta and interval end, with a margin', () => {
  const scale = intervalDomain([
    d({ delta: 0.1, ci_low: -0.02, ci_high: 0.2 }),
    d({ delta: -0.3, ci_low: null, ci_high: null }),
  ])
  expect(scale).toBeCloseTo(0.33, 10)
  expect(intervalDomain([d({ delta: 0, ci_low: null, ci_high: null })])).toBe(0.05)
})

describe('layoutSpans', () => {
  test('bars are placed in percent of the trace and nested spans go one level deeper', () => {
    const { total, bars } = layoutSpans(
      [
        { kind: 'model', name: 'answer', start_ms: 40, duration_ms: 50 },
        { kind: 'retrieval', name: 'search', start_ms: 0, duration_ms: 40 },
        { kind: 'tool', name: 'rerank', start_ms: 10, duration_ms: 20 },
      ],
      100,
    )
    expect(total).toBe(100)
    expect(bars.map((b) => [b.span.name, b.left, b.width, b.depth])).toEqual([
      ['search', 0, 40, 0],
      ['rerank', 10, 20, 1],
      ['answer', 40, 50, 0],
    ])
  })

  test('a trace longer than its latency stretches the axis; empty spans stay visible', () => {
    const { total, bars } = layoutSpans([{ kind: 'k', start_ms: 0, duration_ms: 0 }], null)
    expect(total).toBe(0)
    expect(bars[0]?.width).toBe(100)
    expect(layoutSpans([{ kind: 'k', start_ms: 50, duration_ms: 150 }], 120).total).toBe(200)
  })
})

test('trend axes use round ticks: 1, 2 or 5 times a power of ten', () => {
  // range 0.083 -> a quarter is 0.021 -> step 0.05
  expect(niceAxis(0.667, 0.75)).toEqual({ y0: 0.65, y1: 0.75, ticks: [0.65, 0.7, 0.75], decimals: 2 })
  // a single value still gets a span (at least 0.02) -> step 0.005
  expect(niceAxis(0.906, 0.906)).toEqual({ y0: 0.905, y1: 0.91, ticks: [0.905, 0.91], decimals: 3 })
  expect(niceAxis(0, 1)).toEqual({ y0: 0, y1: 1, ticks: [0, 0.5, 1], decimals: 1 })
})
