import { CircleCheck, CircleX } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import type { Output, Score, Trace } from '@/client/types.gen'
import { cn } from '@/lib/cn'
import { metric, ms } from '@/lib/format'
import { layoutSpans } from '@/lib/verdict'

const FOLD = 24 // lines shown before a long value collapses

type Hit = { text?: unknown; doc?: unknown; id?: unknown; score?: unknown; title?: unknown }

function isHits(value: unknown): value is Hit[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((v) => v && typeof v === 'object' && !Array.isArray(v) && ('text' in v || 'doc' in v))
  )
}

function Folded({ text, mono = false }: { text: string; mono?: boolean }) {
  const lines = text.split('\n')
  const [open, setOpen] = useState(lines.length <= FOLD)
  return (
    <div>
      <pre
        className={cn(
          'whitespace-pre-wrap break-words text-[13px] text-ink',
          mono ? 'font-mono text-[12px]' : 'font-sans',
        )}
      >
        {open ? text : lines.slice(0, FOLD).join('\n')}
      </pre>
      {!open && (
        <button type="button" onClick={() => setOpen(true)} className="mt-1 text-[12px] text-ink-2 underline">
          Show all {lines.length} lines
        </button>
      )}
    </div>
  )
}

/** Any JSON value, readably: text as text, retrieval hits as a ranked list, the rest as JSON. */
export function Value({ value }: { value: unknown }) {
  if (value == null) return <span className="text-muted">none</span>
  if (typeof value === 'string') return <Folded text={value} />
  if (isHits(value)) {
    return (
      <ol className="space-y-2">
        {value.map((h, i) => (
          <li key={i} className="grid grid-cols-[2rem_minmax(0,1fr)] gap-x-2">
            <span className="text-right font-mono text-[12px] text-muted">{i + 1}</span>
            <div className="min-w-0">
              <p className="text-[12px] text-ink-2">
                <span className="font-mono">{String(h.doc ?? h.id ?? '')}</span>
                {typeof h.score === 'number' && <span className="ml-2">score {h.score.toFixed(3)}</span>}
              </p>
              {h.text != null && <Folded text={String(h.text)} />}
            </div>
          </li>
        ))}
      </ol>
    )
  }
  return <Folded text={JSON.stringify(value, null, 2)} mono />
}

/** Input, expected and output side by side; on narrow screens they stack. */
export function SideBySide({ panes }: { panes: { title: string; note?: ReactNode; body: ReactNode }[] }) {
  return (
    <div className="grid gap-px overflow-hidden rounded border border-rule bg-rule lg:grid-cols-3">
      {panes.map((p) => (
        <section key={p.title} className="min-w-0 bg-surface">
          <header className="flex items-baseline justify-between gap-2 border-b border-rule px-3 py-1.5">
            <h3 className="font-semibold text-ink">{p.title}</h3>
            {p.note && <span className="text-[12px] text-ink-2">{p.note}</span>}
          </header>
          <div className="max-h-[560px] overflow-auto px-3 py-2">{p.body}</div>
        </section>
      ))}
    </div>
  )
}

/** The trace as bars on one time axis; nested spans indent one row level. */
export function TraceTimeline({ trace }: { trace: Trace }) {
  const spans = trace.spans ?? []
  if (spans.length === 0) {
    return (
      <p className="px-3 py-2 text-ink-2">
        No spans recorded. Latency {ms(trace.latency_ms)}
        {trace.latency_scope ? ` (${trace.latency_scope})` : ''}.
      </p>
    )
  }
  const { total, bars } = layoutSpans(spans, trace.latency_ms)
  return (
    <div className="px-3 py-2">
      <div className="mb-1 flex justify-between font-mono text-[11px] text-muted">
        <span>0 ms</span>
        <span>{ms(total)}</span>
      </div>
      <ol className="space-y-1">
        {bars.map((b, i) => (
          <li key={i} className="grid grid-cols-[minmax(8rem,14rem)_minmax(0,1fr)_5rem] items-center gap-2">
            <span className="truncate text-[12px]" style={{ paddingLeft: `${b.depth * 12}px` }} title={b.span.name}>
              <span className="text-ink-2">{b.span.kind}</span> {b.span.name}
            </span>
            <span className="relative h-3 rounded-sm bg-hover">
              <span
                className="absolute inset-y-0 rounded-sm bg-ink-2"
                style={{ left: `${b.left}%`, width: `${b.width}%` }}
              />
            </span>
            <span className="text-right font-mono text-[11px] text-ink-2">{ms(b.span.duration_ms)}</span>
          </li>
        ))}
      </ol>
      {trace.latency_scope && <p className="mt-2 text-[12px] text-ink-2">Latency covers: {trace.latency_scope}</p>}
    </div>
  )
}

