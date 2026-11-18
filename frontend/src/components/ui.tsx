import { useCallback, useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'
import { AlertTriangle, ArrowDownRight, ArrowUpRight, Equal, Minus, Ban } from 'lucide-react'
import { ApiError } from '@/data/source'
import { cn } from '@/lib/cn'
import { VERDICT_WORD, type Verdict } from '@/lib/verdict'

type Variant = 'primary' | 'quiet'
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }

/** The button look, also for links that act as buttons (one vocabulary for every control). */
export function buttonClass(variant: Variant = 'quiet', className?: string): string {
  return cn(
    'inline-flex h-7 items-center gap-1.5 whitespace-nowrap rounded px-2.5 text-[13px] font-medium',
    'border transition-colors duration-150',
    'disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:pointer-events-none aria-disabled:opacity-50',
    variant === 'primary'
      ? 'border-ink bg-ink text-surface hover:opacity-90'
      : 'border-rule-strong bg-raised text-ink hover:bg-hover',
    className,
  )
}

export function Button({ variant = 'quiet', className, ...props }: ButtonProps) {
  return <button type="button" {...props} className={buttonClass(variant, className)} />
}

/** A labelled select, the one filter control. */
export function Select<T extends string>({
  label,
  value,
  options,
  onChange,
  disabled,
  className,
}: {
  label: string
  value: T | ''
  options: { value: T | ''; label: string }[] | readonly T[]
  onChange: (value: T) => void
  disabled?: boolean
  className?: string
}) {
  const items = options.map((o) => (typeof o === 'string' ? { value: o, label: o } : o))
  return (
    <label className="flex items-center gap-1.5 text-ink-2">
      {label}
      <select
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value as T)}
        className={cn('h-7 rounded border border-rule-strong bg-raised px-1.5 text-ink', className)}
      >
        {items.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

/** The keyboard hint under a list, e.g. <KeyHints hints={[['J K', 'move'], ['Enter', 'open']]} />. */
export function KeyHints({ hints, className }: { hints: [string, string][]; className?: string }) {
  return (
    <p className={cn('border-t border-rule px-3 py-1.5 text-[12px] text-ink-2', className)}>
      {hints.map(([keys, what], i) => (
        <span key={keys} className="whitespace-nowrap">
          {i > 0 && ' · '}
          {keys.split(' ').map((k) => (
            <Kbd key={k}>{k}</Kbd>
          ))}{' '}
          {what}
        </span>
      ))}
    </p>
  )
}

/** A run's or a comparison's warnings: code + message. */
export function WarningsList({ warnings }: { warnings: { code: string; message: string }[] }) {
  if (warnings.length === 0) return null
  return (
    <Section title={`Warnings (${warnings.length})`}>
      <ul className="divide-y divide-rule">
        {warnings.map((w, i) => (
          <li key={i} className="px-3 py-2">
            <span className="mr-2 font-mono text-[12px] text-warn-text">{w.code}</span>
            <span className="text-ink">{w.message}</span>
          </li>
        ))}
      </ul>
    </Section>
  )
}

/** A short key hint, e.g. <Kbd>J</Kbd>. */
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-rule-strong bg-raised px-1 font-mono text-[11px] text-ink-2">
      {children}
    </kbd>
  )
}

const VERDICT_STYLE: Record<Verdict, { icon: typeof Minus; mark: string }> = {
  worse: { icon: ArrowDownRight, mark: 'text-worse-mark' },
  better: { icon: ArrowUpRight, mark: 'text-better-mark' },
  not_significant: { icon: Minus, mark: 'text-neutral-mark' },
  no_change: { icon: Equal, mark: 'text-neutral-mark' },
  incomparable: { icon: Ban, mark: 'text-neutral-mark' },
}

/** A verdict as icon + word: colour on the icon only, the word in ink (never colour alone). */
export function VerdictMark({ verdict, compact = false }: { verdict: Verdict; compact?: boolean }) {
  const { icon: Icon, mark } = VERDICT_STYLE[verdict]
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap text-ink-2">
      <Icon aria-hidden className={cn('size-3.5 shrink-0', mark)} strokeWidth={2.25} />
      <span className={compact ? 'sr-only' : undefined}>{VERDICT_WORD[verdict]}</span>
    </span>
  )
}

