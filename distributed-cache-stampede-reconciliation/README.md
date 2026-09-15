# Harbor Task: `distributed-cache-stampede-reconciliation`

## Task Overview
This benchmark evaluates an agent's ability to diagnose and fix a **cache stampede** incident in a distributed real-time analytics system. The agent must implement single-flight request coalescing, recover a corrupted SQLite WAL journal without data loss, backfill missing data, and produce a post-mortem report.

---

## Directory Structure & Component Purpose

```
collinear-candidate/distributed-cache-stampede-reconciliation/
├── task.toml                          # Harbor metadata: difficulty, limits, tags
├── instruction.md                     # Full incident description & objectives
├── README.md                          # This file
├── environment/
│   └── Dockerfile                     # Container: Python 3.11 + Redis + SQLite + WAL pre-seed
├── src/
│   ├── analytics_service/
│   │   ├── __init__.py                # Package marker
│   │   ├── cache_manager.py           # BUGGY: StampedeProtectedCache - lacks single-flight locking (THE FIX TARGET)
│   │   ├── db.py                      # DatabasePool - connection pool (MAX_CONNECTIONS=5 is HARD CONSTRAINT)
│   │   └── audit_reconciler.py        # STUB: AuditReconciler - needs WAL recovery implementation
│   └── scripts/
│       ├── benchmark.py               # Reproduces stampede: 100 concurrent requests on cold cache
│       └── reconcile_data.py          # STUB: Needs backfill implementation for IDs 51-60
├── solution/
│   └── solve.sh                       # Oracle fix: patches cache_manager, audit_reconciler, reconcile_data, creates POST_MORTEM.md
└── tests/
    ├── test.sh                        # Verification runner: executes reconcile + pytest
    └── test_outputs.py                # Test suite: single-flight, no memory leaks, constraints, DB integrity, post-mortem
```

---

## Step-by-Step Working Guide

### 1. Build & Start the Environment
```bash
cd collinear-candidate/distributed-cache-stampede-reconciliation
docker build -t cache-stampede-task ./environment
docker run -d --name cache-stampede cache-stampede-task
docker exec -it cache-stampede bash
```
**Why:** The Dockerfile installs Redis, SQLite, Python dependencies, creates the `audit.db` in WAL mode with 50 pre-seeded records (ids 1-50, status `PROCESSED`), and starts Redis in background.

### 2. Reproduce the Cache Stampede (Subgoal 1)
```bash
python3 /app/scripts/benchmark.py
```
**Why:** `benchmark.py` creates 100 concurrent tasks calling `get_or_compute("metric_1")` on a cold cache. The buggy `cache_manager.py` has no locking — all 100 requests miss cache and call `db.fetch_metric()` simultaneously, exhausting the 5-connection pool and crashing.

**Expected failure:** Connection errors, timeouts, or SQLite WAL corruption.

### 3. Inspect the Bug (Diagnosis)
```bash
cat /app/analytics_service/cache_manager.py
cat /app/analytics_service/db.py
cat /app/analytics_service/audit_reconciler.py
```
**Why:** 
- `cache_manager.py` — Missing per-key locks; `get_or_compute` has a check-then-act race condition
- `db.py` — `MAX_CONNECTIONS = 5` is a hard constraint (cannot be changed)
- `audit_reconciler.py` — `recover_wal_checkpoint()` is empty (needs implementation)

### 4. Implement the Fix (Subgoal 2: Cache Refactoring)
Edit `/app/analytics_service/cache_manager.py` to add:
- **Per-key asyncio locks** (`_active_locks: Dict[str, asyncio.Lock]`)
- **Double-checked locking pattern**: Check cache → acquire lock → re-check cache → compute → store → release lock
- **Lock cleanup**: Remove lock from dict after release to prevent memory leaks
- **Dict lock** (`_dict_lock`) to protect the locks dictionary itself

