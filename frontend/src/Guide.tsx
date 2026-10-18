import { useEffect, useState } from 'react'
import { useDialog } from './a11y'
import { CODES, GLOSSARY } from './Plain'
import { DataChapter, TechChapter, useAbout } from './Transparency'

const ICONS: Record<string, React.ReactNode> = {
  breaks: <path d="M12 3c1 3 4 4.5 4 8.5A4 4 0 0 1 8 11.5c0-1.6.7-2.7 1.6-3.6.3 1.4 1.1 2.1 1.9 2.4C11 8 11 5.5 12 3zM7 19h10" />,
  map: <><circle cx="6" cy="7" r="2" /><circle cx="18" cy="6" r="2" /><circle cx="12" cy="17" r="2" /><circle cx="19" cy="15" r="1.6" /><path d="M7.7 8.2 10.6 15.4M16.2 6.6 7.9 6.9M13.8 16.4l3.7-1M17.4 7.8l1.2 5.6" /></>,
  find: <><circle cx="11" cy="11" r="6" /><path d="m20 20-4.2-4.2M11 8v3l2 1.5" /></>,
  remember: <><path d="M12 5a4 4 0 0 0-7.6 1.8A3.5 3.5 0 0 0 5 13.6 3.5 3.5 0 0 0 9.5 18H12zM12 5a4 4 0 0 1 7.6 1.8 3.5 3.5 0 0 1-.6 6.8A3.5 3.5 0 0 1 14.5 18H12z" /><path d="M12 5v13" /></>,
}

export const STEPS = [
  { icon: 'breaks', title: 'Something breaks', text: 'A customer says checkout is failing, or a dozen alarms go off at once.' },
  { icon: 'map', title: 'It reads how your company connects', text: 'Systems, people, past problems and decisions are linked on one map: the graph next to this panel.' },
  { icon: 'find', title: 'It finds the cause and the right person', text: 'It follows the links to the real source and calls whoever actually fixed it before, even if they are off duty.' },
  { icon: 'remember', title: 'It remembers', text: 'Tell it a better fix once and it updates the playbook. Next time, it already knows.' },
]

function Icon({ name }: { name: string }) {
  return <svg viewBox="0 0 24 24" aria-hidden className="guide-icon">{ICONS[name]}</svg>
}

/** Plain-language, four-step explanation of what Ravenhold does. */
export function GuideSteps() {
  return (
    <ol className="guide-steps">
      {STEPS.map((s, i) => (
        <li key={s.title}>
          <span className="guide-badge"><Icon name={s.icon} /><em>{i + 1}</em></span>
          <div>
            <b>{s.title}</b>
            <span>{s.text}</span>
          </div>
        </li>
      ))}
    </ol>
  )
}

/* ---------- diagrams ---------- */

type DNode = { id: string; x: number; y: number; label: string; kind: string; sub?: string; above?: boolean }

/** The real path from the demo data, drawn as a small, labelled map. Labels sit where no arrow crosses them. */
function StoryMap() {
  const N: DNode[] = [
    { id: 'orbit', x: 70, y: 74, label: 'Orbit Rides', kind: 'customer', sub: 'pays $1.75M a year', above: true },
    { id: 'sarah', x: 235, y: 74, label: 'Sarah', kind: 'person', sub: 'fixed it, then moved teams', above: true },
    { id: 'inc', x: 400, y: 74, label: 'Problem in March', kind: 'incident', sub: 'INC-142', above: true },
    { id: 'fix', x: 555, y: 74, label: 'Written-down fix', kind: 'fix', sub: 'runbook RB-7', above: true },
    { id: 'checkout', x: 70, y: 190, label: 'Checkout', kind: 'product', sub: 'what customers use' },
    { id: 'api', x: 235, y: 190, label: 'Payments API', kind: 'service', above: true },
    { id: 'pool', x: 400, y: 190, label: 'PgBouncer', kind: 'service', sub: 'the part that broke' },
    { id: 'marcus', x: 235, y: 300, label: 'Marcus', kind: 'person', sub: 'on call, never touched it' },
  ]
  const E: [string, string, string][] = [
    ['orbit', 'checkout', 'uses'], ['checkout', 'api', 'runs on'], ['api', 'pool', 'depends on'],
    ['inc', 'pool', 'broke'], ['sarah', 'inc', 'fixed'], ['inc', 'fix', 'fixed with'], ['marcus', 'api', 'on call for'],
  ]
  const at = (id: string) => N.find((n) => n.id === id)!
  return (
    <svg viewBox="0 0 625 350" className="story-map" role="img" aria-label="Example of the company map">
      <defs>
        <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0 10 5 0 10z" className="sm-arrow" />
        </marker>
      </defs>
      {E.map(([a, b, label]) => {
        const A = at(a), B = at(b)
        const dx = B.x - A.x, dy = B.y - A.y, len = Math.hypot(dx, dy)
        const ox = (dx / len) * 22, oy = (dy / len) * 22
        const vertical = Math.abs(dx) < 1
        const mx = (A.x + B.x) / 2, my = (A.y + B.y) / 2
        return (
          <g key={a + b}>
            <line x1={A.x + ox} y1={A.y + oy} x2={B.x - ox} y2={B.y - oy} className="sm-edge" markerEnd="url(#arr)" />
            <text x={vertical ? mx + 9 : mx} y={vertical ? my + 4 : my - 7} className="sm-edge-label"
              style={{ textAnchor: vertical ? 'start' : 'middle' }}>{label}</text>
          </g>
        )
      })}
      {N.map((n) => (
        <g key={n.id}>
          <circle cx={n.x} cy={n.y} r="16" className={`sm-ring k-${n.kind}`} />
          <circle cx={n.x} cy={n.y} r="5" className={`sm-dot k-${n.kind}`} />
          <text x={n.x} y={n.above ? n.y - 38 : n.y + 32} className="sm-label">{n.label}</text>
          {n.sub && <text x={n.x} y={n.above ? n.y - 25 : n.y + 45} className="sm-sub">{n.sub}</text>}
        </g>
      ))}
    </svg>
  )
}

