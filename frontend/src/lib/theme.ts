export type Theme = 'system' | 'light' | 'dark'

const KEY = 'lite-eval.theme'

export function readTheme(): Theme {
  try {
    const t = localStorage.getItem(KEY)
    return t === 'light' || t === 'dark' ? t : 'system'
  } catch {
    return 'system' // storage blocked (an exported file opened from disk may be)
  }
}

/** Set the theme on <html>; "system" follows prefers-color-scheme. Remembered per viewer. */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement
  if (theme === 'system') delete root.dataset.theme
  else root.dataset.theme = theme
  try {
    localStorage.setItem(KEY, theme)
  } catch {
    // a per-viewer convenience only
  }
}
