import { useEffect, useState } from 'react'
import { getJSON, type AgentEvent, type GEdge, type GNode } from './api'
import { TOOL_PLAIN } from './Plain'

/* Everything on these panels comes from /api/about (counted live from the graph) or from the answer's own trace events,
   so the numbers can't drift from what the code and data actually do. */

type Dataset = { key: string; label: string; count: number; source: string; real_world: string; sample: Record<string, unknown> }
export type About = {
  datasets: Dataset[]
  totals: { arr: number; teams: number; moves: number; open_todos: number; decisions: number }
  labels: { label: string; count: number }[]
  relationships: { type: string; count: number }[]
  falkordb_version: string | null
  models: { provider: string; commander: string; workers: string }
  tools?: { transport: string; tools: number; enabled: boolean }
  scenarios: Record<string, number>
  nodes: number
}

export function useAbout(open: boolean, tenant = 'ravenhold') {
  const [about, setAbout] = useState<{ tenant: string; data: About } | null>(null)
  useEffect(() => {
    if (open && about?.tenant !== tenant) getJSON(`/api/about?tenant=${tenant}`).then((d) => setAbout({ tenant, data: d as About })).catch(() => {})
  }, [open, about, tenant])
  return about?.tenant === tenant ? about.data : null
}

const money = (n: number) => (n >= 1e6 ? `$${(n / 1e6).toFixed(2)}M` : `$${Math.round(n / 1e3)}K`)

const DATA_NOTE: Record<string, string> = {
  systems: 'like Checkout, Payments API and PgBouncer, and which ones depend on which',
  people: 'in teams, with who is on call and who changed teams',
  customers: 'with how much each one pays per year',
  'past problems': 'with what broke, who fixed it and how',
  documents: 'reviews of past problems and design notes',
  chats: 'team chat messages from past incidents',
  'to-dos': 'tickets, including fixes that were promised but never done',
  'fixes written down': 'step-by-step runbooks',
  projects: 'who leads them, who works on them, their tickets and decisions',
}