function Chapter({ kicker, title, children }: { kicker: string; title: string; children: React.ReactNode }) {
  return (
    <div className="chapter">
      <div className="eyebrow">{kicker}</div>
      <h2 id="guide-title">{title}</h2>
      {children}
    </div>
  )
}

const CHAPTERS = 8
export const DATA_CHAPTER = 2
export const ASK_CHAPTER = 6
export const TECH_CHAPTER = 7

/* Questions that work well, grouped by what they show. Each one is answered from the company map. */
const ASK: { topic: string; icon: string; what: string; qs: string[] }[] = [
  { topic: 'Something is broken', icon: '🚨', what: 'finds the real cause, what else breaks, and who can fix it',
    qs: ['Checkout is throwing errors and payments-api is timing out. What is going on and who do we page?',
         'Kafka just lost a broker and the ledger is falling behind. Have we seen this before?'] },
  { topic: 'What if…', icon: '💥', what: 'follows the links to every system, product and customer affected',
    qs: ['What breaks if payments-db goes down? Which customers and how much money?'] },
  { topic: 'People', icon: '👥', what: 'who actually knows a system, not just who owns it or is on call',
    qs: ['Who actually knows pgbouncer, and are they on call?'] },
  { topic: 'Projects', icon: '📋', what: 'who works on what, and what is late or blocked',
    qs: ['What is the Platform team working on, and is anything at risk?'] },
  { topic: 'Why is it like this?', icon: '🧭', what: 'traces a decision back to the chats and documents behind it',
    qs: ['Why do ledger writes go through Kafka? Trace the decision to its sources.'] },
  { topic: 'Weak spots', icon: '🎯', what: 'graph maths ranks the systems everything depends on',
    qs: ['Which services are our biggest single points of failure?'] },
]

