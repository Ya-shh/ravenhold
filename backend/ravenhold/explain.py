"""One plain-English sentence per agent step, written from the tool's actual result (no LLM call, no cost).

Shown in the UI's Steps tab so a non-technical person can follow what each step found. Everything here is derived
from the result, so it can't drift from what the graph returned.
"""

from __future__ import annotations


def _money(n) -> str:
    n = float(n or 0)
    return f"${n / 1e6:.2f}M" if n >= 1e6 else f"${n / 1e3:.0f}K"


def _list(items: list[str], n: int = 3) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) <= n:
        return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
    return ", ".join(items[:n]) + f" and {len(items) - n} more"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def explain(tool: str, r) -> str:
    if not isinstance(r, dict):
        return ""
    if "error" in r:
        return f"This step hit a problem ({str(r['error'])[:120]}). The agent will adjust and try another way."
    try:
        return _EXPLAIN.get(tool, lambda r: "")(r) or ""
    except Exception:
        return ""


def _correlate(r):
    if not r.get("alerts_firing"):
        return "No alarms are going off right now."
    roots = r.get("root_cause_candidates", [])
    noise = [n["service"] for n in r.get("likely_unrelated_noise", [])]
    s = f"{_plural(r['alerts_firing'], 'alarm')} went off in {_plural(r['services_alerting'], 'system')}. "
    if roots:
        top = roots[0]
        s += f"{top['alerts_explained']} of them come from one place: {top['service']}"
        if top.get("past_incidents_with_this_root"):
            s += f", which has caused this before ({top['past_incidents_with_this_root'][0]['id']})"
        s += ". "
    if noise:
        s += f"{_list(noise)} {'looks' if len(noise) == 1 else 'look'} unrelated, so it can be ignored."
    return s.strip()


def _situation(r):
    alerts = sum(a["alerts"] for a in r.get("firing_alerts", []))
    prods = r.get("products_by_arr", [])[:2]
    risky = r.get("risky_open_follow_ups", [])
    s = (f"Big picture: {_plural(alerts, 'alarm')} going off, {_plural(len(r.get('active_incidents', [])), 'open incident')}, "
         f"{_plural(len(r.get('degraded_services', [])), 'system')} struggling. ")
    if prods:
        s += "The most valuable products are " + " and ".join(f"{p['name']} ({_money(p['arr'])} a year)" for p in prods) + ". "
    if risky:
        s += f"{_plural(len(risky), 'promised fix', 'promised fixes')} from past incidents never got finished ({_list([x['ticket'] for x in risky])})."
    return s.strip()


def _blast(r):
    svc = r.get("service", {}).get("name") or "this system"
    deps = r.get("dependent_services", [])
    prods = [p["name"] for p in r.get("products", [])]
    custs = r.get("customers", [])
    return (f"If {svc} fails, {_plural(len(deps), 'other system')} break too"
            + (f", taking down {_list(prods)}" if prods else "")
            + (f". That affects {_plural(len(custs), 'customer')} paying {_money(r.get('arr_at_risk_usd'))} a year"
               f", including {_list([c['name'] for c in custs], 2)}" if custs else "") + ".")


def _roots(r):
    c = [x for x in r.get("candidates", []) if x.get("past_root_causes")][:3] or r.get("candidates", [])[:3]
    if not c:
        return "Nothing underneath this system looks suspicious."
    return (f"Looked at what {r.get('symptom')} depends on. Most suspicious: "
            + "; ".join(f"{x['id']} ({x['hops']} step{'s' if x['hops'] > 1 else ''} away"
                        + (f", caused {x['past_root_causes']} past problem{'s' if x['past_root_causes'] > 1 else ''}" if x["past_root_causes"] else "")
                        + ")" for x in c) + ".")


def _experts(r):
    ex = r.get("experts", [])
    own = r.get("owner") or {}
    if not ex:
        return "Nobody in the company map has worked on this system."
    e = ex[0]
    why = []
    if e.get("incidents"):
        why.append(f"fixed {_plural(len(e['incidents']), 'past problem')} here")
    if e.get("commits"):
        why.append(f"made {e['commits']} code changes")
    moved = next((m for m in r.get("team_moves", []) if m["person"] == e["id"]), None)
    s = f"The person who knows it best is {e['name']} ({', '.join(why) or 'wrote the docs'})"
    if moved:
        s += (f". They moved from the {moved['former_team']} team {moved.get('days_since_move', 0) // 30} months ago, "
              "so the official owners might not think to call them")
    if own.get("on_call"):
        s += (f". The official on-call, {own['on_call']}, has {own.get('on_call_commits', 0)} code changes and "
              f"{_plural(own.get('on_call_incidents_resolved', 0), 'fix', 'fixes')} here")
    return s + "."


def _similar(r):
    inc = r.get("incidents", [])
    if not inc:
        return "No similar problems have happened before."
    top = inc[0]
    how = {"graph": "nearby on the map", "text": "matching words", "vector": "similar meaning"}
    found = _list([how[m] for m in top.get("matched_by", []) if m in how])
    fix = f", using the written-down fix {top['runbooks'][0]}" if top.get("runbooks") else ""
    who = f" by {_list(top.get('resolved_by', []), 2)}" if top.get("resolved_by") else ""
    return (f"Found {_plural(len(inc), 'similar past problem')}. The closest is {top['id']} on {top.get('date')}"
            + (f" (found by {found})" if found else "") + f": \"{top['title']}\", fixed{who}{fix}.")


