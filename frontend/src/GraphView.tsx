import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from 'react-force-graph-2d'
import type { GEdge, GNode } from './api'
import { CANVAS, ekey, type Theme } from './theme'

const LEGEND = ['Service', 'Person', 'Team', 'Project', 'Incident', 'Alert', 'Page', 'Fix', 'Customer', 'Product', 'Runbook', 'Decision', 'Lesson', 'Task']
const LEGEND_NAME: Record<string, string> = { Page: 'Page request', Fix: 'Fix' }
const PAGE_STATUS: Record<string, string> = { awaiting_approval: 'awaiting approval', sent: 'approved', declined: 'declined', applied: 'applied' }
const LEGEND_KEY = 'rh.legend'
const HIDDEN = new Set(['Episode', 'Turn'])
const HIDDEN_EDGES = new Set(['TOUCHED', 'HAS_TURN', 'PERFORMED', 'FOR_TASK', 'IN_SESSION', 'PREVIOUS'])

type N = NodeObject<GNode>
type LD = { type: string; key: string }
type L = LinkObject<GNode, LD>

export type GraphHandle = { focus: (id: string) => void; fit: () => void }

type Props = {
  nodes: GNode[]
  edges: GEdge[]
  highlight: Map<string, string> // node id -> agent
  highlightEdges: Set<string> // "src|type|dst"
  theme: Theme
}

const idOf = (x: unknown) => (typeof x === 'object' && x !== null ? (x as N).id : (x as string))

