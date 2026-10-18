"""Synthetic company: Ravenhold Pay, a fictional payments startup.

Everything here is invented (hackathon Rule 6). The data has a story planted in it
for the demo:

* payments-db sits behind pgbouncer; Checkout, Payouts and Mobile all depend on it.
* The listed owner of payments-db is the Payments team, whose current on-call
  (Marcus) joined in July and has never touched it.
* The person who actually fixed it last time (INC-142) is Sarah, who has since
  moved to the Platform team.
* INC-142's postmortem produced a follow-up ticket (TKT-512) that is still open,
  and a decision (ADR-12) traced back to a Slack thread.
"""

TEAMS = [
    {"id": "payments", "name": "Payments", "slack": "#team-payments"},
    {"id": "identity", "name": "Identity", "slack": "#team-identity"},
    {"id": "platform", "name": "Platform", "slack": "#team-platform"},
    {"id": "data", "name": "Data", "slack": "#team-data"},
    {"id": "growth", "name": "Growth", "slack": "#team-growth"},
    {"id": "mobile", "name": "Mobile", "slack": "#team-mobile"},
]

# (id, name, role, [(team, from, to|None)])
PEOPLE = [
    ("sarah", "Sarah Bennett", "Staff Engineer", [("payments", "2023-02-01", "2026-06-01"), ("platform", "2026-06-01", None)]),
    ("marcus", "Marcus Reid", "Senior Engineer", [("payments", "2026-07-15", None)]),
    ("thomas", "Thomas Ward", "Engineer", [("payments", "2024-09-01", None)]),
    ("helen", "Helen Carter", "Engineering Manager", [("payments", "2022-05-01", None)]),
    ("daniel", "Daniel Brooks", "Senior SRE", [("platform", "2022-11-01", None)]),
    ("james", "James Porter", "SRE", [("platform", "2024-01-10", None)]),
    ("fiona", "Fiona Marsh", "Platform Lead", [("platform", "2021-08-01", None)]),
    ("alice", "Alice Morgan", "Senior Engineer", [("identity", "2023-04-01", None)]),
    ("oliver", "Oliver Grant", "Engineer", [("identity", "2025-01-15", None)]),
    ("megan", "Megan Foster", "Identity Lead", [("identity", "2022-02-01", None)]),
    ("ryan", "Ryan Cole", "Data Engineer", [("data", "2023-06-01", None)]),
    ("sophie", "Sophie Lane", "ML Engineer", [("payments", "2023-01-01", "2025-03-01"), ("data", "2025-03-01", None)]),
    ("noah", "Noah Fletcher", "Data Lead", [("data", "2021-10-01", None)]),
    ("emma", "Emma Collins", "Engineer", [("growth", "2024-03-01", None)]),
    ("liam", "Liam Walsh", "Growth Lead", [("growth", "2022-07-01", None)]),
    ("zoe", "Zoe Hughes", "Engineer", [("growth", "2025-05-01", None)]),
    ("adam", "Adam Price", "Mobile Lead", [("mobile", "2022-09-01", None)]),
    ("chloe", "Chloe Martin", "iOS Engineer", [("mobile", "2024-02-01", None)]),
    ("samuel", "Samuel Wright", "Android Engineer", [("mobile", "2023-11-01", None)]),
    ("hannah", "Hannah Blake", "Senior Engineer", [("platform", "2023-03-01", "2025-09-01"), ("payments", "2025-09-01", None)]),
]

# Current on-call per team (this week)
ON_CALL = {"payments": "marcus", "identity": "oliver", "platform": "james", "data": "ryan", "growth": "zoe", "mobile": "samuel"}

