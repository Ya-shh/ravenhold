import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getJSON, post, streamChat, type AgentEvent, type Alert, type CallRow, type Limits, type GEdge, type GNode, type Lesson, type Scenario, type SessionRow, type Usage } from './api'
import type { GraphHandle } from './GraphView'
import { CANVAS, ekey, useTheme, type ThemePref } from './theme'
import { onTabKeys, useLayout, usePopover } from './a11y'
import { UsageGlass, modelName, roleName } from './UsageGlass'
import { ASK_CHAPTER, DATA_CHAPTER, GuideModal, GuideSteps, TECH_CHAPTER } from './Guide'
import { AnswerTrace, useAbout } from './Transparency'
import { KnowledgeModal } from './Knowledge'
import { AlertStory, TOOL_PLAIN, Term, describeId } from './Plain'

type Turn = { role: 'user' | 'assistant'; text: string; events: AgentEvent[]; startedAt?: number; endedAt?: number; suggestions?: string[]; costUsd?: number }
type Tab = 'trace' | 'usage' | 'memory' | 'sessions'
type View = 'chat' | 'map' | 'details'

// The map (force layout + canvas) and the markdown parser load after first paint; neither is needed to read the page.
const GraphView = lazy(() => import('./GraphView'))
let parseMd: ((src: string) => string) | null = null
let mdLoading: Promise<unknown> | null = null
const loadMarked = () => (mdLoading ??= import('marked').then(({ marked }) => { parseMd = (src) => marked.parse(src, { async: false }) as string }))

const AGENTS = [
  { id: 'commander', name: 'Commander', role: 'Leads the response, delegates and acts' },
  { id: 'investigator', name: 'Investigator', role: 'Impact, root cause, who to call' },
  { id: 'historian', name: 'Historian', role: 'Past incidents and lessons learned' },
] as const

const SUGGESTIONS = [
  { tag: 'Live incident', text: 'Checkout is throwing 5xx and payments-api has connection timeouts. What is going on and who do we page?' },
  { tag: 'Impact', text: 'What breaks if payments-db goes down? Which customers and how much ARR?' },
  { tag: 'Expertise', text: 'Who actually knows pgbouncer, and are they on call?' },
  { tag: 'Projects', text: 'What is the Platform team working on, and is anything at risk?' },
  { tag: 'Decisions', text: 'Why do ledger writes go through Kafka? Trace the decision to its sources.' },
  { tag: 'Risk', text: 'Which services are our biggest single points of failure?' },
  { tag: 'Vague report', text: 'We are losing a million dollars. What is going on?' },
]

// One agent team, entered in all three Graph Hacks tracks. Each row lets a judge see that track working in one click.
const TRACKS = [
  { id: 'act', icon: '🕵️', name: 'Agents That Act on Connected Data', short: 'Acts on the graph',
    line: 'Follows the graph from 13 alarms to the one part that broke, picks its tools through MCP, then acts: pages the right person and applies the fix once you approve, and verifies the recovery.',
    try: 'Simulate an incident (free)', ask: null },
  { id: 'memory', icon: '🤖', name: 'Agent Memory and Coordination', short: 'Memory & teamwork',
    line: 'Hands work to its two helper agents through the graph (watch "Who is doing what"), remembers past incidents, and keeps what you teach it in every future conversation.',
    try: 'Ask: "Kafka lost a broker. Have we seen this before?"',
    ask: 'Kafka just lost a broker and the ledger is falling behind. Have we seen this before, and who do we page?' },
  { id: 'brain', icon: '🧠', name: 'Company Brain', short: 'Company brain',
    line: 'Knows who owns what, who actually knows it, what each team is working on and why things were decided, and keeps that current from what you paste, always with sources.',
    try: 'Ask: "What is the Platform team working on?"',
    ask: 'What is the Platform team working on, and is anything at risk?' },
] as const

// What the agents can look up, counted live from the graph. Colours match the dots on the map.
const KNOW = [
  { name: 'systems', keys: ['systems'], color: 'Service', eg: 'Checkout, Payments API, PgBouncer and how they depend on each other' },
  { name: 'people', keys: ['people'], color: 'Person', eg: 'in 6 teams, with who is on call and who changed teams' },
  { name: 'customers', keys: ['customers'], color: 'Customer', eg: 'and how much each one pays' },
  { name: 'past outages', keys: ['past problems'], color: 'Incident', eg: 'what broke, who fixed it, what worked' },
  { name: 'documents, chats and tickets', keys: ['documents', 'chats', 'to-dos'], color: 'Doc', eg: 'postmortems, Slack messages, Jira to-dos' },
  { name: 'projects', keys: ['projects'], color: 'Project', eg: 'who works on what, and what is at risk' },
] as const

const TOOL_LABELS: Record<string, string> = {
  delegate: 'Delegate to workers', blast_radius: 'Blast radius', root_cause_candidates: 'Root-cause candidates',
  find_experts: 'Find experts', similar_incidents: 'Similar incidents', get_runbook: 'Read runbook',
  open_follow_ups: 'Open follow-ups', trace_decisions: 'Trace decisions', search_knowledge: 'Search knowledge',
  critical_services: 'Critical services', project_overview: 'Projects', shortest_connection: 'Shortest connection', graph_query: 'Cypher query',
  recall_memory: 'Recall memory', record_lesson: 'Record lesson', declare_incident: 'Declare incident',
  page_person: 'Page person', create_ticket: 'Create ticket', resolve_incident: 'Resolve incident',
  propose_fix: 'Propose fix', check_recovery: 'Check recovery',
}
// Typical cost of a full incident investigation, from measured runs (Sonnet 5.5 Commander, Haiku 5.5 workers).
const INCIDENT_COST = 0.04
const ACTION_TOOLS = new Set(['declare_incident', 'page_person', 'create_ticket', 'resolve_incident', 'record_lesson', 'propose_fix'])

const store = {
  get(k: string) { try { return localStorage.getItem(k) } catch { return null } },
  set(k: string, v: string | null) { try { if (v) { localStorage.setItem(k, v) } else { localStorage.removeItem(k) } } catch { /* storage unavailable */ } },
}

const CITED = /\[([A-Z]{2,}-[\w-]+|[a-z][a-z0-9-]+)\]/g
const attr = (v: string) => v.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')

function renderMd(text: string, unknown: string[] = [], nodes?: Map<string, GNode>) {
  const safe = text.replace(/</g, '&lt;')
  const html = parseMd ? parseMd(safe) : `<p class="pre">${safe}</p>`
  return html.replace(CITED, (_m, id: string) => {
    if (unknown.includes(id)) return `<span class="chip unverified" title="Not found in the graph">${id} ?</span>`
    const d = describeId(id, nodes?.get(id))
    return `<button class="chip" data-node="${id}" title="${attr(d.text ? `${d.kind}: ${d.text}` : d.kind)}">${id}</button>`
  })
}

/** Every code an answer cites, in order, with what it is: a key for readers who don't know INC-142 from RB-7. */
function CodeKey({ text, unknown, nodes }: { text: string; unknown: string[]; nodes: Map<string, GNode> }) {
  const ids = [...new Set([...text.matchAll(CITED)].map((m) => m[1]))].filter((id) => !unknown.includes(id))
  if (!ids.length) return null
  return (
    <details className="codes" onClick={(e) => e.stopPropagation()}>
      <summary>What the names and codes in this answer mean ({ids.length})</summary>
      <dl>
        {ids.map((id) => {
          const d = describeId(id, nodes.get(id))
          return (
            <div key={id}>
              <dt><button className="chip" data-node={id}>{id}</button></dt>
              <dd><b>{d.kind}</b>{d.text && <> · {d.text}</>}</dd>
            </div>
          )
        })}
      </dl>
    </details>
  )
}

function citationsOf(t: Turn) {
  const f = t.events.find((e) => e.type === 'final') as Extract<AgentEvent, { type: 'final' }> | undefined
  return f?.citations
}