/** Chapter 2 of the guide: the demo data, counted live, with where every piece came from. */
export function DataChapter({ about }: { about: About | null }) {
  const [openKey, setOpenKey] = useState<string | null>(null)
  if (!about) return <p className="dim">Counting the data…</p>
  const ds = about.datasets.find((d) => d.key === openKey)
  return (
    <>
      <p className="lead">This is <b>everything the agents can know</b>. We wrote a <b>made-up payments company</b>, Ravenhold
        Pay, by hand: its systems, the people who run them, its customers, and a history of past outages, chats, documents,
        to-dos and projects. Every name, system and number is invented. The agents can only answer from this, plus anything
        you teach them. The numbers are counted live from the map. <b>Tap a card</b> to see a real record.</p>
      <div className="stat-grid">
        {about.datasets.map((d) => (
          <button key={d.key} className={`stat ${openKey === d.key ? 'on' : ''}`} onClick={() => setOpenKey(openKey === d.key ? null : d.key)}>
            <b>{d.count}</b><span>{d.key}</span><em>{DATA_NOTE[d.key]}</em>
          </button>
        ))}
      </div>
      {ds && (
        <div className="source-card">
          <div><span>File</span><code>{ds.source}</code></div>
          <div><span>In the graph</span><code>{ds.count} × (:{ds.label})</code></div>
          <div><span>Real-world source</span><b>{ds.real_world}</b></div>
          <div><span>Sample record</span><code className="sample">{JSON.stringify(ds.sample)}</code></div>
        </div>
      )}
      <p className="dim small">Customers pay {money(about.totals.arr)} a year in total · {about.totals.teams} teams ·
        {' '}{about.totals.moves} people changed teams · {about.totals.open_todos} promised fixes still open · {about.totals.decisions} recorded decisions.</p>

      <details className="glossary">
        <summary>For the curious: how we made this data</summary>
        <ul className="made">
          <li><b>Written by hand, not generated at runtime.</b> Everything is one Python file,{" "}
            <code>backend/ravenhold/seed_data.py</code>, written with an AI coding assistant (Claude Opus 5.5 via Claude
            Code) and reviewed by hand. No data library (like Faker) and no AI call is used when the data is loaded.</li>
          <li><b>Names are fictional.</b> People have common English first and last names. Customer names
            (Orbit Rides, Northwind…) and every product and system are invented; anything about the company itself starts
            with "Ravenhold". We did not run a trademark search, so a name may match a real business by coincidence.</li>
          <li><b>Planted on purpose</b> so the demo questions have interesting answers: Sarah fixed a PgBouncer outage in
            March (INC-142) and then moved teams; Marcus is on call but has never touched PgBouncer; a chat says "don't
            restart the database"; a promised fix (TKT-512) was never done; a decision (ADR-12) traces back to chats and
            a design doc.</li>
          <li><b>Every link is written in the seed file.</b> "PgBouncer depends on the payments database" is a line in
            <code>DEPENDS_ON</code>; nothing is guessed. The only links an AI creates are from <b>Add knowledge</b>, and
            each one is saved with the text it came from.</li>
          <li><b>Alerts</b> come from three hand-written storms in <code>backend/ravenhold/scenarios.py</code>
            ({Object.values(about.scenarios).join(', ')} alarms). They enter the graph only when you press Simulate.</li>
        </ul>
      </details>

      <details className="glossary">
        <summary>In a real company: which tool each list stands in for</summary>
        <p className="small">Ravenhold Pay is not connected to any real tool. In a real company each source below would be a
          live connector; here each one is a list in the seed file.</p>
        <table className="src-table">
          <thead><tr><th>Real-world tool</th><th>Demo source</th></tr></thead>
          <tbody>
            <tr><td>Slack (team chat)</td><td><code>MESSAGES</code> → (:Message)</td></tr>
            <tr><td>Jira / Linear (to-dos)</td><td><code>TICKETS</code> → (:Ticket)</td></tr>
            <tr><td>GitHub (code history)</td><td><code>CONTRIBUTED_TO</code> commit counts per person and system</td></tr>
            <tr><td>PagerDuty (on call, alarms)</td><td><code>ON_CALL</code> + <code>scenarios.py</code> alert storms</td></tr>
            <tr><td>Confluence / Docs</td><td><code>DOCS</code>, <code>DECISIONS</code> → (:Doc), (:Decision)</td></tr>
            <tr><td>Service catalog</td><td><code>SERVICES</code>, <code>DEPENDS_ON</code></td></tr>
            <tr><td>HR directory, CRM</td><td><code>PEOPLE</code>, <code>TEAMS</code>, <code>CUSTOMERS</code></td></tr>
            <tr><td>Anything you paste</td><td><b>Add knowledge</b> box (the only input an AI turns into links)</td></tr>
          </tbody>
        </table>
      </details>
    </>
  )
}

// How each relationship type gets into the graph, from reading seed.py, tools.py, agents.py and ingest.py.
const AGENT_WRITTEN = new Set(['ON', 'TO', 'APPLIES_TO', 'LEARNED_IN', 'ABOUT_INCIDENT', 'REFINES', 'SUPERSEDES', 'PERFORMED',
  'HANDLED', 'TOUCHED', 'PRODUCED', 'HAS_TURN', 'PREVIOUS', 'IN_SESSION', 'DELEGATED', 'FOR_TASK'])
const INGESTED = new Set(['CHANGED', 'FROM_SOURCE'])

