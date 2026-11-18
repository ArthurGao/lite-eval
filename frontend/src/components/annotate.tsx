import * as Dialog from '@radix-ui/react-dialog'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useState } from 'react'
import type { Annotation } from '@/client/types.gen'
import { useData } from '@/data/context'
import { keys } from '@/data/queries'
import { ApiError } from '@/data/source'
import { cn } from '@/lib/cn'
import { Button, Kbd } from './ui'

export type Category = Annotation['category']

export const CATEGORIES: { value: Category; key: string; label: string; hint: string }[] = [
  { value: 'real_miss', key: 'r', label: 'Real miss', hint: 'The system got it wrong' },
  { value: 'label_wrong', key: 'l', label: 'Label wrong', hint: 'The expected answer is wrong' },
  { value: 'excluded', key: 'e', label: 'Excluded', hint: 'The case should not count' },
  { value: 'unclear', key: 'u', label: 'Unclear', hint: 'Cannot tell yet' },
]

/** "real miss", "label wrong", … — the category's label in running text. */
export function categoryWord(category: Category): string {
  return (CATEGORIES.find((c) => c.value === category)?.label ?? category).toLowerCase()
}

export type AnnotationTarget = { dataset: string; case_id: string; case_hash: string; run_id?: string | null }

const NOTE_BYTES = 4096

/**
 * Classify one case (design §18.9). With a current annotation, the new one corrects it
 * (`supersedes`); a 409 means someone corrected it first, so the dialog says to reload.
 * Keys: R / L / E / U pick, Cmd/Ctrl+Enter saves, Esc closes.
 */
export function AnnotateDialog({
  open,
  onOpenChange,
  target,
  current,
  initial,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  target: AnnotationTarget
  current?: Annotation | null
  initial?: Category
  onSaved?: (a: Annotation) => void
}) {
  const source = useData()
  const queries = useQueryClient()
  const [category, setCategory] = useState<Category | null>(initial ?? current?.category ?? null)
  const [note, setNote] = useState('')
  const save = useMutation({
    mutationFn: () =>
      source.annotate({
        ...target,
        category: category!,
        note,
        supersedes: current?.annotation_id ?? null,
      }),
    onSuccess: (a) => {
      // An annotation changes cases and dataset cases only; runs, comparisons and trends stay.
      // Active queries refetch in the background; the dialog does not wait for them.
      for (const family of [keys.case, keys.cases, keys.datasetCases]) void queries.invalidateQueries({ queryKey: [family] })
      onSaved?.(a)
      onOpenChange(false)
      setNote('')
    },
  })
  const tooLong = new TextEncoder().encode(note).length > NOTE_BYTES
  const readOnly = source.mode === 'embedded'
  const canSave = !readOnly && category != null && !tooLong && !save.isPending

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content
          className="fixed top-[12vh] left-1/2 z-50 w-[min(520px,calc(100vw-32px))] -translate-x-1/2 rounded border border-rule-strong bg-raised shadow-[0_8px_28px_rgba(0,0,0,0.18)]"
          onKeyDown={(e) => {
            if (e.target instanceof HTMLTextAreaElement) {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && canSave) save.mutate()
              return
            }
            const pick = CATEGORIES.find((c) => c.key === e.key.toLowerCase())
            if (pick && !e.metaKey && !e.ctrlKey) {
              setCategory(pick.value)
              e.preventDefault()
            } else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && canSave) save.mutate()
          }}
        >
          <header className="flex items-center justify-between border-b border-rule px-4 py-2.5">
            <Dialog.Title className="font-semibold">
              {current ? 'Correct the triage of' : 'Triage'} <span className="font-mono">{target.case_id}</span>
            </Dialog.Title>
            <Dialog.Close className="rounded p-1 text-ink-2 hover:bg-hover" aria-label="Close">
              <X aria-hidden className="size-4" />
            </Dialog.Close>
          </header>
          <Dialog.Description className="px-4 pt-3 text-ink-2">
            {readOnly
              ? 'This is an exported page: it cannot write. Triage in `lite-eval view`.'
              : 'Saved to annotations.jsonl; runs and the dataset are never changed.'}
          </Dialog.Description>
          <fieldset className="grid grid-cols-2 gap-2 px-4 py-3" disabled={readOnly}>
            <legend className="sr-only">Category</legend>
            {CATEGORIES.map((c) => (
              <label
                key={c.value}
                className={cn(
                  'flex cursor-pointer items-start gap-2 rounded border px-2.5 py-2',
                  category === c.value ? 'border-ink bg-select' : 'border-rule-strong hover:bg-hover',
                )}
              >
                <input
                  type="radio"
                  name="category"
                  value={c.value}
                  checked={category === c.value}
                  onChange={() => setCategory(c.value)}
                  className="mt-0.5 accent-[var(--focus)]"
                />
                <span>
                  <span className="font-medium">{c.label}</span> <Kbd>{c.key.toUpperCase()}</Kbd>
                  <span className="block text-[12px] text-ink-2">{c.hint}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <label className="block px-4">
            <span className="text-[12px] text-ink-2">Note (optional)</span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              disabled={readOnly}
              className="mt-1 block w-full resize-y rounded border border-rule-strong bg-surface px-2 py-1.5 text-ink"
            />
            {tooLong && <span className="text-[12px] text-worse-text">A note is at most 4 KB.</span>}
          </label>
          {save.isError && (
            <p role="alert" className="mx-4 mt-2 rounded bg-worse-wash px-2 py-1.5 text-worse-text">
              {save.error instanceof ApiError && save.error.status === 409
                ? `${save.error.message} Close this and reload the page to see the current triage.`
                : (save.error as Error).message}
            </p>
          )}
          <footer className="mt-3 flex items-center justify-end gap-2 border-t border-rule px-4 py-2.5">
            <span className="mr-auto text-[12px] text-ink-2">
              <Kbd>⌘</Kbd>/<Kbd>Ctrl</Kbd> <Kbd>Enter</Kbd> save
            </span>
            <Dialog.Close asChild>
              <Button>Cancel</Button>
            </Dialog.Close>
            <Button variant="primary" disabled={!canSave} onClick={() => save.mutate()}>
              {save.isPending ? 'Saving…' : 'Save'}
            </Button>
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