function argSummary(input: Record<string, unknown> | undefined) {
  if (!input) return ''
  const v = input.service_id ?? input.runbook_id ?? input.person_id ?? input.query ?? input.service_ids ?? input.title
  if (Array.isArray(v)) return v.join(', ')
  if (typeof v === 'string') return v
  if (Array.isArray(input.tasks)) return `${input.tasks.length} tasks`
  return ''
}

function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className="logo">
      <rect width="32" height="32" rx="8" fill="var(--brand-bg)" />
      <path d="M7 21.5c3.2-.4 5.6-2.1 7.3-4.9 1.3-2.2 3-3.6 5.4-3.9l2.7-2.4.2 2.9 2.4.9-2.9 1.2c-1.2 4.9-5 8.3-10.4 8.7-1.8.1-3.4-.7-4.7-2.5z" fill="var(--brand)" />
      <circle cx="20.6" cy="12.2" r=".9" fill="var(--brand-bg)" />
    </svg>
  )
}

function Avatar({ agent, size = 22 }: { agent: string; size?: number }) {
  return <span className={`avatar ag-${agent}`} style={{ width: size, height: size }} aria-hidden>{agent[0].toUpperCase()}</span>
}

const I = {
  play: <path d="M5 3.5v9l7-4.5z" />,
  bolt: <path d="M9 1.5 3.5 9H8l-1 5.5L12.5 7H8z" />,
  help: <><circle cx="8" cy="8" r="6" /><path d="M6.4 6.2a1.7 1.7 0 0 1 3.2.7c0 1.1-1.6 1.4-1.6 2.4M8 11.4v.1" /></>,
  more: <><circle cx="3.5" cy="8" r="1" /><circle cx="8" cy="8" r="1" /><circle cx="12.5" cy="8" r="1" /></>,
  plus: <path d="M8 3v10M3 8h10" />,
  reset: <path d="M3 8a5 5 0 1 0 1.5-3.5M3 3v2.5h2.5" />,
  lock: <><rect x="3.5" y="7" width="9" height="6.5" rx="1.5" /><path d="M5.5 7V5.2a2.5 2.5 0 0 1 5 0V7" /></>,
  doc: <path d="M3 12.5V3.5h7l3 3v6zM10 3.5v3h3M5.5 9h5M5.5 11h3" />,
  chat: <path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z" />,
  map: <><circle cx="4" cy="4.5" r="1.6" /><circle cx="12" cy="4" r="1.6" /><circle cx="8" cy="12" r="1.6" /><path d="M5.2 5.6 7.2 10.6M10.4 4.2 5.6 4.4M9.3 11.3l1.9-5.7" /></>,
  list: <path d="M5.5 4h8M5.5 8h8M5.5 12h8M2.5 4h.01M2.5 8h.01M2.5 12h.01" />,
  sun: <><circle cx="8" cy="8" r="2.8" /><path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1" /></>,
  moon: <path d="M13 9.6A5.5 5.5 0 0 1 6.4 3a5.5 5.5 0 1 0 6.6 6.6z" />,
  system: <><rect x="2" y="3" width="12" height="8" rx="1.5" /><path d="M6 14h4M8 11v3" /></>,
}
const Icon = ({ d }: { d: React.ReactNode }) => <svg viewBox="0 0 16 16" aria-hidden>{d}</svg>

const THEMES: Record<ThemePref, { next: ThemePref; label: string; icon: React.ReactNode }> = {
  system: { next: 'light', label: 'Theme: matches your device', icon: I.system },
  light: { next: 'dark', label: 'Theme: light', icon: I.sun },
  dark: { next: 'system', label: 'Theme: dark', icon: I.moon },
}

function MoreMenu({ busy, onNew, onReset }: { busy: boolean; onNew: () => void; onReset: () => void }) {
  const [open, setOpen] = useState(false)
  const ref = usePopover<HTMLDivElement>(open, setOpen)
  const pick = (fn: () => void) => () => { setOpen(false); fn() }
  return (
    <div className="pop" ref={ref}>
      <button className="btn icon subtle" aria-haspopup="menu" aria-expanded={open} aria-label="More options" onClick={() => setOpen((o) => !o)}>
        <Icon d={I.more} />
      </button>
      {open && (
        <div className="menu" role="menu" aria-label="More options">
          <button role="menuitem" onClick={pick(onNew)} disabled={busy}><Icon d={I.plus} /><b>New conversation</b><span>Past ones stay in History</span></button>
          <button role="menuitem" onClick={pick(onReset)} disabled={busy}><Icon d={I.reset} /><b>Reset my copy</b><span>Wipe your incidents, lessons and history</span></button>
          <p className="menu-note"><Icon d={I.lock} />You have your own private copy of the company. Nothing you do affects other visitors.</p>
        </div>
      )}
    </div>
  )
}

