import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { Loaded } from './ui'

function Page({ load }: { load: () => Promise<string> }) {
  const q = useQuery({ queryKey: ['loaded-test'], queryFn: load, retry: false })
  return (
    <Loaded query={q} rows={3}>
      {(data) => <p>got {data}</p>}
    </Loaded>
  )
}

const show = (load: () => Promise<string>) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Page load={load} />
    </QueryClientProvider>,
  )

test('Loaded shows a skeleton of the asked size while the query is pending', () => {
  show(() => new Promise<string>(() => {}))
  const skeleton = screen.getByLabelText('Loading')
  expect(skeleton.children).toHaveLength(3)
  expect(screen.queryByText(/got/)).not.toBeInTheDocument()
})

test('Loaded shows the error panel when the query fails', async () => {
  show(() => Promise.reject(new Error('disk on fire')))
  expect(await screen.findByRole('alert')).toHaveTextContent('disk on fire')
  expect(screen.queryByLabelText('Loading')).not.toBeInTheDocument()
})

test('Loaded renders its children with the data once loaded', async () => {
  show(() => Promise.resolve('three runs'))
  expect(await screen.findByText('got three runs')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
