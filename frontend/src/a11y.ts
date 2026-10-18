import { useEffect, useRef, useState } from 'react'

const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'

/** Modal dialog behaviour: focus moves in, Tab stays inside, Escape closes, focus returns to where it was. */
export function useDialog<T extends HTMLElement>(open: boolean, onClose: () => void) {
  const ref = useRef<T>(null)
  const close = useRef(onClose)
  useEffect(() => { close.current = onClose })
  useEffect(() => {
    if (!open) return
    const before = document.activeElement as HTMLElement | null
    const el = ref.current
    ;(el?.querySelector<HTMLElement>('[data-autofocus]') ?? el)?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); close.current(); return }
      if (e.key !== 'Tab' || !el) return
      const items = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((n) => n.offsetParent !== null)
      if (!items.length) return
      const first = items[0], last = items[items.length - 1]
      if (e.shiftKey && (document.activeElement === first || document.activeElement === el)) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); before?.focus?.() }
  }, [open])
  return ref
}

/** Arrow-key movement between tabs in a role="tablist" (WAI-ARIA tabs pattern). */
export function onTabKeys(e: React.KeyboardEvent<HTMLElement>) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return
  const tabs = [...e.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]')].filter((n) => n.offsetParent !== null)
  const i = tabs.indexOf(document.activeElement as HTMLElement)
  if (i < 0) return
  e.preventDefault()
  const next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
  tabs[next].focus(); tabs[next].click()
}

/** Menu popover: focus the first item, Up/Down to move, Escape or a click outside closes and returns focus. */
export function usePopover<T extends HTMLElement>(open: boolean, setOpen: (v: boolean) => void) {
  const ref = useRef<T>(null)
  useEffect(() => {
    if (!open) return
    const el = ref.current
    el?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled)')?.focus()
    const click = (e: MouseEvent) => { if (!el?.contains(e.target as Node)) setOpen(false) }
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpen(false); el?.querySelector<HTMLElement>('[aria-haspopup]')?.focus(); return }
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
      const items = [...(el?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? [])]
      if (!items.length) return
      e.preventDefault()
      const i = items.indexOf(document.activeElement as HTMLElement)
      items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus()
    }
    document.addEventListener('mousedown', click)
    document.addEventListener('keydown', key)
    return () => { document.removeEventListener('mousedown', click); document.removeEventListener('keydown', key) }
  }, [open, setOpen])
  return ref
}

export type Layout = 'mobile' | 'tablet' | 'desktop'
const layoutNow = (): Layout => (innerWidth < 768 ? 'mobile' : innerWidth < 1180 ? 'tablet' : 'desktop')

/** Which layout the CSS is showing (breakpoints match index.css). */
export function useLayout() {
  const [layout, setLayout] = useState<Layout>(layoutNow)
  useEffect(() => {
    const on = () => setLayout(layoutNow())
    addEventListener('resize', on)
    return () => removeEventListener('resize', on)
  }, [])
  return layout
}