function SimMenu({ scenarios, disabled, title, onPick }: { scenarios: Scenario[]; disabled: boolean; title: string; onPick: (id: string) => void }) {
  const [open, setOpen] = useState(false)
  const ref = usePopover<HTMLDivElement>(open, setOpen)
  return (
    <div className="pop" ref={ref}>
      <button className="btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)} disabled={disabled} title={title}>
        <Icon d={I.bolt} /><span className="btn-label">Simulate incident</span>
      </button>
      {open && (
        <div className="menu wide" role="menu" aria-label="Choose a practice incident">
          <p className="menu-head">Fire a set of test alarms into the map. The agents have to find the cause. Free.</p>
          {scenarios.map((sc) => (
            <button key={sc.id} role="menuitem" onClick={() => { setOpen(false); onPick(sc.id) }}>
              <b>{sc.name}</b><em>{sc.alerts} alarms</em><span>{sc.summary}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default function App() {
  const [tenant, setTenant] = useState(() => (['ravenhold', 'ravenhold-labs'].includes(store.get('rh.tenant') ?? '') ? store.get('rh.tenant')! : 'ravenhold'))
  const [sessionId, setSessionId] = useState<string | null>(() => store.get(`rh.session.${store.get('rh.tenant') ?? 'ravenhold'}`))
  const [graph, setGraph] = useState<{ nodes: GNode[]; edges: GEdge[] }>({ nodes: [], edges: [] })
  const nodeById = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes])
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  const [input, setInput] = useState('')
  const [tab, setTab] = useState<Tab>('trace')
  const [highlight, setHighlight] = useState<Map<string, string>>(new Map())
  const [hlEdges, setHlEdges] = useState<Set<string>>(new Set())
  const [selectedTurn, setSelectedTurn] = useState<number | null>(null)
  const [openCypher, setOpenCypher] = useState<string | null>(null)
  const [lessons, setLessons] = useState<Lesson[]>([])
  const [sessions, setSessions] = useState<SessionRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [spend, setSpend] = useState<Usage | null>(null)
  const [lastRunUsd, setLastRunUsd] = useState<number | null>(null)
  const [callLog, setCallLog] = useState<CallRow[]>([])
  const [tick, setTick] = useState<{ key: number; usd: number; agent: string } | null>(null)
  const [liveIds, setLiveIds] = useState<Set<string>>(new Set())
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [view, setView] = useState<View>('chat')
  const [moreExamples, setMoreExamples] = useState(false)
  const [showIntro, setShowIntro] = useState(false)
  const [investigatedKey, setInvestigatedKey] = useState<string | null>(() => store.get(`rh.investigated.${store.get('rh.tenant') ?? 'ravenhold'}`))
  const messagesRef = useRef<HTMLDivElement>(null)
  const [mdReady, setMdReady] = useState(!!parseMd)
  const [mapSeen, setMapSeen] = useState(false)
  const { pref, theme, setPref } = useTheme()
  const layout = useLayout()
  const about = useAbout(true, tenant)
  const company = tenant === 'ravenhold' ? 'Ravenhold Pay' : 'Ravenhold Labs'
  const [guideOpen, setGuideOpen] = useState(false)
  const [guidePage, setGuidePage] = useState(0)
  const openGuide = (page = 0) => { setGuidePage(page); setGuideOpen(true) }
  const [knowOpen, setKnowOpen] = useState(false)
  const [pageStatus, setPageStatus] = useState<Record<string, string>>({})
  const [rbStatus, setRbStatus] = useState<Record<string, string>>({})
  const [fixStatus, setFixStatus] = useState<Record<string, FixResult>>({})
  const [limits, setLimits] = useState<Limits | null>(null)
  const graphRef = useRef<GraphHandle>(null)
  const chatEnd = useRef<HTMLDivElement>(null)

  const refresh = useCallback(async (t = tenant) => {
    try {
      const [g, l, s, u, a, c] = await Promise.all([
        getJSON<{ nodes: GNode[]; edges: GEdge[] }>(`/api/graph?tenant=${t}`),
        getJSON<Lesson[]>(`/api/memory?tenant=${t}`),
        getJSON<SessionRow[]>(`/api/sessions?tenant=${t}`),
        getJSON<Usage>('/api/usage'),
        getJSON<Alert[]>(`/api/alerts?tenant=${t}`),
        getJSON<CallRow[]>('/api/usage/calls?limit=120'),
      ])
      setGraph(g); setLessons(l); setSessions(s); setSpend(u); setAlerts(a); setCallLog(c); setError(null)
      setRbStatus((p) => ({ ...p, ...Object.fromEntries(l.filter((x) => x.proposed_version).map((x) => [x.proposed_version!, x.proposed_status ?? 'proposed'])) }))
      getJSON<Limits>('/api/limits').then(setLimits).catch(() => {})
      return u
    } catch (e) {
      setError(`Can't reach the Ravenhold API (${(e as Error).message}).`)
    }
  }, [tenant])

  const loadSession = useCallback(async (sid: string, t = tenant) => {
    try {
      const d = await getJSON<{ turns: { role: 'user' | 'assistant'; text: string; events?: AgentEvent[] }[] }>(`/api/sessions/${sid}?tenant=${t}`)
      // Saved traces come back with the turns, so a reopened session shows every step it took.
      setTurns(d.turns.map((x) => ({ ...x, events: x.events ?? [], suggestions: (x.events?.find((e) => e.type === 'final') as Extract<AgentEvent, { type: 'final' }> | undefined)?.suggestions })))
      const ids = d.turns.flatMap((x) => (x.events ?? []).filter((e) => e.type === 'approval').map((e) => (e as { page_id: string }).page_id))
      if (ids.length) getJSON<Record<string, string>>(`/api/pages?ids=${ids.join(',')}&tenant=${t}`).then((st) => setPageStatus((p) => ({ ...p, ...st }))).catch(() => {})
      const fixIds = d.turns.flatMap((x) => (x.events ?? []).filter((e) => e.type === 'fix_proposal').map((e) => (e as { fix_id: string }).fix_id))
      if (fixIds.length) getJSON<Record<string, FixResult>>(`/api/fixes?ids=${fixIds.join(',')}&tenant=${t}`).then((st) => setFixStatus((p) => ({ ...p, ...st }))).catch(() => {})
      setSessionId(sid); store.set(`rh.session.${t}`, sid)
    } catch { setTurns([]) }
  }, [tenant])

  useEffect(() => {
    store.set('rh.tenant', tenant)
    refresh(tenant)
    const sid = store.get(`rh.session.${tenant}`)
    if (sid) { loadSession(sid, tenant) } else { setTurns([]); setSessionId(null) }
    setHighlight(new Map()); setHlEdges(new Set())
  }, [tenant]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { getJSON<Scenario[]>('/api/scenarios').then(setScenarios).catch(() => {}) }, [])

  useEffect(() => { if (turns.length) chatEnd.current?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }) }, [turns])
  useEffect(() => { if (!mdReady && turns.length) loadMarked().then(() => setMdReady(true)) }, [turns.length, mdReady])

  const applySubgraph = useCallback((agent: string, sg: { nodes: GNode[]; edges: GEdge[] }, replace = false) => {
    setHighlight((prev) => {
      const m = replace ? new Map<string, string>() : new Map(prev)
      sg.nodes.forEach((n) => m.set(n.id, agent))
      return m
    })
    setHlEdges((prev) => {
      const s = replace ? new Set<string>() : new Set(prev)
      sg.edges.forEach((e) => s.add(ekey(e.source, e.type, e.target)))
      return s
    })
    setGraph((g) => {
      const have = new Set(g.nodes.map((n) => n.id))
      const haveE = new Set(g.edges.map((e) => ekey(e.source, e.type, e.target)))
      const nn = sg.nodes.filter((n) => !have.has(n.id))
      const ne = sg.edges.filter((e) => !haveE.has(ekey(e.source, e.type, e.target)))
      return nn.length || ne.length ? { nodes: [...g.nodes, ...nn], edges: [...g.edges, ...ne] } : g
    })
  }, [])

  const send = useCallback(async (text: string) => {
    if (!text.trim() || busy) return
    setBusy(true); setInput(''); setTab('trace'); setError(null)
    setHighlight(new Map()); setHlEdges(new Set())
    setSelectedTurn(null)
    const startedAt = Date.now()
    setLiveIds(new Set())
    setTurns((t) => [...t, { role: 'user', text, events: [] }, { role: 'assistant', text: '', events: [], startedAt }])
    const push = (ev: AgentEvent) => setTurns((t) => {
      const copy = [...t]
      const last = copy[copy.length - 1]
      copy[copy.length - 1] = {
        ...last, events: [...last.events, ev],
        text: ev.type === 'final' ? ev.text : last.text,
        endedAt: ev.type === 'final' ? Date.now() : last.endedAt,
        suggestions: ev.type === 'final' ? ev.suggestions : last.suggestions,
      }
      return copy
    })
    try {
      await streamChat({ tenant, session_id: sessionId, message: text }, (ev) => {
        if (ev.type === 'session') { setSessionId(ev.session_id); store.set(`rh.session.${tenant}`, ev.session_id) }
        if (ev.type === 'tool_result' && ev.subgraph) applySubgraph(ev.agent, ev.subgraph)
        if (ev.type === 'error') setError(ev.text)
        if (ev.type === 'approval') setPageStatus((p) => ({ ...p, [ev.page_id]: 'awaiting_approval' }))
        if (ev.type === 'runbook_proposal') setRbStatus((p) => ({ ...p, [ev.runbook_id]: 'proposed' }))
        if (ev.type === 'usage') {
          // Spend is shown the moment it happens: pill, Usage tab and the answer's running cost.
          setSpend((p) => p && { ...p, spent_usd: p.spent_usd + ev.usd, remaining_usd: p.remaining_usd - ev.usd })
          setCallLog((c) => [ev, ...c])
          setLiveIds((s) => new Set(s).add(ev.id))
          setTick({ key: Date.now(), usd: ev.usd, agent: ev.agent })
        }
        if (ev.type === 'task_status' && ev.status !== 'running') refresh()
        push(ev)
      })
    } catch (e) {
      setError((e as Error).message)
      setTurns((t) => (t.length && !t[t.length - 1].text && t[t.length - 1].role === 'assistant' ? t.slice(0, -1) : t))
    } finally {
      setBusy(false)
      setTurns((t) => {
        const last = t[t.length - 1]
        if (last?.role === 'assistant') setLastRunUsd(turnCost(last))
        return t
      })
      refresh()
    }
  }, [busy, tenant, sessionId, applySubgraph, refresh])

  const simulate = async (id: string) => {
    let res: { alerts: number; services: string[] }
    try { res = await post(`/api/simulate?scenario=${id}&tenant=${tenant}`) } catch (e) { setError((e as Error).message); return null }
    setHighlight(new Map()); setHlEdges(new Set())
    await refresh()
    return res
  }

  /** Free first step: fire test alerts into the graph (no model calls). Investigating is a separate, priced click. */
  const tryDemo = async () => {
    if (busy) return
    if (tenant !== 'ravenhold') { setTenant('ravenhold'); setError('Switched to the Ravenhold Pay workspace. Press "Simulate an incident" again to start.'); return }
    await simulate('pool-exhaustion')
  }
  const decideRunbook = async (id: string, approve: boolean) => {
    try {
      const r = await post(`/api/runbooks/${id}/decision?approve=${approve}&tenant=${tenant}`)
      setRbStatus((p) => ({ ...p, [id]: r.status }))
    } catch (e) { setError((e as Error).message) }
  }
  const blocked = limits && !limits.can_run
  // Keyed by practice scenario, so the card stays collapsed after a fix clears part of the storm.
  const alertKey = [...new Set(alerts.map((a) => a.scenario ?? a.id))].sort().join(',')

  const clearAlerts = async () => {
    setInvestigatedKey(null); store.set(`rh.investigated.${tenant}`, null)
    try { await post(`/api/alerts/clear?tenant=${tenant}`) } finally { refresh() }
  }

  const newSession = () => { setSessionId(null); store.set(`rh.session.${tenant}`, null); setTurns([]); setHighlight(new Map()); setHlEdges(new Set()); setSelectedTurn(null) }

  const resetData = async () => {
    if (!confirm('Reset your private copy? Your incidents, lessons and conversation history are wiped and you get a fresh copy of the company. Nobody else is affected.')) return
    try { await post(`/api/reset?tenant=${tenant}`) } catch (e) { setError((e as Error).message); return }
    newSession(); refresh()
  }

  // On phones and tablets the map may be hidden behind a tab; bring it forward when something points at it.
  const showMap = () => { if (layout !== 'desktop') setView('map') }
  // The map may not be mounted yet on a phone (it loads the first time its tab opens), so wait for it briefly.
  const focusNode = (id: string, tries = 0) => {
    if (tries === 0) showMap()
    if (graphRef.current) graphRef.current.focus(id)
    else if (tries < 20) setTimeout(() => focusNode(id, tries + 1), 150)
  }
  const onChatClick = (e: React.MouseEvent) => {
    const el = (e.target as HTMLElement).closest('[data-node]') as HTMLElement | null
    if (el?.dataset.node) focusNode(el.dataset.node)
  }

  const traceTurn = selectedTurn ?? turns.length - 1
  const events = useMemo(() => turns[traceTurn]?.events ?? [], [turns, traceTurn])
  const tasks = useMemo(() => {
    const m = new Map<string, { id: string; agent: string; instructions: string; status: string; result?: string }>()
    for (const ev of events) {
      if (ev.type === 'task_created') m.set(ev.task_id, { id: ev.task_id, agent: ev.agent, instructions: ev.instructions, status: 'todo' })
      if (ev.type === 'task_status') { const t = m.get(ev.task_id); if (t) m.set(ev.task_id, { ...t, status: ev.status, result: ev.result ?? t.result }) }
    }
    return [...m.values()]
  }, [events])
  const toolEvents = events.filter((e): e is Extract<AgentEvent, { type: 'tool_result' }> => e.type === 'tool_result')
  const calls = new Map(events.filter((e): e is Extract<AgentEvent, { type: 'tool_call' }> => e.type === 'tool_call').map((e) => [e.id, e]))
  const queryCount = toolEvents.reduce((a, e) => a + e.cypher.length, 0)
  const viaMcp = toolEvents.some((e) => e.via === 'mcp')
  const nodeCount = graph.nodes.filter((n) => n.label !== 'Episode').length

  const lastTurn = turns[turns.length - 1]
  const lastCall = busy && lastTurn ? [...lastTurn.events].reverse().find((e): e is Extract<AgentEvent, { type: 'tool_call' }> => e.type === 'tool_call') : undefined
  const progress = busy
    ? lastCall ? `${roleName(lastCall.agent)}: ${TOOL_PLAIN[lastCall.tool] ?? TOOL_LABELS[lastCall.tool] ?? lastCall.tool}` : 'The agents are working on your question.'
    : lastTurn?.role === 'assistant' && lastTurn.text ? 'Answer ready.' : ''
  // Tablets show the chat beside one other pane, so "chat" there means "chat + map".
  const shown: View = layout === 'tablet' && view === 'chat' ? 'map' : view
  const paneProps = (id: View) => (layout === 'desktop' ? {} : { role: 'tabpanel', 'aria-labelledby': `vt-${id}` })
  const VIEWS: { id: View; label: string; icon: React.ReactNode }[] = [
    { id: 'chat', label: 'Chat', icon: I.chat }, { id: 'map', label: 'Map', icon: I.map }, { id: 'details', label: 'Steps & details', icon: I.list },
  ]
  const TABS: { id: Tab; label: string; title: string; count?: React.ReactNode }[] = [
    { id: 'trace', label: 'Steps', title: 'How the agents found the answer, step by step' },
    { id: 'usage', label: 'Cost', title: 'Every AI call and what it cost', count: <span className="count mono">${(spend?.spent_usd ?? 0).toFixed(2)}</span> },
    { id: 'memory', label: 'Learned', title: 'Lessons the agents have learned and will reuse', count: <span className="count">{lessons.length}</span> },
    { id: 'sessions', label: 'History', title: 'Past conversations, saved in the graph', count: <span className="count">{sessions.length}</span> },
  ]
  const theme_ = THEMES[pref]
  // Phones start on Chat: don't load or run the map's layout until its tab is first opened.
  const mapVisible = layout !== 'mobile' || shown === 'map'
  if (mapVisible && !mapSeen) setMapSeen(true)

  return (
    <div className="app">
      <a className="skip" href="#composer-input">Skip to the message box</a>
      <KnowledgeModal open={knowOpen} onClose={() => setKnowOpen(false)} tenant={tenant}
        onApplied={(sg) => { applySubgraph('historian', sg, true); refresh(); showMap() }} />
      <GuideModal open={guideOpen} onClose={() => setGuideOpen(false)} onTry={tryDemo} onAsk={send} busy={busy || !!blocked} startPage={guidePage} />
      <div className="sr-only" role="status" aria-live="polite">{progress}</div>
      <header className="topbar">
        <div className="brand">
          <Logo />
          <span className="wordmark">Ravenhold</span>
          <button className="tracks-chip" onClick={() => openGuide(TECH_CHAPTER)} title="One agent team entered in all three Graph Hacks tracks: see how each is covered">
            Graph Hacks · all 3 tracks
          </button>
        </div>
        <div className="controls">
          <label className="workspace">
            <span className="sr-only">Workspace</span>
            <select value={tenant} onChange={(e) => setTenant(e.target.value)} disabled={busy} aria-label="Workspace (which company)">
              <option value="ravenhold">Ravenhold Pay</option>
              <option value="ravenhold-labs">Ravenhold Labs</option>
            </select>
          </label>
          <button className="btn subtle" onClick={() => openGuide()}>
            <Icon d={I.help} /><span className="btn-label">How it works</span>
          </button>
          <button className="btn icon subtle" onClick={() => setPref(theme_.next)} aria-label={`${theme_.label}. Switch to ${THEMES[theme_.next].label.replace('Theme: ', '')}`} title={theme_.label}>
            <Icon d={theme_.icon} />
          </button>
          <MoreMenu busy={busy} onNew={newSession} onReset={resetData} />
        </div>
      </header>

      <main data-view={shown}>
        <nav className="viewtabs" role="tablist" aria-label="Panels" onKeyDown={onTabKeys}>
          {VIEWS.map((v) => (
            <button key={v.id} id={`vt-${v.id}`} role="tab" className={`vt-${v.id}`} aria-selected={shown === v.id} aria-controls={`pane-${v.id}`}
              tabIndex={shown === v.id ? 0 : -1} onClick={() => setView(v.id)}>
              <Icon d={v.icon} />{v.label}
              {v.id === 'details' && busy && <span className="vt-dot" aria-hidden />}
            </button>
          ))}
        </nav>

        <section className="chat" id="pane-chat" aria-label="Conversation" onClick={onChatClick} {...paneProps('chat')}>
          {turns.length > 0 && (
            // Once a conversation starts the welcome screen gives way to the chat; this strip keeps the three tracks
            // (and the full intro) one click away for anyone who arrives mid-conversation.
            <div className="track-strip" role="region" aria-label="Built for all three Graph Hacks tracks">
              <span className="ts-label">All 3 tracks</span>
              {TRACKS.map((tr) => (
                <button key={tr.id} className="ts-pill" disabled={busy || (!!tr.ask && !!blocked)}
                  title={`${tr.name}: ${tr.try}`} aria-label={`${tr.name}: ${tr.try}`}
                  onClick={() => (tr.ask ? send(tr.ask) : tryDemo())}>
                  <span aria-hidden>{tr.icon}</span>{tr.short}
                </button>
              ))}
              <button className="ts-intro" aria-expanded={showIntro} aria-controls="intro"
                onClick={() => { setShowIntro((v) => !v); if (!showIntro) messagesRef.current?.scrollTo({ top: 0 }) }}>
                {showIntro ? 'Hide intro' : 'Intro'}
              </button>
            </div>
          )}
          <div className="messages" ref={messagesRef}>
            {(turns.length === 0 || showIntro) && (
              <div className={`hero ${turns.length ? 'recalled' : ''}`} id="intro">
                <div className="eyebrow">Live demo · the company and all its data are made up</div>
                <h1>Every incident, remembered.</h1>
                <p className="hero-lead">Ravenhold is an <b>AI incident-response team</b>: three agents that look after
                  {' '}<b>{company}</b>, a fictional payments company that shops and apps use to take card payments. When something
                  breaks, they find the cause, tell you who can fix it, and remember what you teach them.</p>
                <div className="hero-cta">
                  <button className="btn primary lg" onClick={tryDemo} disabled={busy}><Icon d={I.play} />Start: simulate an outage</button>
                  <span className="dim small">Free, nothing real breaks. Or read on: what it knows and what you can ask.</span>
                </div>

                <section className="knows" aria-labelledby="knows-h">
                  <h2 id="knows-h">What it knows: one company, {company}</h2>
                  <ul className="know-tiles">
                    {KNOW.map((k) => (
                      <li key={k.name} className="know-tile" style={{ '--c': CANVAS[theme].labels[k.color] } as React.CSSProperties}>
                        <b>{about ? k.keys.reduce((n, key) => n + (about.datasets.find((x) => x.key === key)?.count ?? 0), 0) : '…'}</b>
                        <span>{k.name}</span>
                        <em>{k.name === 'customers' && about ? `paying $${(about.totals.arr / 1e6).toFixed(1)}M a year` : k.eg}</em>
                      </li>
                    ))}
                  </ul>
                  <p className="knows-note"><b>It isn't trained on any of this.</b> Each time you ask, the agents look the facts up in
                    the company map (the dots in the middle of the screen) and show you where each one came from. They know nothing
                    about real companies, the internet or your own systems.</p>
                  <button className="link-btn" onClick={() => openGuide(DATA_CHAPTER)}>Where this data comes from →</button>
                </section>

                <section className="try" aria-labelledby="try-h">
                  <h2 className="section-label" id="try-h">Try it in 3 steps</h2>
                  <ol className="try-steps">
                    <li>
                      <span className="try-num" aria-hidden>1</span>
                      <div>
                        <b>Set off a practice outage</b>
                        <span>Thirteen test alarms go off on the map. Nothing real breaks, and this step is free.</span>
                        <button className="btn" onClick={tryDemo} disabled={busy}><Icon d={I.play} />Simulate an incident</button>
                      </div>
                    </li>
                    <li>
                      <span className="try-num" aria-hidden>2</span>
                      <div>
                        <b>Press Investigate, or ask about the company</b>
                        <span>An answer takes about 20 seconds and uses about 3 cents of shared demo credit. Try one of these:</span>
                        <div className="suggestions">
                          {(moreExamples ? SUGGESTIONS : SUGGESTIONS.slice(0, 3)).map((s) => (
                            <button key={s.text} onClick={() => send(s.text)} disabled={busy || !!blocked}>
                              <span className="tag">{s.tag}</span>
                              <span>{s.text}</span>
                            </button>
                          ))}
                        </div>
                        <button className="link-btn" aria-expanded={moreExamples} onClick={() => setMoreExamples((v) => !v)}>
                          {moreExamples ? 'Fewer examples' : `More examples (${SUGGESTIONS.length - 3})`}
                        </button>
                      </div>
                    </li>
                    <li>
                      <span className="try-num" aria-hidden>3</span>
                      <div>
                        <b>Teach it something</b>
                        <span>Correct an answer ("don't pause the pool at peak traffic") and every future conversation uses it.
                          Or press <b>Add knowledge</b> above the map and paste news such as "Sarah joins the Data team".</span>
                      </div>
                    </li>
                  </ol>
                  <p className="try-limit">It only answers questions about {company}. Ask about anything else and it will tell you so.
                    {' '}<button className="link-btn inline" onClick={() => openGuide(ASK_CHAPTER)}>What can I ask? →</button></p>
                </section>

                <section className="tracks" aria-labelledby="tracks-h">
                  <h2 className="section-label" id="tracks-h">Built for all three Graph Hacks tracks <span>one agent team, one graph</span></h2>
                  <ul className="track-list">
                    {TRACKS.map((tr) => (
                      <li key={tr.id} className="track">
                        <span className="track-icon" aria-hidden>{tr.icon}</span>
                        <div>
                          <b>{tr.name}</b>
                          <span>{tr.line}</span>
                          <button className="link-btn" disabled={busy || (!!tr.ask && !!blocked)}
                            onClick={() => (tr.ask ? send(tr.ask) : tryDemo())}>{tr.try} →</button>
                        </div>
                      </li>
                    ))}
                  </ul>
                  <button className="link-btn quiet" onClick={() => openGuide(TECH_CHAPTER)}>How each track is covered, in detail →</button>
                </section>
                <section className="hero-steps" aria-labelledby="hero-how">
                  <h2 className="section-label" id="hero-how">What happens behind the scenes</h2>
                  <GuideSteps />
                  <div className="hero-links">
                    <button className="link-btn" onClick={() => openGuide()}>Read the 3-minute guide →</button>
                    <button className="link-btn" onClick={() => openGuide(TECH_CHAPTER)}>Technical details →</button>
                  </div>
                </section>
              </div>
            )}
            {turns.map((t, i) => t.role === 'user' ? (
              <div key={i} className="msg user"><p>{t.text}</p></div>
            ) : (
              <article key={i} className={`msg assistant ${i === traceTurn ? 'selected' : ''}`} onClick={() => setSelectedTurn(i)}>
                <div className="msg-head">
                  <Logo size={18} />
                  <b>Ravenhold</b>
                  {t.events.length > 0 && (
                    <span className="dim">
                      {plural(t.events.filter((e) => e.type === 'tool_call').length, 'tool')} · {plural(t.events.reduce((a, e) => a + (e.type === 'tool_result' ? e.cypher.length : 0), 0), 'query', 'queries')}
                      {t.endedAt && t.startedAt ? ` · ${Math.round((t.endedAt - t.startedAt) / 1000)}s` : ''}
                      {turnCost(t) > 0 && <b className={`cost ${t.text ? '' : 'live'}`}>{` · $${turnCost(t).toFixed(3)}`}</b>}
                    </span>
                  )}
                </div>
                {t.text ? <div className="md" dangerouslySetInnerHTML={{ __html: renderMd(t.text, citationsOf(t)?.unknown, nodeById) }} /> : <Working events={t.events} />}
                {t.text && <CodeKey text={t.text} unknown={citationsOf(t)?.unknown ?? []} nodes={nodeById} />}
                {citationsOf(t) && (citationsOf(t)!.verified.length + citationsOf(t)!.unknown.length) > 0 && (
                  <div className={`verify ${citationsOf(t)!.unknown.length ? 'warn' : ''}`}
                    title="Ravenhold checks every id the answer cites against the graph, instead of trusting the AI.">
                    <svg viewBox="0 0 16 16" aria-hidden><path d="M3.5 8.5 6.5 11.5 12.5 4.5" /></svg>
                    {citationsOf(t)!.verified.length} facts checked against the graph
                    {citationsOf(t)!.unknown.length > 0 && <b> · {citationsOf(t)!.unknown.length} not found: {citationsOf(t)!.unknown.join(', ')}</b>}
                  </div>
                )}
                {t.text && (
                  <AnswerTrace events={t.events} citations={citationsOf(t)}
                    seconds={t.endedAt && t.startedAt ? Math.round((t.endedAt - t.startedAt) / 1000) : null}
                    onShow={(sg) => { showMap(); applySubgraph('commander', sg, true); setTimeout(() => graphRef.current?.fit(), 50) }} />
                )}
                {t.events.filter((e): e is Extract<AgentEvent, { type: 'approval' }> => e.type === 'approval').map((a) => (
                  <ApprovalCard key={a.page_id} a={a} status={pageStatus[a.page_id] ?? 'awaiting_approval'} disabled={busy}
                    onDecide={async (approve) => {
                      try {
                        const r = await post(`/api/pages/${a.page_id}/decision?approve=${approve}&tenant=${tenant}`)
                        setPageStatus((p) => ({ ...p, [a.page_id]: r.status }))
                        refresh()  // the page node on the map shows its new status
                      } catch (e) { setError((e as Error).message) }
                    }} />
                ))}
                {t.events.filter((e): e is Extract<AgentEvent, { type: 'fix_proposal' }> => e.type === 'fix_proposal').map((fx) => (
                  <FixCard key={fx.fix_id} fx={fx} result={fixStatus[fx.fix_id] ?? { status: 'awaiting_approval' }} disabled={busy}
                    onVerify={() => send(`The fix ${fx.runbook_id} was applied for ${fx.incident_id}. Did it work? Check recovery.`)}
                    onDecide={async (approve) => {
                      try {
                        const r = await post(`/api/fixes/${fx.fix_id}/decision?approve=${approve}&tenant=${tenant}`)
                        setFixStatus((p) => ({ ...p, [fx.fix_id]: r }))
                        refresh()  // alarms clear and the map turns healthy
                      } catch (e) { setError((e as Error).message) }
                    }} />
                ))}
                {t.events.filter((e): e is Extract<AgentEvent, { type: 'runbook_proposal' }> => e.type === 'runbook_proposal').map((p) => (
                  <RunbookCard key={p.runbook_id} id={p.runbook_id} supersedes={p.supersedes} steps={p.steps}
                    status={rbStatus[p.runbook_id] ?? 'proposed'} disabled={busy} onDecide={(ok) => decideRunbook(p.runbook_id, ok)} />
                ))}
                {i === turns.length - 1 && !busy && t.suggestions && t.suggestions.length > 0 && (
                  <div className="next-steps" aria-label="Suggested follow-up questions">
                    <span className="next-label">Suggested next questions · optional, one click to ask</span>
                    {t.suggestions.map((sug) => (
                      <button key={sug} onClick={(e) => { e.stopPropagation(); send(sug) }} disabled={!!blocked}>{sug}</button>
                    ))}
                  </div>
                )}
              </article>
            ))}
            <div ref={chatEnd} />
          </div>
          {blocked && (
            <div className="limit-note" role="status">
              <b>The shared demo credit is used up (${limits!.spent_usd.toFixed(2)} of ${limits!.budget_usd.toFixed(2)})</b>
              <span>The AI agents are paused so nothing more is charged. You can still simulate incidents, explore the company map,
                read How it works, and reopen past conversations in History.</span>
            </div>
          )}
          {alerts.length > 0 && !busy && (
            // The alarms keep firing until the test is stopped, so once this outage has been investigated the card
            // shrinks to a status bar instead of offering another (paid) investigation.
            <AlertStory alerts={alerts} scenario={scenarios.find((sc) => sc.id === alerts[0]?.scenario)} price={INCIDENT_COST}
              busy={busy || !!blocked} onClear={clearAlerts} investigated={investigatedKey === alertKey}
              onInvestigate={() => {
                setInvestigatedKey(alertKey); store.set(`rh.investigated.${tenant}`, alertKey)
                send(`${alerts.length} alerts are firing across ${new Set(alerts.map((a) => a.service)).size} services. What is going on and who do we page?`)
              }} />
          )}
          {error && <div className="error" role="alert">{error}</div>}
          <form className="composer" onSubmit={(e) => { e.preventDefault(); send(input) }}>
            <label htmlFor="composer-input" className="sr-only">Message the agents</label>
            <textarea
              id="composer-input"
              value={input}
              placeholder={busy ? 'The agents are working…' : 'Describe an incident, ask about the company, or correct the agents…'}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input) } }}
              disabled={busy || !!blocked}
              rows={2}
            />
            <div className="composer-foot">
              <span className="dim">Enter to send · Shift+Enter for a new line</span>
              <button type="submit" className="send" disabled={busy || !input.trim()} aria-label="Send">
                <svg viewBox="0 0 16 16" aria-hidden><path d="M2 8h11M9 4l4 4-4 4" /></svg>
              </button>
            </div>
          </form>
        </section>

        <section className="graph" id="pane-map" aria-label="Company map" {...paneProps('map')}>
          {(mapSeen || mapVisible) && (
            <Suspense fallback={<div className="graph-loading dim">Loading the map…</div>}>
              <GraphView ref={graphRef} nodes={graph.nodes} edges={graph.edges} highlight={highlight} highlightEdges={hlEdges} theme={theme} />
            </Suspense>
          )}
          <UsageGlass usage={spend} lastRunUsd={lastRunUsd} tick={tick} onOpenTab={() => { setTab('usage'); setView('details') }} />
          <div className="graph-title">
            <b>Company map <span className="dim">· {tenant === 'ravenhold' ? 'Ravenhold Pay' : 'Ravenhold Labs'}</span></b>
            <span className="graph-explain">Each dot is a system, person, past problem or decision. Lines show how they connect.</span>
            <span className="dim small">{nodeCount} dots · {graph.edges.length} connections · stored in FalkorDB</span>
          </div>
          <div className="graph-actions">
            <button className="btn" onClick={() => setKnowOpen(true)} disabled={busy || !!blocked}
              title="Paste a chat or document; the map updates and shows exactly what changed">
              <Icon d={I.doc} /><span className="btn-label">Add knowledge</span>
            </button>
            <SimMenu scenarios={scenarios} disabled={busy || tenant !== 'ravenhold'} onPick={simulate}
              title={tenant !== 'ravenhold' ? 'Scenarios run on the Ravenhold Pay workspace' : 'Fire a realistic set of test alarms into the map'} />
            {highlight.size > 0 && (
              <button className="btn subtle" onClick={() => { setHighlight(new Map()); setHlEdges(new Set()) }}>Clear path</button>
            )}
            <button className="btn subtle" onClick={() => graphRef.current?.fit()}>Fit</button>
          </div>
        </section>

        <aside className="side" id="pane-details" aria-label="Steps and details" {...paneProps('details')}>
          <div className="tabs" role="tablist" aria-label="Details" onKeyDown={onTabKeys}>
            {TABS.map((x) => (
              <button key={x.id} id={`tab-${x.id}`} role="tab" aria-selected={tab === x.id} aria-controls="side-panel" tabIndex={tab === x.id ? 0 : -1}
                className={tab === x.id ? 'on' : ''} onClick={() => setTab(x.id)} title={x.title}>
                {x.label}{x.count}
              </button>
            ))}
          </div>

          <div className="panel" id="side-panel" role="tabpanel" aria-labelledby={`tab-${tab}`} tabIndex={0}>
            {tab === 'trace' && (<>
              {events.length === 0 && (
                <div className="placeholder">
                  <b>See exactly how it thinks</b>
                  <span>When the agents work, every step they take shows up here in plain words: who did it, what they checked, and the exact database query behind it. Click a step to light it up on the map.</span>
                </div>
              )}
              {tasks.length > 0 && (
                <section>
                  <h3 className="section-label">Who is doing what <span>tasks passed between agents through the graph</span></h3>
                  {tasks.map((t) => (
                    <div key={t.id} className="task">
                      <div className="row">
                        <span className="who"><Avatar agent={t.agent} />{t.agent[0].toUpperCase() + t.agent.slice(1)}</span>
                        <span className={`status ${t.status}`}>{t.status}</span>
                      </div>
                      <p>{t.instructions}</p>
                    </div>
                  ))}
                </section>
              )}
              {toolEvents.length > 0 && (
                <section>
                  <h3 className="section-label">How it found the answer <span>{plural(toolEvents.length, 'step')} · {queryCount} <Term k="Cypher">{queryCount === 1 ? 'query' : 'queries'}</Term>{viaMcp && <> · tools via <Term k="MCP">MCP</Term></>} · click to see</span></h3>
                  <ol className="steps">
                    {toolEvents.map((ev) => {
                      const call = calls.get(ev.id)
                      const open = openCypher === ev.id
                      return (
                        <li key={ev.id} className={`step ${open ? 'open' : ''} ${ACTION_TOOLS.has(ev.tool) ? 'action' : ''}`}>
                          <button className="step-head" aria-expanded={open} onClick={() => { if (ev.subgraph) applySubgraph(ev.agent, ev.subgraph, true); setOpenCypher(open ? null : ev.id) }}>
                            <i className={`ag-dot ag-${ev.agent}`} aria-hidden />
                            <span className="step-text">
                              <span className="step-name">{TOOL_PLAIN[ev.tool] ?? TOOL_LABELS[ev.tool] ?? ev.tool}</span>
                              {ev.summary && <span className="step-summary">{ev.summary}</span>}
                              <span className="step-arg">{ev.tool}{argSummary(call?.input) ? `(${argSummary(call?.input)})` : '()'}</span>
                            </span>
                            {ACTION_TOOLS.has(ev.tool) && <ActionBadge tool={ev.tool} preview={ev.preview} pageStatus={pageStatus} fixStatus={fixStatus} />}
                          </button>
                          {open && (
                            <div className="cypher">
                              <div className="for-eng">For engineers · the exact database queries this step ran</div>
                              {ev.cypher.length === 0 && <span className="dim">No database query for this step.</span>}
                              {ev.cypher.map((c, i) => <pre key={i}>{c}</pre>)}
                              <div className="for-eng">Raw result</div>
                              <div className="preview">{ev.preview}</div>
                            </div>
                          )}
                        </li>
                      )
                    })}
                  </ol>
                </section>
              )}
            </>)}

            {tab === 'usage' && spend && (<>
              <div className="usage-card">
                <div className="row"><b>Shared demo budget</b><span className="mono">${spend.spent_usd.toFixed(2)} <span className="dim">of ${spend.budget_usd.toFixed(2)}</span></span></div>
                <div className="gp-bar"><i style={{ width: `${Math.min(100, (spend.spent_usd / spend.budget_usd) * 100)}%` }} className={spend.spent_usd > spend.budget_usd * 0.8 ? 'warn' : ''} /></div>
                <div className="dim small">${spend.remaining_usd.toFixed(2)} left · new runs pause automatically at the budget</div>
                <div className="usage-models">
                  <span><Avatar agent="commander" size={20} /> Commander · <b>{modelName(spend.models.commander)}</b></span>
                  <span><Avatar agent="investigator" size={20} /><Avatar agent="historian" size={20} /> Workers · <b>{modelName(spend.models.workers)}</b></span>
                </div>
              </div>
              {busy && (
                <div className="usage-live">
                  <span className="pulse" aria-hidden />
                  This answer so far: <b className="mono">${turnCost(turns[turns.length - 1] ?? { role: 'assistant', text: '', events: [] }).toFixed(4)}</b>
                  <span className="dim">· {liveIds.size} model calls</span>
                </div>
              )}
              <h3 className="section-label">Every AI call <span>all visitors, newest first · nothing is hidden</span></h3>
              {callLog.length === 0 && <p className="dim">No model calls yet. Every call the agents make will appear here with its cost.</p>}
              <ol className="calls">
                {callLog.map((c) => (
                  <li key={c.id} className={liveIds.has(c.id) ? 'live' : ''}>
                    <Avatar agent={c.agent} size={22} />
                    <div className="call-main">
                      <span><b>{roleName(c.agent)}</b> <span className="dim">· {modelName(c.model)}</span></span>
                      <span className="dim small mono">{(c.tokens_in / 1000).toFixed(1)}k in · {(c.tokens_out / 1000).toFixed(1)}k out · {c.at.slice(11, 19)}</span>
                    </div>
                    <span className="call-usd mono">${c.usd.toFixed(4)}</span>
                  </li>
                ))}
              </ol>
            </>)}

            {tab === 'memory' && (<>
              <p className="panel-note">When you correct the agents, they save it here as a lesson, linked to the systems and step-by-step fixes it changes. Every agent reuses it in every future conversation.</p>
              {lessons.length === 0 && <p className="dim">No lessons yet. Correct the agents during an incident and they will remember it.</p>}
              {lessons.map((l) => (
                <div key={l.id} className="lesson">
                  <div className="row">
                    <span className={`kind ${l.kind}`}>{l.kind}</span>
                    {l.reinforced > 0 && <span className="dim small">reinforced ×{l.reinforced}</span>}
                  </div>
                  <p>{l.text}</p>
                  {l.proposed_version && (
                    <RunbookCard id={l.proposed_version} supersedes={l.runbook ?? ''} steps={l.proposed_steps ?? ''}
                      status={rbStatus[l.proposed_version] ?? l.proposed_status ?? 'proposed'} disabled={busy}
                      onDecide={(ok) => decideRunbook(l.proposed_version!, ok)} />
                  )}
                  <div className="chips">
                    {l.services.map((s) => <button key={s} className="chip" onClick={() => focusNode(s)}>{s}</button>)}
                    {l.runbook && <button className="chip" onClick={() => focusNode(l.runbook!)}>{l.runbook}</button>}
                  </div>
                  <div className="mono dim small">learned in {l.session ?? 'unknown session'} · {l.created_at}</div>
                </div>
              ))}
            </>)}

            {tab === 'sessions' && (<>
              <p className="panel-note">Every conversation is saved in the graph. Open one to pick up where you left off, even after a restart.</p>
              {sessions.map((s) => (
                <button key={s.id} className={`session ${s.id === sessionId ? 'on' : ''}`} aria-current={s.id === sessionId ? 'true' : undefined}
                  onClick={() => { loadSession(s.id); if (layout === 'mobile') setView('chat') }} disabled={busy}>
                  <div className="row"><span className="mono">{s.id}</span><span className="dim small">{s.turns} turns</span></div>
                  <div className="dim small">
                    {s.started_at}
                    {s.incidents.length > 0 && ` · ${s.incidents.join(', ')}`}
                    {s.lessons > 0 && ` · ${s.lessons} lesson${s.lessons > 1 ? 's' : ''}`}
                  </div>
                </button>
              ))}
            </>)}
          </div>
        </aside>
      </main>
    </div>
  )
}

