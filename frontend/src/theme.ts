import { useEffect, useState } from 'react'

export type ThemePref = 'system' | 'light' | 'dark'
export type Theme = 'light' | 'dark'

const KEY = 'rh.theme'
const readPref = (): ThemePref => {
  try { const v = localStorage.getItem(KEY); return v === 'light' || v === 'dark' ? v : 'system' } catch { return 'system' }
}

/** Light / dark / system. index.html sets data-theme before first paint; this keeps it in sync afterwards. */
export function useTheme() {
  const [pref, setPrefState] = useState<ThemePref>(readPref)
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'))
  useEffect(() => {
    const mq = matchMedia('(prefers-color-scheme: dark)')
    const apply = () => {
      const t: Theme = pref === 'system' ? (mq.matches ? 'dark' : 'light') : pref
      document.documentElement.dataset.theme = t
      document.documentElement.style.colorScheme = t
      setTheme(t)
    }
    apply()
    if (pref !== 'system') return
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [pref])
  const setPref = (p: ThemePref) => {
    try { if (p === 'system') { localStorage.removeItem(KEY) } else { localStorage.setItem(KEY, p) } } catch { /* storage unavailable */ }
    setPrefState(p)
  }
  return { pref, theme, setPref }
}

/* Canvas colours for the company map. CSS can't reach inside the canvas, so each theme has its own set
   (every node colour is at least 3:1 against that theme's background). */
const LABELS_DARK: Record<string, string> = {
  Service: '#6ea8fe', Person: '#e9b872', Team: '#b39ddb', Incident: '#f0727a', Customer: '#6fcf97',
  Product: '#4fd1c5', Runbook: '#f2a65a', Doc: '#8b95a7', Message: '#7a8599', Ticket: '#9aa4b5',
  Decision: '#e58fc1', Lesson: '#f5d36b', Session: '#7d8699', Task: '#7fb2ff', Agent: '#c4a7ff', Page: '#22d3ee',
  Alert: '#ff6b6b', Project: '#a3e635', Fix: '#34d399',
}
const LABELS_LIGHT: Record<string, string> = {
  Service: '#2f6fd6', Person: '#a8650c', Team: '#7b55c7', Incident: '#d23f48', Customer: '#1e8a55',
  Product: '#0f857c', Runbook: '#c0571a', Doc: '#66707f', Message: '#6b7686', Ticket: '#6f7989',
  Decision: '#b8478a', Lesson: '#977500', Session: '#6b7385', Task: '#3c76d6', Agent: '#8a63e0', Page: '#0e7490',
  Alert: '#e03131', Project: '#4d7c0f', Fix: '#047857',
}
const AGENTS_DARK: Record<string, string> = { commander: '#a78bfa', investigator: '#38bdf8', historian: '#f5a524' }
const AGENTS_LIGHT: Record<string, string> = { commander: '#6b46c1', investigator: '#0369a1', historian: '#9a5b00' }

export type CanvasPalette = {
  labels: Record<string, string>; agents: Record<string, string>
  link: string; linkDim: string; linkLit: string; labelBg: string; labelText: string; labelTextLit: string; down: string
}
export const CANVAS: Record<Theme, CanvasPalette> = {
  dark: {
    labels: LABELS_DARK, agents: AGENTS_DARK, link: 'rgba(148,163,184,0.18)', linkDim: 'rgba(148,163,184,0.05)',
    linkLit: 'rgba(230,233,238,0.9)', labelBg: 'rgba(14,17,22,0.88)', labelText: '#c9d0da', labelTextLit: '#ffffff', down: '#f38187',
  },
  light: {
    labels: LABELS_LIGHT, agents: AGENTS_LIGHT, link: 'rgba(61,69,82,0.22)', linkDim: 'rgba(61,69,82,0.06)',
    linkLit: 'rgba(21,26,33,0.85)', labelBg: 'rgba(252,252,253,0.92)', labelText: '#3d4552', labelTextLit: '#151a21', down: '#b02a33',
  },
}

export const ekey = (s: string, t: string, d: string) => `${s}|${t}|${d}`