/** The technical panel: data model, storage and indexing, answering pipeline, memory, limits. */
export function TechChapter({ about }: { about: About | null }) {
  if (!about) return <p className="dim">Loading…</p>
  const seeded = about.relationships.filter((r) => !AGENT_WRITTEN.has(r.type) && !INGESTED.has(r.type))
  return (
    <div className="tech">
      <details open>
        <summary>Built for all three Graph Hacks tracks</summary>
        <p>One agent team on one FalkorDB graph, entered in all three tracks at once:</p>
        <ul>
          <li><b>Agents That Act on Connected Data:</b> multi-hop graph walks and graph algorithms as tools, picked and called
            through MCP; the agents open incidents and ask to page the right person; every step shows its Cypher.</li>
          <li><b>Agent Memory and Coordination:</b> episodic, semantic and procedural memory (Episodes, Lessons, runbook versions
            you approve), handoffs through Task nodes ("Who is doing what"), tested with 100 conversations at once, and a
            private graph for every visitor.</li>
          <li><b>Company Brain:</b> people, teams, projects, docs, chats, tickets and decisions in one graph; who owns vs who
            knows; decisions traced to their sources; <b>Add knowledge</b> keeps it current, with sources.</li>
        </ul>
      </details>
      <details>
        <summary>Data model</summary>
        <p><b>Things (node labels), counted live:</b> {about.labels.map((l) => `${l.label} ${l.count}`).join(' · ')}</p>
        <p><b>Links loaded from the seed file</b> (explicit, so no confidence score): {seeded.map((r) => `${r.type} ${r.count}`).join(' · ')}.
          Some carry facts, like dates on MEMBER_OF (team history) and commit counts on CONTRIBUTED_TO.</p>
        <p><b>Links the agents write while working</b> (code in <code>tools.py</code>, <code>agents.py</code>): alerts ON
          systems, pages TO people, lessons that APPLY_TO systems, runbook versions that SUPERSEDE older ones, and
          sessions, turns and tasks (HAS_TURN, DELEGATED, FOR_TASK…). Incidents the agents open get the same
          AFFECTED / ROOT_CAUSE / RESOLVED links as seeded ones.</p>
        <p><b>Links an AI extracts</b> (only from <b>Add knowledge</b>, <code>ingest.py</code>): team moves, on call,
          ownership, dependencies, decisions and to-dos. There is no confidence score: instead every change is a
          (:Change) node linked FROM_SOURCE to the pasted text, and ids must already exist in the graph or the fact is skipped.</p>
      </details>
      <details>
        <summary>Storage and indexing</summary>
        <ul>
          <li><b>Graph database:</b> FalkorDB {about.falkordb_version ?? ''} (FalkorDB Cloud free tier for the live site, Docker
            locally). One graph per workspace, a private copy per visitor (GRAPH.COPY), and an <code>ops</code> graph for the spend ledger.</li>
          <li><b>Vector index:</b> inside FalkorDB itself (no separate vector database): HNSW on <code>Incident.embedding</code>,
            256 dimensions, cosine distance, top 6 per query.</li>
          <li><b>Embeddings:</b> not an AI model. <code>embed.py</code> hashes words and word pairs into 256 numbers, which
            catches shared wording ("pool exhausted" ≈ "connections queueing") but not deep meaning. Free, offline, deterministic.</li>
          <li><b>What is embedded:</b> each incident's title + summary, one vector per incident (no chunking; the
            "chunk" is the node itself, so its id, date and links come with it). Documents, chats, tickets and lessons
            use FalkorDB's <b>full-text index</b> instead.</li>
          <li><b>Other indexes:</b> an exact-match index and a unique constraint on <code>id</code> for every main label.</li>
          <li><b>When indexing runs:</b> at seed time (the server seeds any missing workspace on startup), then
            incrementally: opening or resolving an incident writes its vector, and full-text indexes update on every write.
            Rebuild everything: <code>cd backend &amp;&amp; uv run python -m ravenhold.seed</code>.</li>
        </ul>
      </details>
      <details>
        <summary>How a question gets answered</summary>
        <ol>
          <li><b>No separate intent classifier.</b> The Commander ({about.models.commander}) reads your question plus a
            list of the company's system and people ids, and decides which tools to call. Its instructions are
            <code>COMMANDER_SYSTEM</code> in <code>backend/ravenhold/agents.py</code>.</li>
          <li><b>Tools come from MCP.</b> {about.tools?.transport === 'mcp-stdio'
            ? <>Right now the agents get their {about.tools.tools} graph tools from Ravenhold's MCP server
              (<code>mcp_server.py --agents</code>, a separate process over stdio): <code>tools/list</code> gives each
              agent its catalogue and every step runs with <code>tools/call</code>. Only the workspace and session are
              filled in by the server (<code>mcp_bridge.py</code>), so the model never picks whose graph to use.</>
            : <>MCP is not active on this server right now, so tools run in-process (each step in the trace says which).</>}
            The same server works with any MCP client, such as Claude Desktop or Claude Code.</li>
          <li><b>Linking words to the graph:</b> the model picks ids from that list; every tool checks them against the graph and
            returns close matches if one is wrong ("payment-api" → payments-api).</li>
          <li><b>Graph walk:</b> Cypher in <code>tools.py</code>. What breaks: DEPENDS_ON up to 6 hops upstream. What could be
            causing it: up to 4 hops downstream. Similar incidents: within 2 hops. Custom questions: read-only, 100 rows, 3-second timeout.</li>
          <li><b>Retrieval:</b> similar incidents are found three ways at once (graph neighbours, full-text, vector) and the
            scores are added up; the top 5 are kept. No separate re-ranking model.</li>
          <li><b>Delegation:</b> for bigger questions the Investigator and Historian ({about.models.workers}) work in parallel and
            report back through Task nodes in the graph.</li>
          <li><b>Answer:</b> the Commander writes the answer and cites [ids]; the server then checks every cited id exists in
            the graph and flags any that don't. The one-line step summaries are written by code (<code>explain.py</code>), not by an AI.</li>
        </ol>
      </details>
      <details>
        <summary>Memory ("It remembers")</summary>
        <p>Lessons are (:Lesson) nodes linked to the systems, runbook and conversation they came from, full-text indexed the
          moment they are written. They last across conversations in your private copy until you press Reset (copies unused for
          24 hours are deleted). A corrected fix becomes a new runbook version that waits for your approval. Restarting the
          server does not wipe memory; your own copy never changes the shared template.</p>
      </details>
      <details>
        <summary>Limits</summary>
        <ul>
          <li>No real paging: an approved page is a node in the graph; nobody is called or messaged.</li>
          <li>No live data: everything comes from the seed file, the alert storms or what you paste.</li>
          <li>Small graph: {about.nodes} nodes in the template (the scale test goes to 20,000 systems, all queries under a second).</li>
          <li>Embeddings match wording, not meaning; swapping in a real embedding model needs no query changes.</li>
          <li>The only usage limit is the shared demo credit, shown live in the spend pill and the Cost tab; when it runs out the agents pause.</li>
        </ul>
      </details>
    </div>
  )
}