**Why:** Single-flight coalescing ensures only ONE request computes the value; 99 waiters block on the lock and receive the same result.

### 5. Implement WAL Recovery (Subgoal 3: Database Reconciliation)
Edit `/app/analytics_service/audit_reconciler.py`:
```python
def recover_wal_checkpoint(self):
    conn = sqlite3.connect(self.db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")  # Truncates WAL, checkpoints to main DB
    conn.close()
```
**Why:** `wal_checkpoint(TRUNCATE)` forces WAL frames back into the main database file and truncates the WAL, resolving the locked/corrupted state without deleting the database.

### 6. Implement Data Backfill (Subgoal 3)
Edit `/app/scripts/reconcile_data.py`:
```python
def main():
    rec = AuditReconciler(DB_PATH)
    rec.recover_wal_checkpoint()
    
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    cursor = conn.cursor()
    for idx in range(51, 61):  # Backfill missing IDs 51-60
        cursor.execute(
            "INSERT OR REPLACE INTO audit_log (id, metric_name, status) VALUES (?, ?, ?)",
            (idx, f"metric_{idx}", "RECONCILED")
        )
    conn.commit()
    conn.close()
    print("Reconciliation complete.")
```
**Why:** The stampede caused missing sequence IDs 51-60. Backfill restores them with status `RECONCILED` while preserving historical records (ids 1-50).

### 7. Run Reconciliation
```bash
python3 /app/scripts/reconcile_data.py
```
**Why:** Executes WAL recovery + backfill. Output: "Reconciliation complete."

### 8. Write Post-Mortem (Subgoal 3 Deliverable)
Create `/app/POST_MORTEM.md` with:
- `## Root Cause` section (minimum 100 words total)
- `## Fix Architecture` section

**Why:** Required deliverable documenting the incident and fix for future reference.

### 9. Run Verification Tests (Subgoal 1-3 Validation)
```bash
cd /app
pytest tests/test_outputs.py -v
```
**Why:** Runs 5 tests validating:
1. `test_cache_stampede_single_flight` — Only 1 DB hit for 100 concurrent requests
2. `test_no_memory_leak_in_lock_registry` — Lock dict empty after 100 unique keys
3. `test_hard_constraints_unviolated` — `MAX_CONNECTIONS == 5` unchanged
4. `test_database_integrity_and_backfill` — 50 historical records intact, ≥10 `RECONCILED`
5. `test_post_mortem_deliverable` — POST_MORTEM.md exists with required sections

### 10. (Optional) Run Harbor Verification
```bash
./tests/test.sh
cat /logs/verifier/reward.txt  # Should output "1" for pass
```
**Why:** `test.sh` is the official Harbor verifier — runs reconcile script then pytest, writes reward 1/0.

---

## Quick Test: Apply Oracle Solution
```bash
# From host (outside container)
docker cp solution/solve.sh cache-stampede:/app/solution/solve.sh
docker exec cache-stampede bash /app/solution/solve.sh
docker exec cache-stampede pytest /app/tests/test_outputs.py -v
```
**Why:** Applies the reference implementation and validates it passes all tests.

---

## Key Constraints Summary
| Constraint | Enforcement |
|------------|-------------|
| No stale reads | Tests verify fresh computation only |
| No pool changes | `test_hard_constraints_unviolated` asserts `MAX_CONNECTIONS == 5` |
| No DB deletion | `test_database_integrity_and_backfill` checks file exists + 50 records intact |
| No memory leaks | `test_no_memory_leak_in_lock_registry` asserts empty lock dict |
| Post-mortem required | `test_post_mortem_deliverable` checks file + sections + word count |

---

## Expected Time
- **Diagnosis & reproduction:** 5-10 min
- **Cache fix:** 15-25 min
- **WAL recovery + backfill:** 10-15 min
- **Post-mortem:** 5-10 min
- **Testing:** 5 min
- **Total:** ~45-60 min