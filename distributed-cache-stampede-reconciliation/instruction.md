# INCIDENT-2026-0814: Real-Time Analytics Cache Stampede & Data Reconciliation

At 08:00 UTC, the real-time analytics engine (`/app/analytics_service`) crashed following a scheduled Redis eviction event under heavy concurrent load.

The service suffered from a cache stampede: hundreds of concurrent requests simultaneously observed a cache miss and flooded the upstream SQLite connection pool, causing connection exhaustion, thrown exceptions, and a broken SQLite WAL write state in `/var/log/services/audit.db`.

## Objectives

### Subgoal 1: Diagnosis & Reproduction
* Inspect the service architecture in `/app/analytics_service/` and logs in `/var/log/services/`.
* Identify the concurrency flaw in `/app/analytics_service/cache_manager.py`.
* Reproduce the failure mode locally by executing `python3 /app/scripts/benchmark.py`.

### Subgoal 2: Cache Refactoring & Single-Flight Locking
* Refactor `/app/analytics_service/cache_manager.py` to implement single-flight request coalescing / per-key distributed locks.
* Ensure lock structures auto-expire or clean up after execution (zero memory leaks).
* Ensure only **one** request recomputes a missing key from the database while concurrent incoming callers wait for that single result.

### Subgoal 3: Database Reconciliation & Deliverable
* Resolve the locked SQLite WAL journal in `/var/log/services/audit.db` using recovery routines in `/app/analytics_service/audit_reconciler.py` **without deleting or re-initializing the database file**.
* Implement `/app/scripts/reconcile_data.py` to backfill missing sequence IDs into the `audit_log` table with status `'RECONCILED'`.
* Write a detailed post-mortem report at `/app/POST_MORTEM.md`.

## Strict Operational Constraints
1. **NO Stale Reads:** You MUST NOT return expired or fallback cache data (`probabilistic early expiration` with stale fallbacks is strictly prohibited).
2. **NO Connection Pool Alterations:** You MUST NOT modify `MAX_CONNECTIONS` or pool timeouts in `/app/analytics_service/db.py`.
3. **NO Database Deletion:** You MUST NOT delete, recreate, or drop schemas in `/var/log/services/audit.db`. Existing historical records (`id <= 50`) must be preserved.
4. **NO Memory Leaks:** Dynamic lock tracking dictionaries must not accumulate keys indefinitely.
5. **Required Deliverable:** Deliver `/app/POST_MORTEM.md` containing `## Root Cause` and `## Fix Architecture` sections (minimum 100 words).