/** Human in the loop for procedural memory: a correction proposes a new runbook version; a person approves it. */
function RunbookCard({ id, supersedes, steps, status, disabled, onDecide }: {
  id: string; supersedes: string; steps: string; status: string; disabled: boolean; onDecide: (approve: boolean) => void
}) {
  return (
    <div className={`approval rb ${status === 'active' ? 'sent' : status === 'rejected' ? 'declined' : 'awaiting_approval'}`} onClick={(e) => e.stopPropagation()}>
      <div className="ap-head">
        <svg viewBox="0 0 16 16" aria-hidden><path d="M4 2.5h6l2 2v9H4zM6 6.5h4M6 9h4M6 11.5h2.5" /></svg>
        {status === 'proposed' && <b>Approve the updated fix {id}?</b>}
        {status === 'active' && <b>{id} is now the fix everyone uses ✓</b>}
        {status === 'rejected' && <b>{id} was rejected</b>}
        {supersedes && <span className="dim">replaces {supersedes}</span>}
      </div>
      {steps && <pre className="rb-steps">{steps}</pre>}
      {status === 'proposed' ? (
        <div className="ap-actions">
          <button className="btn primary" disabled={disabled} onClick={() => onDecide(true)}>Approve new version</button>
          <button className="btn subtle" disabled={disabled} onClick={() => onDecide(false)}>Reject</button>
          <span className="dim small">Until approved, the agents keep using {supersedes || 'the current version'}.</span>
        </div>
      ) : (
        <div className="dim small">{status === 'active' ? 'Approved by you. Every future conversation uses this version.' : 'The agents keep using the previous version.'}</div>
      )}
    </div>
  )
}

