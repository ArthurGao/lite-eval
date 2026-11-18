import { describe, expect, it } from 'vitest'

import type { DataSource } from './source'
import { datasetCasesQuery } from './queries'

describe('query keys', () => {
  it('a dataset page is keyed by every argument it is fetched with', () => {
    const source = {} as DataSource
    const a = datasetCasesQuery(source, 'golden', 0, 100).queryKey
    const b = datasetCasesQuery(source, 'golden', 0, 50).queryKey
    expect(a).not.toEqual(b)
  })
})
