import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { embeddedSource, liveSource, readEmbedded } from '@/data/source'
import { applyTheme, readTheme } from '@/lib/theme'
import { App } from './App'
import './styles.css'

const root = document.getElementById('root')
if (!root) throw new Error('index.html has no #root element')

applyTheme(readTheme())
const embedded = readEmbedded()

// An exported page opens where the export says (its run, or its comparison).
if (embedded && !window.location.hash) window.location.hash = `#${embedded.start}`

createRoot(root).render(
  <StrictMode>
    <App source={embedded ? embeddedSource(embedded) : liveSource} />
  </StrictMode>,
)
