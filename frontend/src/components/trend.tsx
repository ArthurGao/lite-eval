import { useId, useState } from 'react'
import type { Trend } from '@/client/types.gen'
import { metric as fmt, runTag, shortSha, when } from '@/lib/format'

const H = 240
const PAD = { top: 28, right: 24, bottom: 28, left: 44 }

/** Annotations on the time axis: where the baseline switched and where the dataset changed. */
export function trendEvents(trend: Trend): { index: number; kind: 'baseline' | 'dataset'; label: string }[] {
  const at = new Map(trend.points.map((p, i) => [p.run_id, i]))
  const events: { index: number; kind: 'baseline' | 'dataset'; label: string }[] = []
  for (const c of trend.baseline_changes ?? []) {
    const i = at.get(c.run_id)
    if (i !== undefined) events.push({ index: i, kind: 'baseline', label: 'baseline set' })
  }
  for (const c of trend.dataset_changes ?? []) {
    const i = at.get(c.run_id)
    if (i !== undefined) events.push({ index: i, kind: 'dataset', label: `dataset ${c.previous_version} → ${c.version}` })
  }
  return events.sort((a, b) => a.index - b.index)
}

/** Round ticks (1, 2 or 5 times a power of ten) that cover [lo, hi], clamped to 0..1 for scores. */
export function niceAxis(lo: number, hi: number): { y0: number; y1: number; ticks: number[]; decimals: number } {
  const range = Math.max(hi - lo, 0.02)
  const raw = range / 4
  const power = 10 ** Math.floor(Math.log10(raw))
  const step = ([1, 2, 5, 10].find((m) => m * power >= raw) ?? 10) * power
  let y0 = Math.floor(lo / step) * step
  let y1 = Math.ceil(hi / step) * step
  if (y1 === y0) y1 = y0 + step
  if (lo >= 0 && hi <= 1) {
    y0 = Math.max(0, y0)
    y1 = Math.min(1, y1)
  }
  const ticks: number[] = []
  for (let t = y0; t <= y1 + step / 2; t += step) ticks.push(Number(t.toFixed(10)))
  const round = (v: number) => Number(v.toFixed(10))
  return { y0: round(y0), y1: round(y1), ticks, decimals: Math.max(0, -Math.floor(Math.log10(step))) }
}

/**
 * One metric over the suite's runs, in run order (dataviz: one series, 2px line, 8px points with
 * a surface ring, recessive grid, crosshair + tooltip, a table view below). Runs are evenly
 * spaced: the order of runs matters here, not the gaps between them.
 */
export function TrendChart({ trend, width }: { trend: Trend; width: number }) {
  const titleId = useId()
  const [hover, setHover] = useState<number | null>(null)
  const points = trend.points
  const values = points.map((p) => p.value)
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const { y0, y1, ticks, decimals } = niceAxis(lo, hi)
  const plotW = width - PAD.left - PAD.right
  const plotH = H - PAD.top - PAD.bottom
  const x = (i: number) => PAD.left + (points.length === 1 ? plotW / 2 : (i / (points.length - 1)) * plotW)
  const y = (v: number) => PAD.top + (1 - (v - y0) / (y1 - y0)) * plotH
  const events = trendEvents(trend)
  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join('')
  const hovered = hover != null ? points[hover] : undefined

  return (
    <div className="relative">
      <svg
        width={width}
        height={H}
        role="img"
        aria-labelledby={titleId}
        tabIndex={0}
        className="block touch-none"
        onPointerMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect()
          const px = e.clientX - rect.left
          const i = points.length === 1 ? 0 : Math.round(((px - PAD.left) / plotW) * (points.length - 1))
          setHover(Math.min(Math.max(i, 0), points.length - 1))
        }}
        onPointerLeave={() => setHover(null)}
        onFocus={() => setHover(points.length - 1)}
        onBlur={() => setHover(null)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowLeft') setHover((h) => Math.max((h ?? points.length) - 1, 0))
          else if (e.key === 'ArrowRight') setHover((h) => Math.min((h ?? -1) + 1, points.length - 1))
          else return
          e.preventDefault()
        }}
      >
        <title id={titleId}>
          {trend.metric} over {points.length} runs of {trend.suite}; use the arrow keys to read each run
        </title>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} stroke="var(--rule)" strokeWidth={1} />
            <text x={PAD.left - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--muted)">
              {t.toFixed(decimals)}
            </text>
          </g>
        ))}
        {events.map((ev, k) => (
          <g key={`${ev.kind}:${ev.index}:${k}`}>
            <line
              x1={x(ev.index)}
              x2={x(ev.index)}
              y1={PAD.top - 6}
              y2={H - PAD.bottom}
              stroke={ev.kind === 'baseline' ? 'var(--ink-2)' : 'var(--muted)'}
              strokeWidth={1}
            />
            <text
              // labels in the right third end at their rule, so they never run off the edge
              x={x(ev.index) > width * 0.66 ? x(ev.index) - 4 : x(ev.index) + 4}
              textAnchor={x(ev.index) > width * 0.66 ? 'end' : 'start'}
              y={ev.kind === 'baseline' ? PAD.top - 10 : PAD.top + 16}
              fontSize={11}
              fill="var(--ink-2)"
            >
              {ev.label}
            </text>
          </g>
        ))}
        <path d={path} fill="none" stroke="var(--ink)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {hovered && (
          <line x1={x(hover!)} x2={x(hover!)} y1={PAD.top} y2={H - PAD.bottom} stroke="var(--rule-strong)" strokeWidth={1} />
        )}
        {points.map((p, i) => (
          <circle
            key={p.run_id}
            cx={x(i)}
            cy={y(p.value)}
            r={p.baseline ? 5 : 4}
            fill={p.baseline ? 'var(--surface)' : 'var(--ink)'}
            stroke={p.baseline ? 'var(--ink)' : 'var(--surface)'}
            strokeWidth={2}
          />
        ))}
        <text x={PAD.left} y={H - 8} fontSize={11} fill="var(--muted)">
          {points[0] ? when(points[0].started_at) : ''}
        </text>
        <text x={width - PAD.right} y={H - 8} fontSize={11} fill="var(--muted)" textAnchor="end">
          {points.at(-1) ? when(points.at(-1)!.started_at) : ''}
        </text>
      </svg>
      {hovered && (
        <div
          role="status"
          className="pointer-events-none absolute z-10 rounded border border-rule-strong bg-raised px-2.5 py-1.5 text-[12px] shadow-[0_4px_14px_rgba(0,0,0,0.12)]"
          style={{
            left: Math.min(Math.max(x(hover!) + 10, 0), width - 200),
            top: Math.max(y(hovered.value) - 60, 0),
          }}
        >
          <p className="text-[14px] font-semibold text-ink">{fmt(hovered.value)}</p>
          <p className="text-ink-2">
            {hovered.config}
            {hovered.baseline ? ' · baseline' : ''} · dataset {hovered.dataset_version}
          </p>
          <p className="font-mono text-ink-2">
            {when(hovered.started_at)} · {runTag(hovered.run_id)}
            {hovered.git_sha ? ` · ${shortSha(hovered.git_sha)}` : ''}
          </p>
        </div>
      )}
    </div>
  )
}
