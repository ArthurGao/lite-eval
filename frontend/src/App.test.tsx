import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import { App } from './App'

afterEach(cleanup)

test('the placeholder names the Viewer and the stage that builds it', () => {
  render(<App />)

  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('lite-eval Viewer')
  expect(screen.getByText(/arrives in stage E3/).textContent).toBe(
    'Not built yet: the Viewer arrives in stage E3.',
  )
})
