import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { Empty, ErrorPanel, Scroll, Section, Skeleton, Tag } from '@/components/ui'
import { useData } from '@/data/context'
import { shortHash } from '@/lib/format'

export const Route = createFileRoute('/datasets/')({ component: DatasetsPage })

function DatasetsPage() {
  const source = useData()
  const datasets = useQuery({ queryKey: ['datasets'], queryFn: () => source.listDatasets() })
  return (
    <Section title="Datasets">
      {datasets.isPending ? (
        <Skeleton />
      ) : datasets.isError ? (
        <ErrorPanel error={datasets.error} />
      ) : datasets.data.length === 0 ? (
        <Empty title="No datasets in this evaluation root">
          A dataset is a directory under <code className="font-mono">datasets/</code> with a{' '}
          <code className="font-mono">dataset.yaml</code> and a <code className="font-mono">cases.jsonl</code>.
        </Empty>
      ) : (
        <Scroll>
          <table className="w-full border-collapse text-left">
            <thead className="text-[12px] text-ink-2">
              <tr className="border-b border-rule">
                <th className="px-3 py-1.5 font-medium">Name</th>
                <th className="px-2 py-1.5 font-medium">Version</th>
                <th className="px-2 py-1.5 text-right font-medium">Cases</th>
                <th className="px-2 py-1.5 font-medium">Pack</th>
                <th className="px-2 py-1.5 font-medium">License</th>
                <th className="px-2 py-1.5 font-medium">Hash</th>
                <th className="px-3 py-1.5 font-medium">Description</th>
              </tr>
            </thead>
            <tbody>
              {datasets.data.map((d) => (
                <tr key={d.name} className="border-b border-rule last:border-b-0 hover:bg-hover">
                  <td className="px-3 py-1.5">
                    <Link to="/datasets/$name" params={{ name: d.name }} className="font-medium hover:underline">
                      {d.name}
                    </Link>{' '}
                    {d.synthetic && <Tag>synthetic</Tag>}
                  </td>
                  <td className="px-2 py-1.5 font-mono text-[12px]">{d.version}</td>
                  <td className="px-2 py-1.5 text-right">{d.n_cases}</td>
                  <td className="px-2 py-1.5 text-ink-2">{d.pack ?? '—'}</td>
                  <td className="px-2 py-1.5 text-ink-2">{d.license ?? '—'}</td>
                  <td className="px-2 py-1.5 font-mono text-[12px] text-ink-2" title={d.hash}>
                    {shortHash(d.hash)}
                  </td>
                  <td className="max-w-[40ch] truncate px-3 py-1.5 text-ink-2" title={d.description}>
                    {d.description || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Scroll>
      )}
    </Section>
  )
}