def _runbook(r):
    s = f"Read the step-by-step fix \"{r.get('title')}\" (version {r.get('version', 1)}), used {_plural(r.get('times_used', 0), 'time')} before."
    if r.get("note"):
        s += " It was updated by an earlier correction."
    if r.get("pending_proposals"):
        s += f" A newer version ({r['pending_proposals'][0]['id']}) is waiting for approval."
    return s


def _followups(r):
    f = r.get("open_follow_ups", [])
    if not f:
        return "Every fix promised after past incidents has been finished."
    return (f"{_plural(len(f), 'promised fix', 'promised fixes')} never got finished: "
            + "; ".join(f"{x['id']} \"{x['title']}\" (promised after {x.get('from_incident')}, assigned to {x.get('assignee')})"
                        for x in f[:3]) + ".")


def _decisions(r):
    d = r.get("decisions", [])
    if not d:
        return "No recorded decisions explain how this was built."
    top = d[0]
    return (f"{_plural(len(d), 'past decision')} shaped this system. The latest, {top['id']} \"{top['title']}\", was made by "
            f"{top.get('made_by')} based on {_plural(len([s for s in top.get('sources', []) if s.get('id')]), 'source')} "
            f"(chats, documents or reviews).")


def _memory(r):
    ls = r.get("lessons", [])
    if not ls:
        return "No lessons about these systems from earlier conversations yet."
    return (f"Remembered {_plural(len(ls), 'lesson')} from earlier conversations, e.g. \"{ls[0]['text'][:90]}\".")


def _lesson(r):
    if r.get("reinforced"):
        return "This lesson was already known, so it was reinforced instead of saved twice."
    s = "Saved the lesson so every future conversation can use it."
    if r.get("new_runbook_version"):
        s += f" Proposed an updated fix, {r['new_runbook_version']}, which waits for your approval."
    return s


def _projects(r):
    ps = r.get("projects") or []
    if not ps:
        return "No project matched, so there is nothing in flight here."
    risky = [p for p in ps if p.get("risks")]
    head = f"Found {_plural(len(ps), 'project')} and who works on them"
    if not risky:
        return head + "; none shows a risk in the graph."
    top = risky[0]
    return (f"{head}. {_plural(len(risky), 'project')} at risk, for example {top['name']}: {top['risks'][0]}."
            .replace(" [", " (").replace("]", ")"))


def _propose_fix(r):
    return (f"Proposed applying {r.get('runbook')} to {r.get('service_name') or r.get('service')}, which should clear "
            f"{_plural(r.get('alarms_it_should_clear', 0), 'alarm')}. Waiting for your approval.")


def _recovery(r):
    rel, unrel = r.get("still_firing_related") or [], r.get("still_firing_unrelated") or []
    if not r.get("fixes"):
        return "No fix has been applied to this incident yet."
    head = (f"Recovered: {_plural(r.get('alarms_cleared_by_fixes', 0), 'alarm')} cleared by the fix"
            if r.get("recovered") else f"Not recovered yet: {_plural(len(rel), 'related alarm')} still firing")
    tail = (f"; {_plural(len(unrel), 'unrelated alarm')} still firing on {_list([a['service'] for a in unrel])}"
            if unrel else "; nothing else is firing")
    return head + tail + "."


_EXPLAIN = {
    "propose_fix": _propose_fix,
    "check_recovery": _recovery,
    "project_overview": _projects,
    "correlate_alerts": _correlate,
    "situation_report": _situation,
    "blast_radius": _blast,
    "root_cause_candidates": _roots,
    "find_experts": _experts,
    "similar_incidents": _similar,
    "get_runbook": _runbook,
    "open_follow_ups": _followups,
    "trace_decisions": _decisions,
    "recall_memory": _memory,
    "record_lesson": _lesson,
    "search_knowledge": lambda r: f"Found {_plural(len(r.get('matches', [])), 'matching document, chat or ticket', 'matching documents, chats or tickets')}.",
    "critical_services": lambda r: ("The parts the most things depend on: "
                                    + _list([x["id"] for x in r.get("most_depended_on_pagerank", [])])
                                    + ". The biggest bottlenecks: "
                                    + _list([x["id"] for x in r.get("chokepoints_betweenness", [])])
                                    + (f". The systems split into {len(r['failure_domains_label_propagation'])} groups that tend to "
                                       "fail together" if r.get("failure_domains_label_propagation") else "") + "."),
    "shortest_connection": lambda r: (f"They are linked in {_plural(r['hops'], 'step')}: " + " → ".join(r["path"])
                                      if r.get("path") else "No direct link found between these two."),
    "graph_query": lambda r: f"Asked the company map a custom question and got {_plural(len(r.get('rows', [])), 'answer row')}.",
    "update_knowledge": lambda r: ((f"Updated the company map with {_plural(len(r.get('changes', [])), 'change')}: "
                                    + " ".join(c["summary"] for c in r.get("changes", [])[:3]))
                                   if r.get("changes") else "Found nothing in that statement that maps onto the company map."),
    "declare_incident": lambda r: f"Officially opened incident {r.get('incident_id')} so the work is tracked.",
    "page_person": lambda r: (f"Asked you to approve calling {r.get('person')}." if r.get("status", "").startswith("awaiting")
                              else f"Called {r.get('paged')}."),
    "create_ticket": lambda r: f"Created to-do {r.get('ticket_id')} so the proper fix isn't forgotten.",
    "resolve_incident": lambda r: f"Closed incident {r.get('incident_id')} and recorded what fixed it, so it can be found next time.",
    "delegate": lambda r: ("The Investigator and Historian finished their tasks and reported back: "
                           + _list([f"{t['agent']} ({t['status']})" for t in r.get("tasks", [])], 4) + "."),
}
