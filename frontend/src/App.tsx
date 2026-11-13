import { QueryClient, QueryClientProvider, keepPreviousData } from '@tanstack/react-query'
import { RouterProvider, createHashHistory, createMemoryHistory, createRouter } from '@tanstack/react-router'
import { useState } from 'react'
import { DataProvider } from '@/data/context'
import type { DataSource } from '@/data/source'
import { routeTree } from './routeTree.gen'

/**
 * Hash history: the live Viewer and an exported file (opened from disk, no server to rewrite
 * paths) share one routing scheme (design §18.22). Tests pass a memory history.
 */
export function createAppRouter(initial?: string) {
  return createRouter({
    routeTree,
    history: initial ? createMemoryHistory({ initialEntries: [initial] }) : createHashHistory(),
    defaultPreload: 'intent',
    scrollRestoration: true,
  })
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof createAppRouter>
  }
}

export function App({ source, initial }: { source: DataSource; initial?: string }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: false,
            staleTime: source.mode === 'embedded' ? Infinity : 5_000,
            placeholderData: keepPreviousData,
          },
        },
      }),
  )
  const [router] = useState(() => createAppRouter(initial))
  return (
    <DataProvider source={source}>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </DataProvider>
  )
}
