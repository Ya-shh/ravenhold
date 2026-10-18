export type GNode = { id: string; label: string; name: string; status?: string | null; tier?: number | null; about?: string | null }
export type GEdge = { source: string; target: string; type: string }
export type Subgraph = { nodes: GNode[]; edges: GEdge[] }

export type AgentEvent =
  | { type: 'session'; session_id: string }
  | { type: 'task_created'; task_id: string; agent: string; instructions: string }
  | { type: 'task_status'; task_id: string; agent: string; status: string; result?: string }
  | { type: 'tool_call'; agent: string; tool: string; input: Record<string, unknown>; id: string }
  | { type: 'tool_result'; agent: string; tool: string; id: string; cypher: string[]; subgraph: Subgraph | null; preview: string; summary?: string; via?: string }
  | { type: 'runbook_proposal'; runbook_id: string; supersedes: string; steps: string; because: string }
  | { type: 'fix_proposal'; fix_id: string; runbook_id: string; runbook_title?: string; service: string; service_name?: string;
      incident_id: string; steps: string; reason: string; alarms: number; systems: string[] }
  | { type: 'thought'; agent: string; text: string }
  | { type: 'final'; agent: string; text: string; session_id: string; suggestions?: string[]; citations?: { verified: string[]; unknown: string[] } }
  | { type: 'usage'; id: string; agent: string; model: string; tokens_in: number; tokens_out: number; usd: number; at: string; session: string }
  | { type: 'approval'; page_id: string; person_id: string; name: string; message: string; incident_id: string }
  | { type: 'error'; text: string }
  | { type: 'done' }

export type Lesson = {
  id: string; text: string; kind: string; source: string; created_at: string
  reinforced: number; services: string[]; runbook: string | null; session: string | null
  proposed_version?: string | null; proposed_status?: string; proposed_steps?: string | null
}
export type Limits = { can_run: boolean; reason: 'budget' | null; budget_usd: number; spent_usd: number; budget_left_usd: number }
type Bucket = { usd: number; calls: number; tokens_in: number; tokens_out: number }
export type Usage = {
  spent_usd: number; budget_usd: number; remaining_usd: number; calls: number
  by_model: Record<string, Bucket>; by_role: Record<string, Bucket>
  models: { provider: string; commander: string; workers: string }
}
export type CallRow = { id: string; session: string; agent: string; model: string; tokens_in: number; tokens_out: number; usd: number; at: string }
export type Alert = { id: string; title: string; severity: string; metric: string; fired_at: string; service: string; service_name?: string; scenario?: string }
export type Scenario = { id: string; name: string; summary: string; impact?: string; alerts: number }
export type SessionRow = { id: string; started_at: string; turns: number; incidents: string[]; lessons: number }

const BASE = import.meta.env.VITE_API_URL ?? ''

/** A random id per browser: the server gives each visitor a private copy of the company graph. */
let memVisitor = ''
export function visitorId(): string {
  try {
    let v = localStorage.getItem('rh.visitor')
    if (!v) { v = crypto.randomUUID().replace(/-/g, '').slice(0, 16); localStorage.setItem('rh.visitor', v) }
    return v
  } catch {
    memVisitor ||= Math.random().toString(36).slice(2, 18).padEnd(16, '0')
    return memVisitor
  }
}
const vh = () => ({ 'X-Visitor': visitorId() })

export async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { headers: vh() })
  if (!r.ok) throw new Error(`${r.status} ${path}`)
  return r.json()
}

/** The server's own message (rate limit, budget guard, locked reset) instead of a bare status code. */
async function failure(r: Response, what: string) {
  try { const j = await r.json(); if (j?.detail) return new Error(String(j.detail)) } catch { /* not JSON */ }
  return new Error(`${what} failed: ${r.status}`)
}

/** Admin token for the public deployment: open the site once with ?admin=<token> to store it. */
function adminHeaders(): Record<string, string> {
  try {
    const fromUrl = new URLSearchParams(location.search).get('admin')
    if (fromUrl) localStorage.setItem('rh.admin', fromUrl)
    const t = localStorage.getItem('rh.admin')
    return t ? { 'X-Admin-Token': t } : {}
  } catch { return {} }
}

export async function post(path: string) {
  const r = await fetch(`${BASE}${path}`, { method: 'POST', headers: { ...adminHeaders(), ...vh() } })
  if (!r.ok) throw await failure(r, path)
  return r.json()
}

/** POST a chat message and stream agent events back (SSE over fetch). */
export async function streamChat(
  body: { tenant: string; session_id: string | null; message: string },
  onEvent: (e: AgentEvent) => void,
) {
  const r = await fetch(`${BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...vh() },
    body: JSON.stringify(body),
  })
  if (!r.ok || !r.body) throw await failure(r, 'chat')
  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true }).replace(/\r\n/g, '\n')
    let i
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i)
      buf = buf.slice(i + 2)
      const data = chunk.split('\n').filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trimStart()).join('\n')
      if (data) onEvent(JSON.parse(data))
    }
  }
}
