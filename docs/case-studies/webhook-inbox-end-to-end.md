# Webhook Inbox: a real end-to-end delivery

oh-my-multica received one requirement: deliver a production-constrained Webhook Inbox that authenticates,
stores, and deduplicates third-party webhook events.

Planner and Orchestrator Agents planned the delivery DAG. Worker Agents implemented its nodes in parallel,
Reviewer Agents independently verified each change, and an Acceptor Agent tested the integrated service. The
deterministic Loop controlled dependencies, evidence gates, merge conditions, and final convergence.

The delivered FastAPI and SQLite service verifies HMAC-SHA256 signatures, prevents duplicate event records under
retries and concurrent delivery, rejects conflicting payloads, supports event lookup and health checks, and runs
in a non-root container.

The complete requirement, Agent collaboration, public Pull Requests, service behavior, and verification results
are documented in the
[demo README](https://github.com/xiaohei-info/oh-my-multica-demo-webhook-inbox#readme).