export const GraphView = forwardRef<GraphHandle, Props>(function GraphView({ nodes, edges, highlight, highlightEdges, theme }, ref) {
  const pal = CANVAS[theme]
  const fg = useRef<ForceGraphMethods<N, L> | undefined>(undefined)
  const wrap = useRef<HTMLDivElement>(null)
  const cache = useRef(new Map<string, N>())
  const [size, setSize] = useState({ w: 800, h: 600 })
  const [hover, setHover] = useState<string | null>(null)
  const fitted = useRef(false)
  const [legend, setLegend] = useState(() => {
    try { const v = localStorage.getItem(LEGEND_KEY); if (v) return v === '1' } catch { /* storage unavailable */ }
    return window.innerWidth >= 1180
  })
  const toggleLegend = () => setLegend((v) => { try { localStorage.setItem(LEGEND_KEY, v ? '0' : '1') } catch { /* storage unavailable */ } return !v })

  useEffect(() => {
    const el = wrap.current
    if (!el) return
    // A hidden pane (phone/tablet tabs) measures 0x0: keep the last real size, and re-fit once it is shown again.
    let hidden = !el.clientWidth
    const ro = new ResizeObserver(() => {
      if (!el.clientWidth || !el.clientHeight) { hidden = true; return }
      setSize({ w: el.clientWidth, h: el.clientHeight })
      if (hidden) { hidden = false; setTimeout(() => refit.current(), 60) }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Reuse node objects across updates so the layout doesn't jump when the graph grows.
  const data = useMemo(() => {
    const ns: N[] = []
    const ids = new Set<string>()
    for (const n of nodes) {
      if (HIDDEN.has(n.label) || ids.has(n.id)) continue
      ids.add(n.id)
      const prev = cache.current.get(n.id)
      const obj = prev ? Object.assign(prev, n) : ({ ...n } as N)
      cache.current.set(n.id, obj)
      ns.push(obj)
    }
    const ls: L[] = []
    const seen = new Set<string>()
    for (const e of edges) {
      if (HIDDEN_EDGES.has(e.type) || !ids.has(e.source) || !ids.has(e.target)) continue
      const key = ekey(e.source, e.type, e.target)
      if (seen.has(key)) continue
      seen.add(key)
      ls.push({ source: e.source, target: e.target, type: e.type, key })
    }
    return { nodes: ns, links: ls }
  }, [nodes, edges])

  useImperativeHandle(ref, () => ({
    fit() { fitLinked(500) },
    focus(id: string) {
      const n = cache.current.get(id)
      if (n && n.x != null && n.y != null && fg.current) {
        fg.current.centerAt(n.x, n.y, 600)
        fg.current.zoom(3, 600)
        setHover(id)
      }
    },
  }))

  const active = highlight.size > 0

  // Fit to the connected graph only: a few unlinked nodes (agents, sessions) drift outwards and would shrink the view.
  const linked = useMemo(() => {
    const ids = new Set<string>()
    for (const l of data.links) { ids.add(idOf(l.source)); ids.add(idOf(l.target)) }
    return ids
  }, [data.links])
  const FIT_SKIP = new Set(['Task', 'Agent', 'Session'])
  const fitLinked = (ms: number) => fg.current?.zoomToFit(ms, 60, (n) =>
    !FIT_SKIP.has((n as N).label) && (linked.size === 0 || linked.has((n as N).id)))
  // The canvas stops repainting once the layout settles; nudge it so a theme switch shows the new colours.
  useEffect(() => {
    const z = fg.current?.zoom()
    if (z) fg.current?.zoom(z * 1.00001, 0)
  }, [theme])
  const refit = useRef(() => {})
  useEffect(() => { refit.current = () => (highlight.size ? fg.current?.zoomToFit(400, 60, (n) => highlight.has((n as N).id)) : fitLinked(400)) })

  // Fit the whole graph once the first data arrives (the force layout needs a moment to spread out).
  useEffect(() => {
    if (fitted.current || !data.nodes.length) return
    // Fit early so it looks right fast, then again once the layout has settled.
    const t1 = setTimeout(() => fitLinked(500), 900)
    const t2 = setTimeout(() => { fitted.current = true; fitLinked(700) }, 3200)
    return () => { clearTimeout(t1); clearTimeout(t2) }
  }, [data.nodes.length])

  useEffect(() => {
    if (!active || !fg.current) return
    const t = setTimeout(() => {
      fg.current?.zoomToFit(700, 60, (n) => highlight.has((n as N).id))
    }, 250)
    return () => clearTimeout(t)
  }, [highlight, active])

  return (
    <div className="graph-wrap" ref={wrap}>
      <ForceGraph2D<GNode, LD>
        ref={fg}
        width={size.w}
        height={size.h}
        graphData={data}
        backgroundColor="rgba(0,0,0,0)"
        cooldownTicks={120}
        d3VelocityDecay={0.3}
        nodeRelSize={4}
        nodeVal={(n) => (n.label === 'Service' ? 4 - (n.tier ?? 2) : n.label === 'Incident' ? 2.5 : 1.5)}
        onNodeHover={(n) => setHover(n ? n.id : null)}
        onNodeClick={(n) => { setHover(n.id); fg.current?.centerAt(n.x, n.y, 400) }}
        linkColor={(l) => {
          if (highlightEdges.has(l.key)) return pal.linkLit
          return active ? pal.linkDim : pal.link
        }}
        linkWidth={(l) => (highlightEdges.has(l.key) ? 2 : 0.6)}
        linkDirectionalArrowLength={(l) => (highlightEdges.has(l.key) ? 4 : 0)}
        linkDirectionalArrowRelPos={0.9}
        linkDirectionalParticles={(l) => (highlightEdges.has(l.key) ? 2 : 0)}
        linkDirectionalParticleWidth={2.2}
        linkDirectionalParticleSpeed={0.008}
        linkLabel={(l) => l.type}
        nodeLabel={() => ''}
        nodeCanvasObject={(n, ctx, scale) => {
          const agent = highlight.get(n.id)
          const lit = !!agent
          const base = pal.labels[n.label] ?? '#94a3b8'
          const r = Math.sqrt(Math.max(1, n.label === 'Service' ? 4 - (n.tier ?? 2) : n.label === 'Incident' ? 2.5 : 1.5)) * 4
          ctx.globalAlpha = active && !lit ? 0.16 : 1
          if (lit) {
            ctx.beginPath()
            ctx.arc(n.x!, n.y!, r + 4, 0, 2 * Math.PI)
            ctx.fillStyle = (pal.agents[agent] ?? pal.labelTextLit) + '55'
            ctx.fill()
          }
          if (n.status === 'degraded' || n.status === 'down') {
            ctx.beginPath()
            ctx.arc(n.x!, n.y!, r + 7, 0, 2 * Math.PI)
            ctx.strokeStyle = pal.down
            ctx.lineWidth = 1.5
            ctx.setLineDash([3, 2])
            ctx.stroke()
            ctx.setLineDash([])
          }
          ctx.beginPath()
          ctx.arc(n.x!, n.y!, r, 0, 2 * Math.PI)
          ctx.fillStyle = base
          ctx.fill()
          const showLabel = lit || hover === n.id || scale > 2.2 || (!active && n.label === 'Service' && scale > 1.1)
          if (showLabel) {
            const fs = Math.max(10 / scale, 2.5)
            ctx.font = `${lit ? 600 : 500} ${fs}px Inter, system-ui, sans-serif`
            ctx.textAlign = 'center'
            ctx.textBaseline = 'top'
            const text = n.label === 'Page' ? `Page request · ${PAGE_STATUS[n.status ?? ''] ?? 'recorded'}`
              : n.label === 'Fix' ? `Fix · ${PAGE_STATUS[n.status ?? ''] ?? 'proposed'}`
              : ['Service', 'Person', 'Customer', 'Project'].includes(n.label) ? n.name : n.id
            ctx.fillStyle = pal.labelBg
            const w = ctx.measureText(text).width
            ctx.fillRect(n.x! - w / 2 - 1.5, n.y! + r + 2, w + 3, fs + 2)
            ctx.fillStyle = lit ? pal.labelTextLit : pal.labelText
            ctx.fillText(text, n.x!, n.y! + r + 3)
          }
          ctx.globalAlpha = 1
        }}
        nodePointerAreaPaint={(n, color, ctx) => {
          ctx.fillStyle = color
          ctx.beginPath()
          ctx.arc(n.x!, n.y!, 8, 0, 2 * Math.PI)
          ctx.fill()
        }}
      />
      {hover && cache.current.get(hover) && (
        <div className="node-card">
          <span className="dot" style={{ background: pal.labels[cache.current.get(hover)!.label] }} />
          <b>{cache.current.get(hover)!.name}</b>
          <span className="muted"> · {cache.current.get(hover)!.label} · {hover}</span>
          {cache.current.get(hover)!.about && <span className="about">{cache.current.get(hover)!.about}</span>}
        </div>
      )}
      <div className={`legend ${legend ? 'open' : ''}`}>
        <button className="legend-toggle" onClick={toggleLegend} aria-expanded={legend} aria-controls="map-legend">
          {legend ? 'Hide key' : 'What the colours mean'}
        </button>
        {legend && (
          <div id="map-legend" className="legend-items">
            {LEGEND.map((l) => <span key={l}><i style={{ background: pal.labels[l] }} />{LEGEND_NAME[l] ?? l}</span>)}
          </div>
        )}
      </div>
    </div>
  )
})

export { idOf }
export default GraphView
