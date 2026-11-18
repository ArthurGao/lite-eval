import { useQuery } from '@tanstack/react-query'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { Flag } from 'lucide-react'
import { useState } from 'react'
import type { DatasetCase } from '@/client/types.gen'
import { AnnotateDialog, categoryWord } from '@/components/annotate'
import { Tags } from '@/components/cases'
import { Value } from '@/components/detail'
import { Button, Empty, Loaded, Pager, Section, Tag } from '@/components/ui'
import { useData } from '@/data/context'
import { datasetCasesQuery } from '@/data/queries'
import { shortHash } from '@/lib/format'

const LIMIT = 50

export const Route = createFileRoute('/datasets/$name')({
  validateSearch: (s: Record<string, unknown>): { offset?: number } => ({
    offset: typeof s.offset === 'number' && s.offset > 0 ? s.offset : undefined,
  }),
  component: DatasetPage,
})

/**
 * Browse a dataset's cases and flag a wrong label (design §18.9): the flag is an annotation, the
 * dataset file is never changed; fixing the label is an edit in git, which changes the hash.
 */
function DatasetPage() {
  const { name } = Route.useParams()
  const { offset = 0 } = Route.useSearch()
  const navigate = useNavigate({ from: '/datasets/$name' })
  const source = useData()
  const page = useQuery(datasetCasesQuery(source, name, offset, LIMIT))
  const [flagging, setFlagging] = useState<DatasetCase | null>(null)

  return (
    <Loaded query={page} rows={10}>
      {(data) => {
        const d = data.dataset

        return (
          <div className="space-y-4">
            <header className="rounded border border-rule bg-surface px-3 py-2.5">
              <h1 className="flex flex-wrap items-baseline gap-2 text-[16px] font-semibold">
                {d.name}
                <span className="font-mono text-[12px] font-normal text-ink-2">@{d.version}</span>
                {d.synthetic && <Tag>synthetic</Tag>}
              </h1>
              <p className="mt-1 text-ink-2">
                {d.n_cases} cases · {d.license ?? 'no license recorded'} ·{' '}
                <span className="font-mono text-[12px]" title={d.hash}>
                  {shortHash(d.hash)}
                </span>{' '}
                · <span className="font-mono text-[12px]">{d.path}</span>
              </p>
              {d.description && <p className="mt-1 max-w-[80ch]">{d.description}</p>}
            </header>

            <Section title="Cases">
              {data.items.length === 0 ? (
                <Empty title="No cases on this page" />
              ) : (
                <ul className="divide-y divide-rule">
                  {data.items.map((c) => (
                    <li key={c.case.id} className="grid gap-3 px-3 py-2.5 lg:grid-cols-[10rem_minmax(0,1fr)_minmax(0,1fr)_9rem]">
                      <div>
                        <p className="font-mono text-[12px] font-medium">{c.case.id}</p>
                        <p className="text-[12px] text-ink-2">
                          <Tags tags={c.case.tags} />
                        </p>
                        {c.case.provenance?.source && (
                          <p className="text-[12px] text-ink-2" title="Provenance">
                            {c.case.provenance.source}
                          </p>
                        )}
                      </div>
                      <div className="min-w-0">
                        <p className="text-[12px] text-ink-2">Input</p>
                        <Value value={c.case.input} />
                      </div>
                      <div className="min-w-0">
                        <p className="text-[12px] text-ink-2">Expected</p>
                        <Value value={c.case.expected} />
                      </div>
                      <div className="flex flex-col items-start gap-1">
                        {c.annotation && <Tag>{categoryWord(c.annotation.category)}</Tag>}
                        {source.mode === 'live' && (
                          <Button onClick={() => setFlagging(c)} aria-label={`Label wrong: ${c.case.id}`}>
                            <Flag aria-hidden className="size-3.5" />
                            Label wrong
                          </Button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <Pager offset={offset} limit={LIMIT} total={data.total} onChange={(o) => void navigate({ search: { offset: o || undefined } })} />
            </Section>

            {flagging && (
              <AnnotateDialog
                key={flagging.case.id}
                open
                onOpenChange={(open) => !open && setFlagging(null)}
                initial="label_wrong"
                current={flagging.annotation}
                target={{ dataset: d.name, case_id: flagging.case.id, case_hash: flagging.case_hash, run_id: null }}
              />
            )}
          </div>
        )
      }}
    </Loaded>
  )
}
