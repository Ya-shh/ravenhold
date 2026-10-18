import { useState } from 'react'
import type { Alert, Scenario } from './api'

/** What each graph tool does, in words a non-technical person understands. The technical name is shown beside it. */
export const TOOL_PLAIN: Record<string, string> = {
  delegate: 'Handing tasks to the other two agents',
  update_knowledge: 'Updating the company map from what you said',
  situation_report: 'Getting the big picture of the whole company',
  correlate_alerts: 'Tracing all the alarms back to one cause',
  blast_radius: 'Checking what else breaks and which customers feel it',
  root_cause_candidates: 'Looking underneath for what could be causing it',
  find_experts: 'Finding who knows this system best',
  similar_incidents: 'Looking for the same problem in the past',
  get_runbook: 'Reading the step-by-step fix',
  open_follow_ups: 'Checking for promised fixes that never happened',
  trace_decisions: 'Tracing why it was built this way',
  project_overview: 'Checking who is working on what, and what is at risk',
  search_knowledge: 'Searching documents, chats and tickets',
  critical_services: 'Finding the parts everything depends on',
  shortest_connection: 'Finding how two things are linked',
  graph_query: 'Asking the company map a custom question',
  recall_memory: 'Remembering lessons from earlier conversations',
  record_lesson: 'Saving a new lesson so it is never forgotten',
  declare_incident: 'Officially opening an incident',
  page_person: 'Asking you to approve calling an engineer',
  propose_fix: 'Proposing the fix for you to approve',
  check_recovery: 'Checking whether everything has recovered',
  create_ticket: 'Creating a to-do so it gets fixed properly',
  resolve_incident: 'Closing the incident and recording the fix',
}

export const GLOSSARY: Record<string, string> = {
  alert: 'An automatic warning from a system, like a smoke detector going off. Many alerts can come from one real problem.',
  incident: 'Something is broken and affecting customers, so a team works on it until it is fixed.',
  'root cause': 'The one thing that actually broke. Everything else failing is a side effect of it.',
  'on-call': 'The engineer whose turn it is to respond tonight. Not always the person who knows the system best.',
  runbook: 'A step-by-step fix written down from past incidents, like a recipe.',
  graph: 'A map of how everything connects: systems, people, past problems and decisions are dots, and the lines between them are relationships.',
  ARR: 'Annual recurring revenue: how much money a customer pays per year.',
  Cypher: 'The query language used to ask the graph questions, like SQL for connected data.',
  agent: 'An AI worker that can use tools by itself. Ravenhold has three that work as a team.',
  MCP: 'Model Context Protocol: a standard way for AI agents to discover tools and call them. Ravenhold\'s agents get their graph tools from an MCP server.',
}

/** What a record code means, from its prefix. Engineers write these codes everywhere; most people have never seen them. */
const CODE_KIND: [RegExp, string][] = [
  [/^INC-/, 'Outage record'], [/^RB-.*-v\d+$/, 'Written fix (runbook), updated version'], [/^RB-/, 'Written fix (runbook)'],
  [/^TKT-/, 'To-do ticket'], [/^ADR-/, 'Design decision on record'], [/^MSG-/, 'Chat message'],
  [/^PM-/, 'Postmortem: the write-up after an outage'], [/^DD-/, 'Design document'], [/^PRJ-/, 'Project'],
  [/^PAGE-/, 'Request to call an engineer'], [/^FIX-/, 'Proposed fix'], [/^LSN-/, 'Lesson the agents learned'],
  [/^CHG-/, 'Change made from pasted knowledge'], [/^SRC-/, 'Pasted message or document'],
]
const LABEL_KIND: Record<string, string> = {
  Service: 'System', Person: 'Person', Team: 'Team', Customer: 'Customer', Product: 'Product', Incident: 'Outage record',
  Runbook: 'Written fix (runbook)', Doc: 'Document', Message: 'Chat message', Ticket: 'To-do ticket',
  Decision: 'Design decision on record', Lesson: 'Lesson the agents learned', Project: 'Project', Alert: 'Alarm',
  Page: 'Request to call an engineer', Fix: 'Proposed fix',
}
const DOC_KIND: Record<string, string> = { postmortem: 'Postmortem: the write-up after an outage', design: 'Design document' }

/** The codes in the glossary, with an example of each, for the Guide and anywhere a key is useful. */
export const CODES: [string, string][] = [
  ['INC-142', 'An outage record: what broke, when, and who fixed it.'],
  ['RB-7', 'A runbook: the written, step-by-step fix. RB-7-v2 is a newer version that a human approved.'],
  ['TKT-512', 'A to-do ticket in the team\'s task tracker.'],
  ['ADR-12', 'A design decision on record: what was decided and why.'],
  ['PM-142', 'A postmortem: the write-up after an outage, with what went wrong and what to change.'],
  ['DD-31', 'A design document: a proposal for how to build something.'],
  ['MSG-5', 'A chat message, for example from Slack.'],
  ['PRJ-POOL', 'A project a team is working on.'],
  ['pgbouncer', 'Lower-case names are systems or people. PgBouncer is the connection pool in front of the payments database.'],
]