type FixResult = {
  status: string; cleared?: number | null; recovered?: boolean; seconds_to_mitigate?: number | null
  still_firing_unrelated?: { id: string; title: string; service: string }[]
}

const duration = (s?: number | null) => (s == null ? '' : s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`)

/** Closed loop: the agent proposes the fix; a human applies it; recovery is then verified from the graph. */
function FixCard({ fx, result, disabled, onDecide, onVerify }: {
  fx: Extract<AgentEvent, { type: 'fix_proposal' }>; result: FixResult; disabled: boolean
  onDecide: (approve: boolean) => void; onVerify: () => void
}) {
  const st = result.status
  const unrelated = result.still_firing_unrelated ?? []
  return (
    <div className={`approval fix ${st === 'applied' ? 'sent' : st === 'declined' ? 'declined' : 'awaiting_approval'}`} onClick={(e) => e.stopPropagation()}>
      <div className="ap-head">
        <svg viewBox="0 0 16 16" aria-hidden><path d="M10.5 2.5a3 3 0 0 0-3.9 3.7L2.5 10.3a1.4 1.4 0 0 0 2 2l4.1-4.1a3 3 0 0 0 3.7-3.9l-1.8 1.8-1.6-.4-.4-1.6z" /></svg>
        {st === 'applied' ? <b>Fix applied and verified ✓</b> : st === 'declined' ? <b>Fix not applied</b>
          : <b>Apply {fx.runbook_id} to {fx.service_name || fx.service}?</b>}
        <span className="dim">{fx.incident_id}</span>
      </div>
      {st === 'applied' ? (
        <ul className="fix-result">
          <li><b>{result.cleared ?? fx.alarms} alarms cleared</b> on {fx.service_name || fx.service} and the systems that depend on it.</li>
          <li>{result.recovered ? 'Verified from the graph: no alarm related to this incident is still firing.' : 'Some related alarms are still firing.'}</li>
          {unrelated.length > 0 && <li>Still firing, and unrelated: {unrelated.map((a) => a.service).join(', ')} (the alarm flagged as noise earlier).</li>}
          {result.seconds_to_mitigate != null && <li>Mitigated {duration(result.seconds_to_mitigate)} after the incident was opened.</li>}
        </ul>
      ) : (
        <>
          {fx.reason && <p>{fx.reason}</p>}
          <div className="dim small">Expected effect: clears {fx.alarms} alarms across {fx.systems.length} systems
            ({fx.systems.slice(0, 4).join(', ')}{fx.systems.length > 4 ? '…' : ''}).</div>
          {fx.steps && <pre className="rb-steps">{fx.steps}</pre>}
        </>
      )}
      {st === 'awaiting_approval' ? (
        <div className="ap-actions">
          <button className="btn primary" disabled={disabled} onClick={() => onDecide(true)}>Apply fix</button>
          <button className="btn subtle" disabled={disabled} onClick={() => onDecide(false)}>Not now</button>
          <span className="dim small">Nothing changes until you approve. In this demo, applying updates the system state in the graph.</span>
        </div>
      ) : st === 'declined' ? <div className="dim small">You chose not to apply it.</div> : (
        <button className="link-btn" disabled={disabled} onClick={onVerify}>Ask the agents to confirm recovery →</button>
      )}
    </div>
  )
}

/** Human in the loop: the agents ask before paging anyone. */
function ApprovalCard({ a, status, disabled, onDecide }: {
  a: Extract<AgentEvent, { type: 'approval' }>; status: string; disabled: boolean; onDecide: (approve: boolean) => void
}) {
  return (
    <div className={`approval ${status}`} onClick={(e) => e.stopPropagation()}>
      <div className="ap-head">
        <svg viewBox="0 0 16 16" aria-hidden><path d="M8 2.5a4 4 0 0 1 4 4v2.2l1.2 2H2.8l1.2-2V6.5a4 4 0 0 1 4-4zM6.6 13a1.5 1.5 0 0 0 2.8 0" /></svg>
        {status === 'awaiting_approval' && <b>Approve paging {a.name}?</b>}
        {status === 'sent' && <b>Paged {a.name} ✓</b>}
        {status === 'declined' && <b>Not paged: {a.name}</b>}
        <span className="dim">{a.incident_id}</span>
      </div>
      {a.message && <p>"{a.message}"</p>}
      {status === 'awaiting_approval' ? (
        <div className="ap-actions">
          <button className="btn primary" disabled={disabled} onClick={() => onDecide(true)}>Approve page</button>
          <button className="btn subtle" disabled={disabled} onClick={() => onDecide(false)}>Don't page</button>
          <span className="dim small">Nothing is sent until you approve. In this demo, approving records the page in the graph.</span>
        </div>
      ) : (
        <div className="dim small">{status === 'sent' ? 'Approved by you and recorded in the graph.' : 'You declined this page.'}</div>
      )}
    </div>
  )
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/** Steps that changed something. A page request shows its live approval status instead of a static label. */
function ActionBadge({ tool, preview, pageStatus, fixStatus }: {
  tool: string; preview: string; pageStatus: Record<string, string>; fixStatus: Record<string, FixResult>
}) {
  if (tool === 'propose_fix') {
    const id = preview.match(/FIX-[0-9a-f]+/)?.[0]
    const st = id ? fixStatus[id]?.status : undefined
    if (st === 'applied') return <span className="badge ok">applied ✓</span>
    if (st === 'declined') return <span className="badge muted">not applied</span>
    return <span className="badge" title="Waiting for your approval in the chat">needs approval</span>
  }
  if (tool === 'page_person') {
    const id = preview.match(/PAGE-[0-9a-f]+/)?.[0]
    const st = id ? pageStatus[id] : undefined
    if (st === 'sent') return <span className="badge ok">approved ✓</span>
    if (st === 'declined') return <span className="badge muted">declined</span>
    return <span className="badge" title="Waiting for your approval in the chat">needs approval</span>
  }
  return <span className="badge muted" title="This step changed the graph">action taken</span>
}

function turnCost(t: Turn) {
  return t.events.reduce((a, e) => a + (e.type === 'usage' ? e.usd : 0), 0)
}

function Working({ events }: { events: AgentEvent[] }) {
  const state = (agent: string) => {
    const mine = events.filter((e) => 'agent' in e && e.agent === agent)
    const lastCall = [...mine].reverse().find((e) => e.type === 'tool_call') as Extract<AgentEvent, { type: 'tool_call' }> | undefined
    const done = mine.some((e) => e.type === 'task_status' && e.status === 'done')
    if (agent !== 'commander' && done) return { label: 'done', detail: '' }
    if (lastCall) return { label: 'working', detail: TOOL_PLAIN[lastCall.tool] ?? TOOL_LABELS[lastCall.tool] ?? lastCall.tool }
    return { label: agent === 'commander' ? 'thinking' : 'waiting', detail: '' }
  }
  return (
    <div className="working">
      {AGENTS.map((a) => {
        const s = state(a.id)
        return (
          <div key={a.id} className={`work-row is-${s.label}`}>
            <Avatar agent={a.id} />
            <b>{a.name}</b>
            <span className="dim">{s.detail || s.label}</span>
            {s.label === 'working' || s.label === 'thinking' ? <span className={`pulse ag-${a.id}`} aria-hidden /> : null}
          </div>
        )
      })}
    </div>
  )
}
