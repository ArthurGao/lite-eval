import { expect, test, type Page } from '@playwright/test'
import { E2E } from '../playwright.config'

// Data: backend tests/e2e/viewer_fixture.py. Suite `demo` on dataset `golden` (12 cases):
// `baseline` misses q03, q07; `candidate` misses q03, q05, q09, q11 and is the suite's baseline;
// `candidate` ran again on dataset version 2.

type Row = { suite: string; run_id: string; config: string; dataset_version: string }

async function run(page: Page, config: string, version = '1'): Promise<Row> {
  const body = (await (await page.request.get('/api/runs')).json()) as { items: Row[] }
  const row = body.items.find((r) => r.config === config && r.dataset_version === version)
  if (!row) throw new Error(`no ${config} run on dataset version ${version}`)
  return row
}

test('runs → compare → the regressed case first → triage it', async ({ page }) => {
  await page.goto('/')
  const rows = page.locator('tbody tr')
  await expect(rows).toHaveCount(3)
  const candidateV1 = rows.filter({ hasText: 'candidate' }).filter({ hasText: '@1' })
  await expect(candidateV1.getByText('baseline', { exact: true })).toBeVisible()

  await rows.filter({ hasText: /^.*baseline.*golden@1/ }).filter({ hasNotText: 'candidate' }).getByRole('checkbox').check()
  await candidateV1.getByRole('checkbox').check()
  await page.getByRole('button', { name: /Compare 2\/2/ }).click()

  // The suite's baseline (candidate) is the comparison's baseline; baseline-the-config regressed q07.
  await expect(page.getByRole('heading', { level: 1 })).toContainText('candidate')
  const metric = page.getByRole('row', { name: /^exact_match/ })
  await expect(metric).toContainText('0.667')
  await expect(metric).toContainText('0.833')
  const changes = page.locator('section', { hasText: 'Cases that changed' }).locator('tbody tr')
  await expect(changes).toHaveCount(4)
  await expect(changes.first()).toContainText('q07')
  await expect(changes.first()).toContainText('regressed') // regressions before fixes
  await expect(changes.nth(1)).toContainText('fixed')

  await changes.first().getByRole('link', { name: 'q07' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'q07' })).toBeVisible()
  await expect(page.getByText('miss', { exact: true }).first()).toBeVisible()

  await page.keyboard.press('t')
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await page.keyboard.press('r')
  await expect(dialog.getByRole('radio', { name: /Real miss/ })).toBeChecked()
  await dialog.getByRole('textbox').fill('retriever never returns the document')
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).toBeHidden()
  await expect(page.locator('section', { hasText: 'Triage' }).first()).toContainText('real miss')
  await expect(page.getByText('retriever never returns the document')).toBeVisible()
})

test('a leaked key is redacted in the Viewer', async ({ page }) => {
  const r = await run(page, 'candidate')
  await page.goto(`/#/runs/${r.suite}/${r.run_id}/cases/q11`)
  await expect(page.getByText('«redacted:api_keys»')).toBeVisible()
  await expect(page.locator('body')).not.toContainText('sk-E2E')
})

test('triage page: J moves, L opens the dialog on "label wrong", saving counts it', async ({ page }) => {
  const r = await run(page, 'candidate')
  await page.goto(`/#/runs/${r.suite}/${r.run_id}/triage`)
  await expect(page.getByText(/0 of 4 triaged|1 of 4 triaged/)).toBeVisible()
  const before = await page.getByText(/of 4 triaged/).textContent()
  await page.keyboard.press('j')
  await page.keyboard.press('l')
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('radio', { name: /Label wrong/ })).toBeChecked()
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).toBeHidden()
  const n = Number(before?.split(' ')[0])
  await expect(page.getByText(`${n + 1} of 4 triaged`)).toBeVisible()
})

test('datasets: flag a wrong label', async ({ page }) => {
  await page.goto('/#/datasets')
  await page.getByRole('link', { name: 'golden' }).click()
  await expect(page.getByRole('heading', { level: 1 })).toContainText('golden')
  await page.getByRole('button', { name: 'Label wrong: q04' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('radio', { name: /Label wrong/ })).toBeChecked()
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).toBeHidden()
  await expect(page.locator('li', { hasText: 'q04' }).getByText('label wrong', { exact: true })).toBeVisible()
})

test('trends mark the baseline switch and the dataset version change', async ({ page }) => {
  await page.goto('/#/trends?suite=demo&config=candidate&metric=exact_match')
  const chart = page.getByRole('img', { name: /exact_match over 2 runs of demo/ })
  await expect(chart).toBeVisible()
  await expect(chart.getByText('baseline set')).toBeVisible()
  await expect(chart.getByText('dataset 1 → 2')).toBeVisible()
  await expect(page.getByRole('row', { name: /candidate.*baseline/ }).getByText('baseline', { exact: true })).toBeVisible()
})

async function openOffline(page: Page, file: string): Promise<string[]> {
  const fetched: string[] = []
  page.on('request', (req) => {
    const url = req.url()
    if (!url.startsWith('file:') && !url.startsWith('data:')) fetched.push(url)
  })
  await page.route(/^https?:/, (route) => route.abort())
  await page.goto(`file://${E2E}/exports/${file}`)
  return fetched
}

test('an exported run opens from disk on its run, with no server, case text inert', async ({ page }) => {
  const fetched = await openOffline(page, 'run.html')
  await expect(page.getByText('Exported page · read-only')).toBeVisible()
  await expect(page.getByRole('heading', { level: 1 })).toContainText('candidate') // the start route
  await page.getByRole('link', { name: 'Runs', exact: true }).click()
  await expect(page.locator('tbody tr')).toHaveCount(1)
  await expect(page.getByRole('combobox', { name: 'Status' })).toHaveCount(0) // no filters in an export
  await page.getByRole('link', { name: 'candidate' }).click()
  await page.getByRole('link', { name: 'q01' }).click()
  await expect(page.getByText('</script><script>window.__pwned=1</script>', { exact: false })).toBeVisible()
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined()
  await page.getByRole('link', { name: 'Next case' }).click() // q02
  await page.goBack()
  expect(fetched).toEqual([])
})

test('an exported comparison opens on the comparison, offline', async ({ page }) => {
  const fetched = await openOffline(page, 'compare.html')
  await expect(page.getByRole('heading', { name: /Cases that changed/ })).toBeVisible()
  await expect(page.getByRole('row', { name: /^exact_match/ })).toContainText('not significant')
  await expect(page.locator('body')).not.toContainText('sk-E2E')
  expect(fetched).toEqual([])
})

test('the theme follows the system and the toggle overrides it', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.goto('/')
  await page.evaluate(() => localStorage.removeItem('lite-eval.theme'))
  await page.reload()
  const canvas = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  expect(await canvas()).toBe('rgb(15, 18, 21)') // dark canvas #0f1215
  await page.getByRole('button', { name: /Theme: system/ }).click() // system -> light
  expect(await canvas()).toBe('rgb(238, 241, 244)') // light canvas #eef1f4, though the OS is dark
  await page.getByRole('button', { name: /Theme: light/ }).click() // light -> dark
  expect(await canvas()).toBe('rgb(15, 18, 21)')
})