/** One line saying what a cited id is, using the live graph when the node is on it. */
export function describeId(id: string, node?: { label: string; name: string; about?: string | null }) {
  const kind = (node?.label === 'Doc' && node.about && DOC_KIND[node.about])
    || CODE_KIND.find(([re]) => re.test(id))?.[1] || (node && LABEL_KIND[node.label]) || 'Record'
  if (!node) return { kind, text: '' }
  const name = node.name && node.name !== id ? node.name.replace(/^(Postmortem|Design):\s*/, '') : ''
  const about = node.about && !DOC_KIND[node.about] && node.about !== node.label.toLowerCase() ? node.about : ''
  if (node.label === 'Person') return { kind: 'Person', text: [node.name, node.about].filter(Boolean).join(', ') }
  return { kind, text: [name, about].filter(Boolean).join(': ') }
}

/** A word with a dotted underline that explains itself on hover or tap. */
export function Term({ k, children }: { k: keyof typeof GLOSSARY | string; children?: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <span className="term" tabIndex={0} onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} onClick={(e) => { e.stopPropagation(); setOpen((o) => !o) }}>
      {children ?? k}
      {open && <span className="term-pop" role="tooltip"><b>{k}</b>{GLOSSARY[k]}</span>}
    </span>
  )
}

const FLOW = ['Read every alarm', 'Find the one cause', 'Find who can fix it', 'Call them and give the fix']

/** The alert banner as a story: what happened, why it matters, where, and what investigating will do. */
export function AlertStory({ alerts, scenario, price, busy, investigated = false, onInvestigate, onClear }: {
  alerts: Alert[]; scenario?: Scenario; price: number; busy: boolean; investigated?: boolean
  onInvestigate: () => void; onClear: () => void
}) {
  const [showAll, setShowAll] = useState(false)
  const systems = [...new Map(alerts.map((a) => [a.service, a.service_name || a.service])).values()]
  const critical = alerts.filter((a) => a.severity === 'critical').length
  if (investigated) {
    return (
      <div className="alert-story compact" role="status">
        <span className="ab-dot" aria-hidden />
        <div className="as-compact-text">
          <b>Practice outage still running{scenario ? `: ${scenario.name}` : ''}</b>
          <span>{alerts.length} {alerts.length === 1 ? 'alarm' : 'alarms'} · investigated ✓ · stop the test when you're done</span>
        </div>
        <button className="btn" onClick={onClear} disabled={busy}>Stop test</button>
        <button className="link-btn" onClick={onInvestigate} disabled={busy} title={`Runs the agents again. Uses about $${price.toFixed(2)} of credit.`}>
          Investigate again
        </button>
      </div>
    )
  }
  return (
    <div className="alert-story" role="status">
      <div className="as-top">
        <span className="ab-dot" aria-hidden />
        <span className="as-kicker">Test incident running</span>
        {scenario && <span className="as-name">{scenario.name}</span>}
      </div>
      {scenario?.impact && <p className="as-impact">{scenario.impact}</p>}
      <p className="as-what">
        <b>{alerts.length} <Term k="alert">alarms</Term></b> went off in <b>{systems.length} systems</b>
        {critical > 0 && <> ({critical} serious)</>}. They look like separate problems, but they usually come from <Term k="root cause">one cause</Term>.
      </p>
      <div className="as-systems">
        {systems.slice(0, showAll ? systems.length : 5).map((s) => <span key={s}>{s}</span>)}
        {systems.length > 5 && (
          <button onClick={() => setShowAll((v) => !v)}>{showAll ? 'show less' : `+${systems.length - 5} more`}</button>
        )}
      </div>
      {showAll && (
        <ul className="as-list">
          {alerts.map((a) => (
            <li key={a.id}><i className={`sev ${a.severity}`} /><b>{a.service_name || a.service}</b><span>{a.title}</span></li>
          ))}
        </ul>
      )}
      <div className="as-flow-label">If you investigate, the agents will:</div>
      <div className="as-flow">
        {FLOW.map((f, i) => (
          <span key={f} className="as-step">{f}{i < FLOW.length - 1 && <svg viewBox="0 0 16 16" aria-hidden><path d="M3 8h9M9 5l3 3-3 3" /></svg>}</span>
        ))}
      </div>
      <div className="as-actions">
        <button className="btn primary" onClick={onInvestigate} disabled={busy} title={`Runs the three agents. Uses about $${price.toFixed(2)} of credit.`}>
          Investigate <span className="price">about ${price.toFixed(2)}</span>
        </button>
        <button className="btn subtle" onClick={onClear} disabled={busy}>Stop test</button>
        <span className="as-note">Nothing real is broken. This is a practice incident.</span>
      </div>
    </div>
  )
}