/** A scorer's own pass / miss: icon + word, the same words the case uses. */
export function PassMark({ passed }: { passed: boolean }) {
  const Icon = passed ? CircleCheck : CircleX
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap text-ink-2">
      <Icon aria-hidden className={cn('size-3.5', passed ? 'text-neutral-mark' : 'text-worse-mark')} />
      {passed ? 'pass' : 'miss'}
    </span>
  )
}

/** Why a case is a miss: each failing or erroring scorer with its version and reason. */
export function FailingScores({ scores, output }: { scores: Score[]; output?: Output }) {
  const failing = scores.filter((s) => s.passed === false || s.error)
  if (failing.length === 0 && !output?.error) return null
  return (
    <ul className="divide-y divide-rule rounded border border-rule bg-surface">
      {output?.error && (
        <li className="flex flex-wrap items-baseline gap-x-2 px-3 py-1.5">
          <PassMark passed={false} />
          <span className="font-medium">output</span>
          <span className="text-ink-2">
            {output.error.kind.replaceAll('_', ' ')}: {output.error.message}
          </span>
        </li>
      )}
      {failing.map((s) => (
        <li key={`${s.scorer}:${s.repeat ?? 0}`} className="flex flex-wrap items-baseline gap-x-2 px-3 py-1.5">
          <PassMark passed={false} />
          <span className="font-medium">{s.scorer}</span>
          <span className="font-mono text-[11px] text-muted">v{s.scorer_version}</span>
          <span className="font-medium">{metric(s.value)}</span>
          <span className="min-w-0 text-ink-2">{s.error ? `${s.error.kind.replaceAll('_', ' ')}: ${s.error.message}` : s.reason}</span>
        </li>
      ))}
    </ul>
  )
}

export function ScoreCard({ score }: { score: Score }) {
  return (
    <article className="border-b border-rule px-3 py-2 last:border-b-0">
      <header className="flex flex-wrap items-baseline gap-x-3">
        <h4 className="font-medium text-ink">{score.scorer}</h4>
        <span className="font-mono text-[11px] text-muted">v{score.scorer_version}</span>
        <span className="ml-auto font-medium">{metric(score.value)}</span>
        {score.passed != null && <PassMark passed={score.passed} />}
      </header>
      {score.error && (
        <p className="mt-1 text-worse-text">
          {score.error.kind.replaceAll('_', ' ')}: {score.error.message}
        </p>
      )}
      {score.reason && <p className="mt-1 text-ink-2">{score.reason}</p>}
      {score.evidence != null && (
        <details className="mt-1">
          <summary className="cursor-pointer text-[12px] text-ink-2">Evidence</summary>
          <Value value={score.evidence} />
        </details>
      )}
      {score.judge && (
        <details className="mt-1">
          <summary className="cursor-pointer text-[12px] text-ink-2">
            Judge {score.judge.role} · {score.judge.model} · prompt {score.judge.prompt_version}
          </summary>
          <Value value={score.judge.raw} />
        </details>
      )}
    </article>
  )
}

export function OutputBody({ output }: { output: Output | undefined }) {
  if (!output) return <span className="text-worse-text">Never generated (the run stopped before this case).</span>
  if (output.error) {
    return (
      <div className="space-y-2">
        <p className="text-worse-text">
          {output.error.kind.replaceAll('_', ' ')}: {output.error.message}
        </p>
        {output.value != null && <Value value={output.value} />}
      </div>
    )
  }
  return <Value value={output.value} />
}

/** When the dataset no longer holds the content the run saw (design §18.9). */
export function ContentNote({ status }: { status: 'current' | 'changed' | 'missing' }) {
  if (status === 'current') return null
  return (
    <p role="note" className="rounded border border-warn-mark/40 bg-warn-wash px-3 py-2 text-warn-text">
      {status === 'changed'
        ? "The dataset's content for this case changed after this run: input and expected show today's content, not what the run saw."
        : 'This case is no longer in the dataset, so its input and expected answer cannot be shown.'}
    </p>
  )
}