/* ---------- per-answer "Show how this was answered" ---------- */

type ToolResult = Extract<AgentEvent, { type: 'tool_result' }>
type ToolCall = Extract<AgentEvent, { type: 'tool_call' }>
type UsageEv = Extract<AgentEvent, { type: 'usage' }>

function idsIn(input: Record<string, unknown>): string[] {
  const out: string[] = []
  for (const [k, v] of Object.entries(input)) {
    if (/_ids?$/.test(k)) (Array.isArray(v) ? v : [v]).forEach((x) => typeof x === 'string' && out.push(x))
  }
  return out
}

export function AnswerTrace({ events, seconds, citations, onShow }: {
  events: AgentEvent[]; seconds: number | null; citations?: { verified: string[]; unknown: string[] }
  onShow: (sg: { nodes: GNode[]; edges: GEdge[] }) => void
}) {
  const calls = events.filter((e): e is ToolCall => e.type === 'tool_call')
  const results = new Map(events.filter((e): e is ToolResult => e.type === 'tool_result').map((e) => [e.id, e]))
  const usage = events.filter((e): e is UsageEv => e.type === 'usage')
  if (!calls.length && !usage.length) return null
  const graphSteps = [...results.values()].filter((r) => r.tool !== 'delegate')
  const mcpOnly = graphSteps.length > 0 && graphSteps.every((r) => r.via === 'mcp')

  const entities = [...new Set(calls.flatMap((c) => idsIn(c.input)))]
  const names = new Map<string, string>()
  const edges = new Map<string, GEdge>()
  const nodes = new Map<string, GNode>()
  for (const r of results.values()) {
    r.subgraph?.nodes.forEach((n) => { names.set(n.id, n.name || n.id); nodes.set(n.id, n) })
    r.subgraph?.edges.forEach((e) => edges.set(`${e.source}|${e.type}|${e.target}`, e))
  }
  const path = [...edges.values()]
  const byModel = new Map<string, { calls: number; tin: number; tout: number; usd: number }>()
  usage.forEach((u) => {
    const m = byModel.get(u.model) ?? { calls: 0, tin: 0, tout: 0, usd: 0 }
    byModel.set(u.model, { calls: m.calls + 1, tin: m.tin + u.tokens_in, tout: m.tout + u.tokens_out, usd: m.usd + u.usd })
  })
  const cited = citations ? citations.verified.length + citations.unknown.length : 0
  const flag = !citations || !cited
    ? { cls: 'warn', text: calls.length ? 'No ids cited, so nothing could be checked' : 'Uncertain: answered without looking anything up' }
    : citations.unknown.length
      ? { cls: 'warn', text: `Uncertain: ${citations.unknown.length} of ${cited} cited ids are not in the graph (${citations.unknown.join(', ')})` }
      : { cls: 'ok', text: `Grounded: all ${cited} cited ids exist in the graph` }

  return (
    <details className="how" onClick={(e) => e.stopPropagation()}>
      <summary>Show how this was answered</summary>
      <div className={`how-flag ${flag.cls}`}>{flag.text}</div>

      <div className="how-sec"><b>1 · Things recognised in the question</b>
        {entities.length
          ? <div className="chips">{entities.map((id) => <button key={id} className="chip" data-node={id}>{id}</button>)}</div>
          : <span className="dim"> none named directly; the agent started from an overview tool.</span>}
      </div>

      <div className="how-sec"><b>2 · Tools used, in order</b>
        {mcpOnly && <span className="dim"> · picked from the MCP server's tool list and run over MCP</span>}
        <ol className="how-tools">
          {calls.map((c) => {
            const r = results.get(c.id)
            return (
              <li key={c.id}>
                <details>
                  <summary><span className="mono">{c.agent}</span> · {TOOL_PLAIN[c.tool] ?? c.tool}
                    {r?.cypher.length ? <em> · {r.cypher.length} {r.cypher.length === 1 ? 'query' : 'queries'}</em> : null}</summary>
                  {r?.summary && <p>{r.summary}</p>}
                  <div className="kv"><span>Input</span><code>{JSON.stringify(c.input)}</code></div>
                  {r && <div className="kv"><span>Output (start)</span><code>{r.preview}</code></div>}
                  {r?.cypher.map((q, i) => <pre key={i} className="cypher">{q}</pre>)}
                </details>
              </li>
            )
          })}
        </ol>
      </div>

      <div className="how-sec"><b>3 · Path through the map</b> <span className="dim">({path.length} links)</span>
        {path.length > 0 && (
          <>
            <button className="btn subtle tiny" onClick={() => onShow({ nodes: [...nodes.values()], edges: path })}>Highlight on the map</button>
            <ul className="how-path">
              {path.slice(0, 30).map((e) => (
                <li key={`${e.source}|${e.type}|${e.target}`}>
                  <button className="chip" data-node={e.source}>{names.get(e.source) ?? e.source}</button>
                  <span className="rel">{e.type}</span>
                  <button className="chip" data-node={e.target}>{names.get(e.target) ?? e.target}</button>
                </li>
              ))}
              {path.length > 30 && <li className="dim">and {path.length - 30} more (all highlighted on the map)</li>}
            </ul>
          </>
        )}
      </div>

      {citations && cited > 0 && (
        <div className="how-sec"><b>4 · Sources cited</b>
          <div className="chips">
            {citations.verified.map((id) => <button key={id} className="chip" data-node={id}>{id}</button>)}
            {citations.unknown.map((id) => <span key={id} className="chip unverified">{id} ?</span>)}
          </div>
        </div>
      )}

      <div className="how-sec"><b>5 · Models and cost</b>
        <table className="src-table">
          <thead><tr><th>Model</th><th>Calls</th><th>Tokens in / out</th><th>Cost</th></tr></thead>
          <tbody>{[...byModel].map(([m, v]) => (
            <tr key={m}><td className="mono">{m}</td><td>{v.calls}</td><td>{v.tin.toLocaleString()} / {v.tout.toLocaleString()}</td><td>${v.usd.toFixed(4)}</td></tr>
          ))}</tbody>
        </table>
        {seconds != null && <span className="dim">Took {seconds} s from question to answer.</span>}
      </div>
    </details>
  )
}
