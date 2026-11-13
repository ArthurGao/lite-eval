import { useEffect, useRef, useState } from 'react'
import { cn } from './cn'

/** True when a key press belongs to a text field or a dialog, not to page shortcuts. */
export function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return Boolean(target.closest('input, textarea, select, [contenteditable="true"], [role="dialog"]'))
}

/**
 * Single-key page shortcuts (PRODUCT.md: fully keyboard-operable). The one place that decides
 * a key is not a shortcut: a modifier is held, or the key is typed into a field or a dialog.
 * The map is read through a ref, so the listener subscribes once.
 */
export function useShortcuts(map: Record<string, () => void>, enabled = true): void {
  const current = useRef(map)
  useEffect(() => {
    current.current = map
  })
  useEffect(() => {
    if (!enabled) return
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return
      // Enter on a focused link or button is that control's own action, not a page shortcut
      if (e.key === 'Enter' && e.target instanceof HTMLElement && e.target.closest('a, button')) return
      const action = current.current[e.key]
      if (!action) return
      e.preventDefault()
      action()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enabled])
}

const ROW = 'border-b border-rule bg-surface last:border-b-0 hover:bg-hover'

/**
 * J / K move through a list, Enter opens the current item, and `extra` keys act on it.
 * `rowProps(i)` wires a row: its index, aria-current, click-to-select and the active style
 * (solid backgrounds, so a sticky first cell covers what scrolls under it).
 */
export function useListKeys<T extends HTMLElement = HTMLElement>(
  count: number,
  onOpen?: (index: number) => void,
  extra: Record<string, (index: number) => void> = {},
) {
  const [index, setIndex] = useState(count > 0 ? 0 : -1)
  const list = useRef<T | null>(null)
  const active = count === 0 ? -1 : Math.min(Math.max(index, 0), count - 1)

  const keys: Record<string, () => void> = {
    j: () => setIndex((i) => Math.min(Math.max(i, 0) + 1, count - 1)),
    k: () => setIndex((i) => Math.max(Math.min(i, count - 1) - 1, 0)),
  }
  if (onOpen && active >= 0) keys.Enter = () => onOpen(active)
  for (const [key, act] of Object.entries(extra)) if (active >= 0) keys[key] = () => act(active)
  useShortcuts(keys, count > 0)

  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [active])

  const rowProps = (i: number, className?: string) => ({
    'data-index': i,
    'aria-current': active === i ? ('true' as const) : undefined,
    onClick: () => setIndex(i),
    className: cn(ROW, active === i && 'bg-select hover:bg-select', className),
  })

  // `active`, not `current`: a field named current beside a ref reads as ref.current
  return { active, setActive: setIndex, list, rowProps }
}