# (id, name, tier, owner_team, kind, description)
SERVICES = [
    ("api-gateway", "API Gateway", 0, "platform", "edge", "Public HTTPS entrypoint, routing and rate limits"),
    ("service-mesh", "Service Mesh", 0, "platform", "infra", "mTLS and service discovery between services"),
    ("pgbouncer", "PgBouncer", 0, "platform", "infra", "Postgres connection pooler in front of payments-db"),
    ("kafka", "Kafka", 0, "platform", "infra", "Event bus for ledger, notifications and analytics"),
    ("secrets-vault", "Secrets Vault", 0, "platform", "infra", "Stores card network keys and service credentials"),
    ("observability", "Observability Stack", 2, "platform", "infra", "Metrics, logs and traces"),
    ("payments-api", "Payments API", 0, "payments", "service", "Creates and captures payments for Checkout"),
    ("payments-db", "Payments DB", 0, "payments", "database", "Primary Postgres for payments and ledger"),
    ("ledger-service", "Ledger Service", 0, "payments", "service", "Double-entry ledger of all money movement"),
    ("payouts-service", "Payouts Service", 1, "payments", "service", "Daily merchant payouts"),
    ("card-gateway", "Card Gateway", 0, "payments", "service", "Talks to card networks"),
    ("fraud-scorer", "Fraud Scorer", 1, "payments", "service", "Real-time fraud scoring of payments"),
    ("auth-service", "Auth Service", 0, "identity", "service", "Login, tokens and API keys"),
    ("user-db", "User DB", 0, "identity", "database", "Users, merchants and API keys"),
    ("session-cache", "Session Cache", 1, "identity", "cache", "Redis cache for sessions and fraud features"),
    ("kyc-service", "KYC Service", 1, "identity", "service", "Merchant identity verification"),
    ("warehouse", "Data Warehouse", 2, "data", "database", "Analytics warehouse"),
    ("analytics-pipeline", "Analytics Pipeline", 2, "data", "pipeline", "Streams events from Kafka into the warehouse"),
    ("ml-feature-store", "ML Feature Store", 1, "data", "service", "Online features for fraud scoring"),
    ("reporting-api", "Reporting API", 1, "data", "service", "Merchant reports and exports"),
    ("notifications-service", "Notifications Service", 1, "growth", "service", "Fans out email and push notifications"),
    ("email-sender", "Email Sender", 2, "growth", "service", "Transactional email"),
    ("promo-engine", "Promo Engine", 2, "growth", "service", "Discounts and promo codes at checkout"),
    ("referral-service", "Referral Service", 2, "growth", "service", "Merchant referral rewards"),
    ("merchant-dashboard", "Merchant Dashboard", 1, "growth", "frontend", "Web dashboard for merchants"),
    ("mobile-bff", "Mobile BFF", 1, "mobile", "service", "Backend-for-frontend for the mobile apps"),
    ("push-service", "Push Service", 2, "mobile", "service", "APNs / FCM push delivery"),
]

# What each system does, in everyday words, for people who aren't engineers. Shown in the UI beside the system's name.
PLAIN = {
    "api-gateway": "The front door: every request from the apps and websites comes in here first.",
    "service-mesh": "The secure wiring that lets Ravenhold's systems find and talk to each other.",
    "pgbouncer": "The connection pool: it shares a small number of connections to the payments database among "
                 "every system that needs one, so the database isn't overwhelmed.",
    "kafka": "The message queue: systems drop messages here and others pick them up later, like a post office.",
    "secrets-vault": "The safe for passwords and card-network keys that systems need.",
    "observability": "The dashboards and logs engineers use to see what every system is doing.",
    "payments-api": "Takes the payment when a customer pays at checkout.",
    "payments-db": "The payments database: the record of every payment and every money movement.",
    "ledger-service": "The accounting book: records where every dollar went.",
    "payouts-service": "Sends merchants the money they're owed, once a day.",
    "card-gateway": "Talks to Visa, Mastercard and the other card networks to approve card payments.",
    "fraud-scorer": "Checks each payment for signs of fraud before it goes through.",
    "auth-service": "Handles logging in and checks who is allowed to do what.",
    "user-db": "The database of users, merchants and their access keys.",
    "session-cache": "Short-term memory that keeps people logged in and speeds up fraud checks.",
    "kyc-service": "Checks that a new merchant is who they say they are.",
    "warehouse": "Stores a copy of the data for reports and analysis.",
    "analytics-pipeline": "Copies events from the message queue into the reporting store.",
    "ml-feature-store": "Holds the facts the fraud checker looks up for each payment.",
    "reporting-api": "Gives merchants their reports and spreadsheet exports.",
    "notifications-service": "Decides who gets an email or a phone notification, and sends it on.",
    "email-sender": "Sends emails such as receipts and password resets.",
    "promo-engine": "Applies discounts and promo codes at checkout.",
    "referral-service": "Rewards merchants who refer other merchants.",
    "merchant-dashboard": "The website where merchants see their payments and payouts.",
    "mobile-bff": "The part that serves the Ravenhold mobile apps.",
    "push-service": "Delivers notifications to people's phones.",
}

