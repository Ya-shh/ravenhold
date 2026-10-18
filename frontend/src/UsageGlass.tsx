import { useEffect, useRef, useState } from 'react'
import type { Usage } from './api'

const MODEL_NAMES: Record<string, string> = {
  'claude-sonnet-5-5': 'Claude Sonnet 5.5', 'claude-haiku-5-5': 'Claude Haiku 5.5', 'claude-opus-5-5': 'Claude Opus 5.5',
  'openai/gpt-oss-120b': 'gpt-oss-120b', 'openai/gpt-oss-20b': 'gpt-oss-20b',
}
const ROLE_NAMES: Record<string, string> = { commander: 'Commander', worker: 'Investigator + Historian', earlier: 'Earlier runs' }
const nice = (m: string) => MODEL_NAMES[m] ?? m
export const modelName = nice
export const roleName = (a: string) => ({ commander: 'Commander', investigator: 'Investigator', historian: 'Historian' } as Record<string, string>)[a] ?? a
const usd = (n: number) => `$${n.toFixed(n < 0.1 && n > 0 ? 3 : 2)}`
const short = (m: string) => nice(m).replace('Claude ', '')

/** Floating spend control: spend vs budget, which models run, and where every cent goes. */
export function UsageGlass({ usage, lastRunUsd, tick, onOpenTab }: {
  usage: Usage | null; lastRunUsd: number | null
  tick: { key: number; usd: number; agent: string } | null; onOpenTab: () => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc) }
  }, [open])

  if (!usage) return null
  const pct = Math.min(1, usage.spent_usd / Math.max(usage.budget_usd, 0.01))
  const warn = pct > 0.8
  const R = 9, C = 2 * Math.PI * R
  const roles = Object.entries(usage.by_role).sort((a, b) => b[1].usd - a[1].usd)
  const models = Object.entries(usage.by_model).sort((a, b) => b[1].usd - a[1].usd)
  const maxRole = Math.max(...roles.map(([, v]) => v.usd), 0.0001)

  return (
    <div className="usage-anchor" ref={ref}>
      {open && (
        <div className="glass-panel" id="usage-panel" role="dialog" aria-label="Credit usage">
          <div className="gp-head">
            <span>Shared demo budget</span>
            <span className={`gp-total ${warn ? 'warn' : ''}`}>{usd(usage.spent_usd)} <em>of {usd(usage.budget_usd)}</em></span>
          </div>
          <div className="gp-bar"><i style={{ width: `${pct * 100}%` }} className={warn ? 'warn' : ''} /></div>
          <div className="gp-sub">
            {usd(usage.remaining_usd)} left before the budget guard pauses new runs
            {lastRunUsd != null && <> · last answer cost <b>{usd(lastRunUsd)}</b></>}
          </div>

          <div className="gp-section">Models in use <span>{usage.models.provider}</span></div>
          <div className="gp-models">
            <div><b>{nice(usage.models.commander)}</b><span>Commander: plans, decides, takes actions</span></div>
            <div><b>{nice(usage.models.workers)}</b><span>Investigator and Historian: graph tool work</span></div>
          </div>

          <div className="gp-section">Where the credits go</div>
          {roles.map(([role, v]) => (
            <div key={role} className="gp-row">
              <span className="gp-label">{ROLE_NAMES[role] ?? role}</span>
              <span className="gp-meter"><i style={{ width: `${(v.usd / maxRole) * 100}%` }} /></span>
              <span className="gp-val">{usd(v.usd)}</span>
            </div>
          ))}

          <div className="gp-section">By model</div>
          <table className="gp-table">
            <thead><tr><th>Model</th><th>Calls</th><th>Tokens in / out</th><th>Cost</th></tr></thead>
            <tbody>
              {models.map(([m, v]) => (
                <tr key={m}>
                  <td>{nice(m)}</td><td>{v.calls}</td>
                  <td>{(v.tokens_in / 1000).toFixed(0)}k / {(v.tokens_out / 1000).toFixed(0)}k</td>
                  <td>{usd(v.usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="gp-foot">
            Costs are computed from the token counts each model call returns, at list prices per million tokens.
            The budget guard is enforced on the server and survives restarts.
          </div>
          <button className="gp-link" onClick={() => { setOpen(false); onOpenTab() }}>See every model call →</button>
        </div>
      )}
      {tick && (
        <span key={tick.key} className="glass-tick" aria-hidden>
          +${tick.usd.toFixed(4)} <em>· {roleName(tick.agent)}</em>
        </span>
      )}
      <button className={`glass-pill ${warn ? 'warn' : ''}`} onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls="usage-panel"
        aria-label={`AI credit used: ${usd(usage.spent_usd)} of ${usd(usage.budget_usd)}. Show details`}>
        <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden>
          <circle cx="12" cy="12" r={R} className="ring-bg" />
          <circle cx="12" cy="12" r={R} className="ring-fg" strokeDasharray={`${C * pct} ${C}`} transform="rotate(-90 12 12)" />
        </svg>
        <span className="gl-amount">{usd(usage.spent_usd)}<em> of {usd(usage.budget_usd)}</em></span>
        <span className="gl-sep" />
        <span className="gl-models">{short(usage.models.commander)} · {short(usage.models.workers)}</span>
      </button>
    </div>
  )
}
