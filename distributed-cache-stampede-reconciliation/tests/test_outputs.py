import pytest
import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, "/app")

from analytics_service.cache_manager import StampedeProtectedCache
from analytics_service.db import DatabasePool, MAX_CONNECTIONS

DB_PATH = "/var/log/services/audit.db"

@pytest.mark.asyncio
async def test_cache_stampede_single_flight():
    cache = StampedeProtectedCache()
    await cache.init()
    db = DatabasePool()

    db_hit_count = 0

    async def mock_compute():
        nonlocal db_hit_count
        db_hit_count += 1
        return await db.fetch_metric("metric_1")

    tasks = [
        cache.get_or_compute("metric_1_test", ttl=10, compute_fn=mock_compute)
        for _ in range(100)
    ]
    results = await asyncio.gather(*tasks)

    assert len(results) == 100
    assert db_hit_count == 1, f"Cache stampede! Expected 1 DB query, got {db_hit_count}."

@pytest.mark.asyncio
async def test_no_memory_leak_in_lock_registry():
    cache = StampedeProtectedCache()
    await cache.init()

    async def mock_compute():
        return {"data": "ok"}

    for i in range(100):
        await cache.get_or_compute(f"temp_key_{i}", ttl=1, compute_fn=mock_compute)

    assert len(cache._active_locks) == 0, f"Memory leak! {len(cache._active_locks)} residual locks retained."

def test_hard_constraints_unviolated():
    assert MAX_CONNECTIONS == 5, "Constraint violation: MAX_CONNECTIONS was modified in db.py!"

def test_database_integrity_and_backfill():
    assert os.path.exists(DB_PATH), "Database file was deleted!"

    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM audit_log WHERE id <= 50")
    initial_count = cursor.fetchone()[0]
    assert initial_count == 50, "Historical database records were modified or deleted!"

    cursor.execute("SELECT COUNT(*) FROM audit_log WHERE status = 'RECONCILED'")
    reconciled_count = cursor.fetchone()[0]
    conn.close()

    assert reconciled_count >= 10, "Reconciliation backfill incomplete or missing!"

def test_post_mortem_deliverable():
    filepath = "/app/POST_MORTEM.md"
    assert os.path.exists(filepath), "POST_MORTEM.md deliverable is missing!"

    with open(filepath, "r") as f:
        content = f.read()

    assert len(content.split()) >= 80, "POST_MORTEM.md is too brief."
    assert "Root Cause" in content, "Missing 'Root Cause' section."
    assert "Fix Architecture" in content, "Missing 'Fix Architecture' section."