# (service, depends_on)
DEPENDS_ON = [
    ("api-gateway", "auth-service"), ("api-gateway", "service-mesh"),
    ("payments-api", "pgbouncer"), ("payments-api", "ledger-service"), ("payments-api", "fraud-scorer"),
    ("payments-api", "card-gateway"), ("payments-api", "auth-service"), ("payments-api", "kafka"),
    ("pgbouncer", "payments-db"),
    ("ledger-service", "pgbouncer"), ("ledger-service", "kafka"),
    ("payouts-service", "ledger-service"), ("payouts-service", "pgbouncer"), ("payouts-service", "kafka"),
    ("card-gateway", "secrets-vault"),
    ("fraud-scorer", "ml-feature-store"), ("fraud-scorer", "session-cache"),
    ("auth-service", "user-db"), ("auth-service", "session-cache"), ("auth-service", "secrets-vault"),
    ("kyc-service", "user-db"), ("kyc-service", "kafka"),
    ("mobile-bff", "api-gateway"), ("mobile-bff", "payments-api"), ("mobile-bff", "notifications-service"),
    ("merchant-dashboard", "api-gateway"), ("merchant-dashboard", "reporting-api"), ("merchant-dashboard", "payouts-service"),
    ("reporting-api", "warehouse"), ("reporting-api", "ledger-service"),
    ("analytics-pipeline", "kafka"), ("analytics-pipeline", "warehouse"),
    ("ml-feature-store", "warehouse"), ("ml-feature-store", "kafka"),
    ("notifications-service", "kafka"), ("notifications-service", "email-sender"), ("notifications-service", "push-service"),
    ("promo-engine", "payments-api"), ("promo-engine", "user-db"),
    ("referral-service", "promo-engine"), ("referral-service", "user-db"),
]

# Products customers buy, and the services that power them
PRODUCTS = [
    ("checkout", "Ravenhold Checkout", ["payments-api", "card-gateway", "fraud-scorer", "promo-engine"]),
    ("payouts", "Ravenhold Payouts", ["payouts-service"]),
    ("dashboard", "Ravenhold Dashboard", ["merchant-dashboard"]),
    ("mobile-app", "Ravenhold Mobile", ["mobile-bff"]),
    ("onboarding", "Ravenhold Onboarding", ["kyc-service", "auth-service"]),
]

# (id, name, arr_usd, sla, [products])
CUSTOMERS = [
    ("northwind", "Northwind Grocers", 1_200_000, "platinum", ["checkout", "payouts", "dashboard"]),
    ("bluebird", "Bluebird Travel", 860_000, "gold", ["checkout", "mobile-app"]),
    ("helix", "Helix Fitness", 240_000, "silver", ["checkout", "dashboard"]),
    ("copperline", "Copperline Hardware", 410_000, "gold", ["payouts", "dashboard"]),
    ("lumen", "Lumen Books", 95_000, "standard", ["checkout"]),
    ("orbit", "Orbit Rides", 1_750_000, "platinum", ["checkout", "mobile-app", "payouts"]),
    ("saffron", "Saffron Kitchens", 130_000, "standard", ["onboarding", "checkout"]),
    ("tidewater", "Tidewater Hotels", 690_000, "gold", ["checkout", "dashboard", "payouts"]),
]

