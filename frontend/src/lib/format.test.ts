import { expect, test } from 'vitest'
import { delta, metric, weight } from './format'
import { typing } from './keys'

test('a nonzero number never reads as zero', () => {
  expect(metric(0.75)).toBe('0.750')
  expect(metric(0.0004)).toBe('0.00040')
  expect(metric(0)).toBe('0.000')
  expect(delta(0.0004)).toBe('+0.00040')
  expect(delta(-0.0002)).toBe('−0.00020')
  expect(delta(0)).toBe('±0.000')
  expect(weight(1 / 32)).toBe('0.031')
  expect(weight(1 / 2500)).toBe('0.00040') // 2500 cases: one case still has a visible weight
})

test('keys typed into any form control are never page shortcuts', () => {
  const box = document.createElement('input')
  box.type = 'checkbox'
  expect(typing(box)).toBe(true) // X on a focused checkbox does not also toggle the row
  expect(typing(document.createElement('a'))).toBe(false)
})
