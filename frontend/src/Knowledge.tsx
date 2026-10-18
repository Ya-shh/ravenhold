import { useState } from 'react'
import { useDialog } from './a11y'
import type { GEdge, GNode } from './api'

type Result = { changes: { id: string; kind: string; summary: string }[]; skipped: string[]; source_id: string; facts_found: number; subgraph: { nodes: GNode[]; edges: GEdge[] } }

const EXAMPLES = [
  'Sarah is moving to the Data team starting today.',
  'Sarah takes over TKT-512 and joins the PgBouncer pool hardening project.',
  'James takes over Payments on-call this week.',
  'Decision from Fiona: we will move session-cache to a managed Redis cluster.',
  'From now on the Identity team owns kyc-service. Someone needs to add pool-saturation alerts for pgbouncer; Daniel will own it.',
]

/** Company Brain: paste what people wrote; Ravenhold updates the map and shows exactly what changed, with the source. */
export function KnowledgeModal({ open, onClose, tenant, onApplied }: {
  open: boolean; onClose: () => void; tenant: string; onApplied: (sg: { nodes: GNode[]; edges: GEdge[] }) => void
}) {
  const [text, setText] = useState('')
  const [kind, setKind] = useState<'chat' | 'doc'>('chat')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<Result | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const ref = useDialog<HTMLDivElement>(open, onClose)
  if (!open) return null
  const visitor = (() => { try { return localStorage.getItem('rh.visitor') ?? '' } catch { return '' } })()

  const submit = async () => {
    setBusy(true); setErr(null); setRes(null)
    try {
      const r = await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/ingest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Visitor': visitor },
        body: JSON.stringify({ tenant, text, kind }),
      })
      const j = await r.json()
      if (!r.ok) throw new Error(j.detail ?? `failed: ${r.status}`)
      setRes(j)
      if (j.subgraph) onApplied(j.subgraph)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal know" role="dialog" aria-modal="true" aria-labelledby="know-title" tabIndex={-1} ref={ref} onMouseDown={(e) => e.stopPropagation()}>
        <button className="modal-x" onClick={onClose} aria-label="Close"><svg viewBox="0 0 16 16" aria-hidden><path d="M4 4l8 8M12 4l-8 8" /></svg></button>
        <div className="eyebrow">Company brain</div>
        <h2 id="know-title">Teach Ravenhold what changed</h2>
        <p className="modal-lead">Paste a chat message, meeting note or document. Ravenhold reads it, updates the company map, and
          shows exactly what changed, with a link back to what you pasted. Old facts are closed, not deleted, so history is kept.</p>
        <div className="kind-toggle" role="group" aria-label="What are you pasting?">
          <button className={kind === 'chat' ? 'on' : ''} aria-pressed={kind === 'chat'} onClick={() => setKind('chat')}>Chat message</button>
          <button className={kind === 'doc' ? 'on' : ''} aria-pressed={kind === 'doc'} onClick={() => setKind('doc')}>Document</button>
        </div>
        <label htmlFor="know-text" className="sr-only">Text to add</label>
        <textarea id="know-text" data-autofocus className="know-text" rows={5} value={text} onChange={(e) => setText(e.target.value)}
          placeholder="e.g. Sarah is moving to the Data team starting today." />
        <div className="know-examples">
          <span className="dim small">Try:</span>
          {EXAMPLES.map((x) => <button key={x} onClick={() => setText(x)}>{x.length > 48 ? x.slice(0, 46) + '…' : x}</button>)}
        </div>
        <div className="ap-actions">
          <button className="btn primary" onClick={submit} disabled={busy || text.trim().length < 3}>
            {busy ? 'Reading…' : 'Update the map'} <span className="price">under $0.01</span>
          </button>
          <span className="dim small">Uses the small, cheap model. Only ids already in the map are used; nothing is invented.</span>
        </div>
        {err && <div className="error inline" role="alert">{err}</div>}
        {res && (
          <div className="know-result" role="status">
            <h4>{res.changes.length ? `${res.changes.length} change${res.changes.length > 1 ? 's' : ''} made` : 'Nothing to change'}
              <span className="dim"> · saved as source {res.source_id}</span></h4>
            <ol>{res.changes.map((c) => <li key={c.id}><span className="mono dim">{c.id}</span> {c.summary}</li>)}</ol>
            {res.skipped.length > 0 && <div className="dim tiny">Skipped: {res.skipped.join(' · ')}</div>}
            <div className="dim tiny">The changes are highlighted on the map. Ask the agents about them, e.g. "Who is on call for Payments now?"</div>
          </div>
        )}
      </div>
    </div>
  )
}