# Who has actually written code in what (person, service, commits, last_commit)
CONTRIBUTED_TO = [
    ("sarah", "payments-db", 212, "2026-05-28"), ("sarah", "pgbouncer", 64, "2026-09-30"),
    ("sarah", "ledger-service", 140, "2026-05-02"), ("thomas", "payments-db", 38, "2026-08-19"),
    ("thomas", "payments-api", 175, "2026-10-10"), ("marcus", "payments-api", 22, "2026-10-12"),
    ("marcus", "payouts-service", 15, "2026-10-01"), ("helen", "payments-api", 30, "2024-12-01"),
    ("hannah", "ledger-service", 41, "2026-10-08"), ("hannah", "pgbouncer", 57, "2025-08-01"),
    ("daniel", "kafka", 260, "2026-10-05"), ("daniel", "pgbouncer", 33, "2026-03-12"),
    ("james", "session-cache", 48, "2026-09-23"), ("james", "service-mesh", 90, "2026-10-02"),
    ("fiona", "api-gateway", 120, "2026-07-14"), ("fiona", "secrets-vault", 80, "2026-04-20"),
    ("alice", "auth-service", 300, "2026-10-11"), ("alice", "session-cache", 25, "2026-08-15"),
    ("oliver", "kyc-service", 66, "2026-10-09"), ("megan", "user-db", 110, "2026-06-30"),
    ("ryan", "analytics-pipeline", 150, "2026-10-07"), ("ryan", "warehouse", 95, "2026-09-11"),
    ("sophie", "fraud-scorer", 180, "2025-02-20"), ("sophie", "ml-feature-store", 140, "2026-10-03"),
    ("noah", "reporting-api", 70, "2026-08-08"), ("emma", "promo-engine", 130, "2026-10-06"),
    ("zoe", "referral-service", 45, "2026-10-04"), ("liam", "notifications-service", 60, "2026-05-05"),
    ("emma", "notifications-service", 40, "2026-09-01"), ("adam", "mobile-bff", 200, "2026-10-01"),
    ("samuel", "push-service", 85, "2026-09-25"), ("chloe", "mobile-bff", 70, "2026-10-10"),
]

RUNBOOKS = [
    {"id": "RB-7", "title": "Drain and resize the PgBouncer pool",
     "service": "pgbouncer",
     "steps": "1) Check SHOW POOLS for cl_waiting > 0. 2) PAUSE payments; wait for active txns to finish. "
              "3) Raise default_pool_size 40->120 and max_client_conn 400->1200. 4) RESUME payments. "
              "5) Kill idle-in-transaction sessions older than 60s on payments-db."},
    {"id": "RB-9", "title": "Rebuild a missing index concurrently",
     "service": "payments-db",
     "steps": "1) Find seq scans in pg_stat_user_tables. 2) CREATE INDEX CONCURRENTLY. 3) ANALYZE the table."},
    {"id": "RB-3", "title": "Fail over a Kafka broker",
     "service": "kafka",
     "steps": "1) Confirm under-replicated partitions. 2) Trigger preferred leader election. 3) Replace broker node."},
    {"id": "RB-12", "title": "Flush and warm the session cache",
     "service": "session-cache",
     "steps": "1) Enable read-through fallback to user-db. 2) FLUSHALL on the replica first. 3) Warm top 10k keys."},
    {"id": "RB-15", "title": "Rotate an expired card network key",
     "service": "secrets-vault",
     "steps": "1) Fetch new key from network portal. 2) vault kv put cards/visa. 3) Rolling restart card-gateway."},
    {"id": "RB-4", "title": "Restart Payments DB replica",
     "service": "payments-db",
     "steps": "1) Remove replica from pool. 2) Restart Postgres. 3) Re-add when replication lag < 1s."},
]