/** A small labelled tag: baseline, partial, synthetic, n warnings. */
export function Tag({
  tone = 'plain',
  title,
  children,
}: {
  tone?: 'plain' | 'warn' | 'worse'
  title?: string
  children: ReactNode
}) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex h-[18px] items-center gap-1 whitespace-nowrap rounded-sm px-1.5 text-[11px] font-medium',
        tone === 'plain' && 'bg-select text-ink-2',
        tone === 'warn' && 'bg-warn-wash text-warn-text',
        tone === 'worse' && 'bg-worse-wash text-worse-text',
      )}
    >
      {tone !== 'plain' && <AlertTriangle aria-hidden className="size-3" />}
      {children}
    </span>
  )
}

export function Skeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading" className="space-y-2 p-4">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-5 animate-pulse rounded bg-hover" style={{ width: `${90 - (i % 3) * 12}%` }} />
      ))}
    </div>
  )
}

export function ErrorPanel({ error }: { error: unknown }) {
  const text =
    error instanceof ApiError
      ? error.message
      : error instanceof Error
        ? error.message
        : 'Something went wrong while reading this page.'
  const status = error instanceof ApiError && error.status ? ` (${error.status})` : ''
  return (
    <div role="alert" className="m-4 rounded border border-rule-strong bg-raised p-4">
      <p className="font-medium text-ink">Could not load this page{status}</p>
      <p className="mt-1 text-ink-2">{text}</p>
    </div>
  )
}

/** A query's three states: a skeleton while it loads, the error panel if it failed, else the page. */
export function Loaded<T>({
  query,
  rows,
  children,
}: {
  query: UseQueryResult<T>
  rows?: number
  children: (data: T) => ReactNode
}) {
  if (query.isPending) return <Skeleton rows={rows} />
  if (query.isError) return <ErrorPanel error={query.error} />
  return children(query.data)
}

/** An empty state that says what would fill it. */
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="px-4 py-10 text-center">
      <p className="font-medium text-ink">{title}</p>
      {children && <div className="mx-auto mt-1 max-w-[60ch] text-ink-2">{children}</div>}
    </div>
  )
}

export function Pager({
  offset,
  limit,
  total,
  onChange,
}: {
  offset: number
  limit: number
  total: number
  onChange: (offset: number) => void
}) {
  if (total <= limit) return null
  const last = Math.min(offset + limit, total)
  return (
    <nav aria-label="Pages" className="flex items-center justify-end gap-2 border-t border-rule px-3 py-2 text-ink-2">
      <span>
        {offset + 1}–{last} of {total}
      </span>
      <Button disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        Previous
      </Button>
      <Button disabled={last >= total} onClick={() => onChange(offset + limit)}>
        Next
      </Button>
    </nav>
  )
}

/**
 * A horizontally scrolling table: when more columns sit to the right, the edge fades so a
 * clipped table shows that it scrolls (the first column stays sticky in the tables that use it).
 */
export function Scroll({ children }: { children: ReactNode }) {
  const [more, setMore] = useState(false)
  const box = useRef<HTMLDivElement | null>(null)
  const measure = useCallback(() => {
    const el = box.current
    if (el) setMore(el.scrollLeft + el.clientWidth < el.scrollWidth - 1)
  }, [])
  useEffect(() => {
    const el = box.current
    if (!el) return
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [measure])
  return (
    <div className="relative">
      <div ref={box} onScroll={measure} className="overflow-x-auto">
        {children}
      </div>
      {more && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-surface to-transparent"
        />
      )}
    </div>
  )
}

/** Page section with a heading row and optional actions on the right. */
export function Section({
  title,
  actions,
  children,
  className,
}: {
  title: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={cn('rounded border border-rule bg-surface', className)}>
      <header className="flex min-h-9 items-center justify-between gap-3 border-b border-rule px-3 py-1.5">
        <h2 className="text-[13px] font-semibold text-ink">{title}</h2>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </header>
      {children}
    </section>
  )
}