export function GuideModal({ open, onClose, onTry, onAsk, busy, startPage = 0 }: {
  open: boolean; onClose: () => void; onTry: () => void; onAsk: (q: string) => void; busy: boolean; startPage?: number
}) {
  const [page, setPage] = useState(0)
  const about = useAbout(open)
  const ref = useDialog<HTMLDivElement>(open, onClose)
  useEffect(() => { if (open) setPage(startPage) }, [open, startPage])
  useEffect(() => {
    if (!open) return
    const keys = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest?.('summary, textarea, input')) return
      if (e.key === 'ArrowRight') setPage((p) => Math.min(CHAPTERS - 1, p + 1))
      if (e.key === 'ArrowLeft') setPage((p) => Math.max(0, p - 1))
    }
    document.addEventListener('keydown', keys)
    return () => document.removeEventListener('keydown', keys)
  }, [open, onClose])
  if (!open) return null

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal story" role="dialog" aria-modal="true" aria-labelledby="guide-title" tabIndex={-1} ref={ref} onMouseDown={(e) => e.stopPropagation()}>
        <button className="modal-x" onClick={onClose} aria-label="Close">
          <svg viewBox="0 0 16 16" aria-hidden><path d="M4 4l8 8M12 4l-8 8" /></svg>
        </button>

        {page === 0 && (
          <Chapter kicker="1 · Overview" title="Welcome to Ravenhold">
            <p className="lead">This website is a working demo of <b>Ravenhold</b>: a team of three AI agents that handles
              outages for a company. To show it safely, we invented a whole company for it to look after.</p>
            <div className="who-row" role="list">
              <div className="who-box" role="listitem"><span aria-hidden>🏢</span><b>The company</b>
                <em>Ravenhold Pay, a made-up payments company. Its systems, people, customers and history are stored as a map.</em></div>
              <i aria-hidden>→</i>
              <div className="who-box" role="listitem"><span aria-hidden>🤖</span><b>The AI team</b>
                <em>Three agents read that map to find causes and people, and take action.</em></div>
              <i aria-hidden>→</i>
              <div className="who-box you" role="listitem"><span aria-hidden>🧑</span><b>You</b>
                <em>The person on call. You ask, you approve anything that matters, and you teach it.</em></div>
            </div>
            <div className="callout emphasis">
              <b>Is the AI trained on this company?</b> No. The agents are general AI models (Claude) that know nothing about
              Ravenhold Pay on their own. Every time you ask, they <b>look the facts up in the map</b> with database queries,
              and every answer shows which facts it used. When you correct them, the lesson is saved in the map too, which
              is how they "remember". Nothing here comes from the internet or a real company.
            </div>
            <p className="dim small">Next: the problem it solves, the data it reads, how the map works, the team, a full
              example, what you can ask, and (for engineers) how it is built. About 3 minutes.</p>
          </Chapter>
        )}

        {page === 1 && (
          <Chapter kicker="2 · The problem" title="It is 2 AM and checkout is broken">
            <div className="story-scene">
              <p>A normal night at <b>Ravenhold Pay</b>, until shops using it suddenly can't take payments. Alarms are going off
                everywhere: thirteen of them, in ten different systems.</p>
              <p><b>Marcus</b> is on call tonight. He opens the alarms. He has <b>never worked on these systems</b>.</p>
              <p>He spends <b>40 minutes</b> asking around in chat. Eventually someone remembers that <b>Sarah</b> fixed something
                similar in March. But Sarah moved to another team, so nobody thought to call her.</p>
              <p>When Sarah finally joins, she says: "Don't restart the database, that didn't help last time. The fix is already
                written down." It was, in a document from March that nobody found.</p>
            </div>
            <div className="story-moral">
              <b>The knowledge existed. It was just scattered</b> across people, chats, old documents and tickets, and no one
              could connect the dots fast enough. Every minute of a payment outage costs real money.
            </div>
          </Chapter>
        )}

        {page === 2 && (
          <Chapter kicker="3 · The data" title="Meet Ravenhold Pay, our practice company">
            <DataChapter about={about} />
          </Chapter>
        )}

        {page === 3 && (
          <Chapter kicker="4 · The map" title="Everything is connected, so put it on a map">
            <p className="lead">Instead of a long list of records, Ravenhold stores the company as a <b>map of connections</b>{' '}
              (a graph). Every <b>dot</b> is a thing; every <b>arrow</b> says how two things relate. Here is a real piece of it:</p>
            <StoryMap />
            <div className="map-reads">
              <div><b>Follow the arrows and the story tells itself.</b> Orbit Rides uses Checkout, which runs on the Payments API,
                which depends on PgBouncer. PgBouncer broke in March, and <b>Sarah</b> fixed it with a written-down fix.</div>
              <div>That is why Ravenhold calls <b>Sarah</b>, not Marcus. Marcus is on call, but the map shows he has never touched it.
                A normal list can't answer that quickly; a map answers it in one step.</div>
            </div>
          </Chapter>
        )}

        {page === 4 && (
          <Chapter kicker="5 · The team" title="Three AI agents, each with a job">
            <p className="lead">An <b>agent</b> is an AI that can use tools on its own. Ravenhold has three, and they work like a small
              incident team. They pass notes to each other through the map, so nothing gets lost.</p>
            <div className="team">
              <div className="member"><span className="avatar-lg ag-commander">C</span><b>The Commander</b><em>the team lead</em>
                <span>Talks to you, splits up the work, and takes action: opens the incident, calls the right engineer, saves lessons.</span></div>
              <div className="team-arrows" aria-hidden><span>hands out tasks →</span><span>← reports back</span></div>
              <div className="member-pair">
                <div className="member"><span className="avatar-lg ag-investigator">I</span><b>The Investigator</b><em>the detective</em>
                  <span>Follows the arrows to find what really broke, who is affected, and who knows how to fix it.</span></div>
                <div className="member"><span className="avatar-lg ag-historian">H</span><b>The Historian</b><em>the librarian</em>
                  <span>Remembers every past problem, every written-down fix, and every lesson you have taught the team.</span></div>
              </div>
            </div>
          </Chapter>
        )}

        {page === 5 && (
          <Chapter kicker="6 · The same night, with Ravenhold" title="Solved in about 30 seconds">
            <ol className="timeline">
              <li><span>0s</span><div><b>Thirteen alarms go off.</b> You press Investigate.</div></li>
              <li><span>5s</span><div><b>The Investigator follows the map</b> and finds that twelve alarms come from <b>one</b> part, PgBouncer, and that one more is unrelated noise it can ignore.</div></li>
              <li><span>10s</span><div><b>The Historian finds March's problem</b>, the written-down fix, and the warning "don't restart the database".</div></li>
              <li><span>20s</span><div><b>The Commander calls Sarah</b>, explains why her (she fixed it before), and lists exactly what to do.</div></li>
              <li><span>later</span><div><b>You teach it something new</b> ("don't pause at peak traffic"). It updates the fix to a new version.</div></li>
              <li><span>next time</span><div><b>It already knows.</b> A different conversation, weeks later, uses the improved fix straight away.</div></li>
            </ol>
            <div className="story-moral">Every answer shows the exact steps it took and what it cost, so you can always check its work.</div>
            <button className="btn primary lg cta-wide" onClick={() => { onClose(); onTry() }} disabled={busy}>
              <svg viewBox="0 0 16 16" aria-hidden><path d="M5 3.5v9l7-4.5z" /></svg>
              Try it yourself: simulate the 2 AM incident
            </button>
            <p className="dim small cta-note">Free. It fires test alarms; you decide when the agents investigate.</p>
            <details className="glossary">
              <summary>Words you will see</summary>
              <dl>{Object.entries(GLOSSARY).map(([k, v]) => (<div key={k}><dt>{k}</dt><dd>{v}</dd></div>))}</dl>
            </details>
            <details className="glossary">
              <summary>Codes you will see (INC-142, RB-7, TKT-512…)</summary>
              <dl>{CODES.map(([k, v]) => (<div key={k}><dt><code>{k}</code></dt><dd>{v}</dd></div>))}</dl>
            </details>
          </Chapter>
        )}

        {page === 6 && (
          <Chapter kicker="7 · Your turn" title="What you can ask (and what it can't do)">
            <p className="lead">Ask in your own words. These work well; tap one to ask it. Each answer takes about 20 seconds
              and uses about 3 cents of shared demo credit.</p>
            <div className="ask-groups">
              {ASK.map((g) => (
                <div key={g.topic} className="ask-group">
                  <div className="ask-head"><span aria-hidden>{g.icon}</span><b>{g.topic}</b><em>{g.what}</em></div>
                  {g.qs.map((q) => (
                    <button key={q} className="ask-q" disabled={busy} onClick={() => { onClose(); onAsk(q) }}>{q} <span aria-hidden>→</span></button>
                  ))}
                </div>
              ))}
            </div>
            <div className="callout">
              <b>What it can't do</b>
              <ul className="cant">
                <li>Answer about anything outside Ravenhold Pay. It only has this one company's map.</li>
                <li>See live systems. Alarms come from practice storms you start with <b>Simulate</b>.</li>
                <li>Really call anyone. An approved page is written into the map; nobody is phoned.</li>
                <li>Learn on its own. It only learns what you tell it: corrections, and news you paste into <b>Add knowledge</b>.</li>
              </ul>
            </div>
          </Chapter>
        )}

        {page === 7 && (
          <Chapter kicker="8 · Under the hood" title="How it works (technical)">
            <p className="lead">The same story, for engineers. Every fact here is read from the code or counted live from the graph.</p>
            <TechChapter about={about} />
          </Chapter>
        )}

        <div className="story-nav">
          <button className="btn subtle" onClick={() => setPage((p) => p - 1)} disabled={page === 0}>← Back</button>
          <div className="dots">
            {Array.from({ length: CHAPTERS }, (_, i) => (
              <button key={i} className={i === page ? 'on' : ''} onClick={() => setPage(i)} aria-label={`Chapter ${i + 1}`} aria-current={i === page ? 'step' : undefined} />
            ))}
          </div>
          {page < CHAPTERS - 1
            ? <button className="btn primary" onClick={() => setPage((p) => p + 1)}>Next →</button>
            : <button className="btn" onClick={onClose}>Done</button>}
        </div>
      </div>
    </div>
  )
}