# Incidents. root = service at fault; affected = services that degraded.
INCIDENTS = [
    {"id": "INC-142", "date": "2026-03-11", "sev": 1, "duration_min": 74,
     "title": "Checkout failing: payments-db connection pool exhausted",
     "summary": "A retry storm from payouts-service opened thousands of connections. PgBouncer queued clients, "
                "payments-api timed out and checkout error rate hit 38%. Fixed by draining and resizing the pool.",
     "root": "pgbouncer", "affected": ["payments-db", "payments-api", "ledger-service", "payouts-service"],
     "resolved_by": ["sarah", "daniel"], "runbook": "RB-7", "customers": ["northwind", "orbit", "bluebird", "tidewater"]},
    {"id": "INC-098", "date": "2025-11-02", "sev": 2, "duration_min": 51,
     "title": "Ledger lag after Kafka broker loss",
     "summary": "One Kafka broker died; ledger consumers fell 20 minutes behind and payouts were delayed.",
     "root": "kafka", "affected": ["ledger-service", "payouts-service", "analytics-pipeline"],
     "resolved_by": ["daniel"], "runbook": "RB-3", "customers": ["copperline", "northwind"]},
    {"id": "INC-171", "date": "2026-05-20", "sev": 2, "duration_min": 38,
     "title": "Slow payments-db queries after index dropped in migration",
     "summary": "A migration dropped idx_payments_merchant_created. Queries on payments-db went to seq scans, "
                "p99 latency of payments-api rose to 4s.",
     "root": "payments-db", "affected": ["payments-api", "reporting-api"],
     "resolved_by": ["sarah", "thomas"], "runbook": "RB-9", "customers": ["helix", "lumen"]},
    {"id": "INC-188", "date": "2026-08-14", "sev": 2, "duration_min": 27,
     "title": "Logins failing: auth-service token signing key expired",
     "summary": "The JWT signing key in secrets-vault expired; auth-service rejected all new sessions.",
     "root": "secrets-vault", "affected": ["auth-service", "api-gateway", "mobile-bff", "merchant-dashboard"],
     "resolved_by": ["alice", "fiona"], "runbook": "RB-15", "customers": ["bluebird", "orbit", "helix"]},
    {"id": "INC-201", "date": "2026-09-22", "sev": 2, "duration_min": 33,
     "title": "Session cache eviction storm",
     "summary": "Memory pressure on session-cache evicted hot keys; auth-service and fraud-scorer hammered user-db.",
     "root": "session-cache", "affected": ["auth-service", "fraud-scorer", "user-db"],
     "resolved_by": ["james", "alice"], "runbook": "RB-12", "customers": ["orbit", "saffron"]},
    {"id": "INC-155", "date": "2026-04-03", "sev": 3, "duration_min": 95,
     "title": "Push notifications delayed",
     "summary": "APNs certificate rotation broke push-service for iOS.",
     "root": "push-service", "affected": ["notifications-service"],
     "resolved_by": ["samuel"], "runbook": None, "customers": ["bluebird"]},
    {"id": "INC-163", "date": "2026-04-29", "sev": 3, "duration_min": 120,
     "title": "Merchant reports stale",
     "summary": "Warehouse load job stuck; reporting-api served day-old data.",
     "root": "warehouse", "affected": ["reporting-api", "analytics-pipeline"],
     "resolved_by": ["ryan"], "runbook": None, "customers": ["copperline", "tidewater"]},
    {"id": "INC-177", "date": "2026-06-18", "sev": 2, "duration_min": 22,
     "title": "Fraud scorer timeouts at peak",
     "summary": "Feature store latency spike made fraud-scorer time out; payments-api fell back to allow-list.",
     "root": "ml-feature-store", "affected": ["fraud-scorer", "payments-api"],
     "resolved_by": ["sophie"], "runbook": None, "customers": ["orbit", "northwind"]},
    {"id": "INC-120", "date": "2026-01-15", "sev": 3, "duration_min": 40,
     "title": "Payments DB replica lag",
     "summary": "Long-running report query on the payments-db replica caused 6 minutes of replication lag.",
     "root": "payments-db", "affected": ["reporting-api"],
     "resolved_by": ["thomas"], "runbook": "RB-4", "customers": ["helix"]},
    {"id": "INC-193", "date": "2026-09-02", "sev": 3, "duration_min": 18,
     "title": "Promo codes not applying",
     "summary": "Bad deploy of promo-engine ignored stacked discounts.",
     "root": "promo-engine", "affected": ["payments-api"],
     "resolved_by": ["emma"], "runbook": None, "customers": ["lumen", "saffron"]},
]

