import { Link, Outlet, createRootRoute } from '@tanstack/react-router'
import { Monitor, Moon, Sun } from 'lucide-react'
import { useState } from 'react'
import { useData } from '@/data/context'
import { applyTheme, readTheme, type Theme } from '@/lib/theme'
import { Empty } from '@/components/ui'

export const Route = createRootRoute({
  component: Shell,
  notFoundComponent: () => (
    <Empty title="No such page">
      Go back to{' '}
      <Link to="/" className="underline">
        the runs
      </Link>
      .
    </Empty>
  ),
})

const NAV = [
  { to: '/', label: 'Runs', exact: true, live: false },
  { to: '/compare', label: 'Compare', exact: false, live: false },
  { to: '/datasets', label: 'Datasets', exact: false, live: true },
  { to: '/trends', label: 'Trends', exact: false, live: true },
] as const

const NEXT: Record<Theme, Theme> = { system: 'light', light: 'dark', dark: 'system' }
const THEME_ICON = { system: Monitor, light: Sun, dark: Moon }

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme)
  const Icon = THEME_ICON[theme]
  return (
    <button
      type="button"
      onClick={() => {
        const next = NEXT[theme]
        applyTheme(next)
        setTheme(next)
      }}
      className="inline-flex h-7 items-center gap-1.5 rounded px-2 text-ink-2 hover:bg-hover hover:text-ink"
      aria-label={`Theme: ${theme}. Switch to ${NEXT[theme]}`}
    >
      <Icon aria-hidden className="size-3.5" />
      <span className="hidden capitalize sm:inline">{theme}</span>
    </button>
  )
}

function Shell() {
  const source = useData()
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-20 border-b border-rule bg-canvas">
        <div className="mx-auto flex h-10 max-w-[1600px] items-center gap-3 px-3 sm:gap-5 sm:px-4">
          <Link to="/" className="font-semibold tracking-tight whitespace-nowrap text-ink">
            lite-eval
          </Link>
          <nav aria-label="Main" className="flex h-full items-stretch sm:gap-1">
            {NAV.filter((n) => source.mode === 'live' || !n.live).map((n) => (
              <Link
                key={n.to}
                to={n.to}
                activeOptions={{ exact: n.exact }}
                className="flex items-center border-b-2 border-transparent px-1.5 text-ink-2 sm:px-2 hover:text-ink data-[status=active]:border-ink data-[status=active]:text-ink"
              >
                {n.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            {source.mode === 'embedded' && (
              <span className="text-[12px] text-ink-2">Exported page · read-only</span>
            )}
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1600px] flex-1 px-3 py-4 sm:px-4">
        <Outlet />
      </main>
    </div>
  )
}