DOCS = [
    {"id": "PM-142", "kind": "postmortem", "title": "Postmortem: INC-142 payments-db pool exhaustion", "date": "2026-03-14",
     "author": "sarah", "incident": "INC-142", "about": ["pgbouncer", "payments-db", "payouts-service"],
     "text": "Root cause: payouts-service retried failed batches with no backoff, opening ~3,000 client connections. "
             "PgBouncer max_client_conn was 400, so clients queued and payments-api timed out. "
             "What worked: draining the pool and raising limits (RB-7). Restarting payments-db did NOT help and cost 10 minutes. "
             "Action items: TKT-512 make pool limits permanent in Terraform; TKT-513 add exponential backoff to payouts-service."},
    {"id": "PM-171", "kind": "postmortem", "title": "Postmortem: INC-171 missing index", "date": "2026-05-22",
     "author": "thomas", "incident": "INC-171", "about": ["payments-db"],
     "text": "Migration 0457 dropped idx_payments_merchant_created. Add a CI check that diffs indexes before deploy (TKT-530)."},
    {"id": "PM-098", "kind": "postmortem", "title": "Postmortem: INC-098 Kafka broker loss", "date": "2025-11-05",
     "author": "daniel", "incident": "INC-098", "about": ["kafka", "ledger-service"],
     "text": "Replication factor for ledger topics was 2. Raised to 3. Ledger consumers need lag alerts."},
    {"id": "PM-188", "kind": "postmortem", "title": "Postmortem: INC-188 expired signing key", "date": "2026-08-16",
     "author": "alice", "incident": "INC-188", "about": ["auth-service", "secrets-vault"],
     "text": "Signing key had a 1-year TTL nobody tracked. Added expiry alerts 30 days ahead."},
    {"id": "PM-201", "kind": "postmortem", "title": "Postmortem: INC-201 session cache evictions", "date": "2026-09-24",
     "author": "james", "incident": "INC-201", "about": ["session-cache", "auth-service", "fraud-scorer"],
     "text": "maxmemory-policy was allkeys-lru with fraud features sharing the instance. Split fraud features out (TKT-560)."},
    {"id": "DD-31", "kind": "design", "title": "Design: Async ledger writes", "date": "2026-03-22",
     "author": "hannah", "incident": None, "about": ["ledger-service", "kafka", "payments-db"],
     "text": "Move ledger writes off the synchronous payment path onto Kafka to cut payments-db connections by ~60%."},
    {"id": "DD-18", "kind": "design", "title": "Design: PgBouncer in transaction mode", "date": "2025-07-10",
     "author": "hannah", "incident": None, "about": ["pgbouncer", "payments-db"],
     "text": "Run PgBouncer in transaction pooling mode. Session-level features (advisory locks) are not allowed in payments code."},
]

# (id, channel, ts, author, text, mentions_services, mentions_incidents)
MESSAGES = [
    ("MSG-1", "#inc-142", "2026-03-11T09:14", "marcus", "checkout 5xx spiking, anyone know payments-db?", ["payments-db"], ["INC-142"]),
    ("MSG-2", "#inc-142", "2026-03-11T09:21", "helen", "restarting payments-db primary now", ["payments-db"], ["INC-142"]),
    ("MSG-3", "#inc-142", "2026-03-11T09:33", "sarah", "restart won't help, it's pgbouncer: cl_waiting is 2.8k. payouts is retry-storming. draining pool + raising max_client_conn", ["pgbouncer", "payouts-service"], ["INC-142"]),
    ("MSG-4", "#inc-142", "2026-03-11T10:20", "daniel", "error rate back to baseline after pool resize. nice catch Sarah", ["pgbouncer"], ["INC-142"]),
    ("MSG-5", "#team-payments", "2026-03-20T15:02", "sarah", "proposal: we stop writing ledger entries synchronously, it's half our db connections. Hannah has a design", ["ledger-service", "payments-db"], []),
    ("MSG-6", "#team-payments", "2026-03-20T15:10", "helen", "agreed, let's do it. Hannah please write the ADR", ["ledger-service"], []),
    ("MSG-7", "#team-platform", "2026-06-02T11:00", "sarah", "hi all, joining platform today. still happy to help with pgbouncer/payments-db stuff", ["pgbouncer", "payments-db"], []),
    ("MSG-8", "#team-payments", "2026-10-01T10:00", "marcus", "is TKT-512 still needed? not sure what it's for", [], []),
    ("MSG-9", "#inc-201", "2026-09-22T14:05", "james", "session-cache evicting like crazy, enabling read-through to user-db", ["session-cache", "user-db"], ["INC-201"]),
]

# (id, title, status, assignee, service, follow_up_of)
TICKETS = [
    ("TKT-512", "Make PgBouncer pool limits permanent in Terraform", "open", "marcus", "pgbouncer", "INC-142"),
    ("TKT-513", "Exponential backoff for payouts-service retries", "done", "thomas", "payouts-service", "INC-142"),
    ("TKT-530", "CI check: diff DB indexes before deploy", "in_progress", "thomas", "payments-db", "INC-171"),
    ("TKT-560", "Move fraud features to a dedicated Redis", "open", "james", "session-cache", "INC-201"),
    ("TKT-541", "Alert on secret expiry 30 days ahead", "done", "fiona", "secrets-vault", "INC-188"),
    ("TKT-499", "Raise ledger topic replication factor to 3", "done", "daniel", "kafka", "INC-098"),
]

# (id, title, date, status, made_by, affects, derived_from)
DECISIONS = [
    ("ADR-12", "Ledger writes go async through Kafka", "2026-03-25", "accepted", "helen",
     ["ledger-service", "payments-db", "kafka"], ["MSG-5", "MSG-6", "PM-142", "DD-31"]),
    ("ADR-07", "PgBouncer runs in transaction pooling mode", "2025-07-15", "accepted", "hannah",
     ["pgbouncer", "payments-db"], ["DD-18"]),
    ("ADR-15", "Ledger topics use replication factor 3", "2025-11-08", "accepted", "daniel",
     ["kafka", "ledger-service"], ["PM-098"]),
    ("ADR-19", "Fraud features get their own Redis", "2026-09-26", "proposed", "james",
     ["session-cache", "fraud-scorer"], ["PM-201", "MSG-9"]),
]

# Projects: who owns them, who leads and works on them, the systems they touch, their tickets, the decisions they carry
# out and their design docs. Planted on purpose: pool hardening is overdue because its key ticket (TKT-512) sits
# with Marcus, who has never touched pgbouncer and asked what it was for (MSG-8), while Sarah, who knows it best,
# isn't on the project; the fraud-features project is waiting on a decision (ADR-19) that is still only proposed.
PROJECTS = [
    {"id": "PRJ-LEDGER", "name": "Async ledger writes", "status": "in_progress", "due": "2026-12-15",
     "goal": "Move ledger writes onto Kafka to cut payments-db connections by about 60%.", "team": "payments",
     "lead": "hannah", "members": ["hannah", "thomas", "ryan"], "touches": ["ledger-service", "kafka", "payments-db"],
     "implements": ["ADR-12"], "docs": ["DD-31"], "tickets": [], "follows": []},
    {"id": "PRJ-POOL", "name": "PgBouncer pool hardening", "status": "in_progress", "due": "2026-09-30",
     "goal": "Make the INC-142 pool fixes permanent so the outage can't come back.", "team": "platform",
     "lead": "daniel", "members": ["daniel", "james", "marcus"], "touches": ["pgbouncer", "payments-db", "payouts-service"],
     "implements": ["ADR-07"], "docs": ["PM-142", "DD-18"], "tickets": ["TKT-512", "TKT-513"], "follows": ["INC-142"]},
    {"id": "PRJ-FRAUD-REDIS", "name": "Dedicated Redis for fraud features", "status": "planned", "due": "2027-01-31",
     "goal": "Stop fraud features from evicting user sessions (INC-201).", "team": "platform",
     "lead": "james", "members": ["james", "sophie"], "touches": ["session-cache", "fraud-scorer"],
     "implements": ["ADR-19"], "docs": ["PM-201"], "tickets": ["TKT-560"], "follows": ["INC-201"]},
    {"id": "PRJ-INDEX-CI", "name": "Index safety checks in CI", "status": "in_progress", "due": "2026-11-15",
     "goal": "Catch dropped database indexes before they reach production (INC-171).", "team": "payments",
     "lead": "thomas", "members": ["thomas"], "touches": ["payments-db"],
     "implements": [], "docs": ["PM-171"], "tickets": ["TKT-530"], "follows": ["INC-171"]},
    {"id": "PRJ-SECRETS", "name": "Secret expiry alerts", "status": "done", "due": "2026-09-01",
     "goal": "Warn 30 days before any signing key or secret expires (INC-188).", "team": "identity",
     "lead": "alice", "members": ["alice", "fiona"], "touches": ["secrets-vault", "auth-service"],
     "implements": [], "docs": ["PM-188"], "tickets": ["TKT-541"], "follows": ["INC-188"]},
]


# ---------------------------------------------------------------------------
# A second, much smaller tenant to show graph-per-tenant isolation.
LABS = {
    "teams": [{"id": "core", "name": "Core", "slack": "#core"}],
    "people": [("ivy", "Ivy Park", "Engineer", [("core", "2024-01-01", None)])],
    "on_call": {"core": "ivy"},
    "services": [
        ("web", "Ravenhold Labs Storefront", 0, "core", "frontend", "Ravenhold Labs storefront"),
        ("orders-db", "Orders DB", 0, "core", "database", "Orders Postgres"),
    ],
    "depends_on": [("web", "orders-db")],
}
