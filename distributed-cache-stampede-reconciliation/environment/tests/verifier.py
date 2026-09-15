#!/usr/bin/env python3
"""
Multi-criterion verifier for cache stampede reconciliation task.
Outputs reward.json with detailed scoring.
"""

import json
import os
import subprocess
import sys
import sqlite3

# Add app to path
sys.path.insert(0, "/app")

from analytics_service.cache_manager import StampedeProtectedCache
from analytics_service.db import DatabasePool, MAX_CONNECTIONS

DB_PATH = "/var/log/services/audit.db"
REWARD_FILE = "/logs/verifier/reward.json"


def check_functional_correctness() -> dict:
    """Check core functional correctness: stampede fix + reconciliation."""
    results = {"stampede_fixed": False, "reconciliation_complete": False, "details": []}

    # Test 1: Cache stampede single-flight
    try:
        import asyncio
        cache = StampedeProtectedCache()
        asyncio.run(cache.init())
        db = DatabasePool()

        db_hit_count = 0

        async def mock_compute():
            nonlocal db_hit_count
            db_hit_count += 1
            return await db.fetch_metric("metric_1")

        async def run_test():
            tasks = [
                cache.get_or_compute("metric_1_test", ttl=10, compute_fn=mock_compute)
                for _ in range(100)
            ]
            await asyncio.gather(*tasks)

        asyncio.run(run_test())

        if db_hit_count == 1:
            results["stampede_fixed"] = True
            results["details"].append("✓ Stampede fixed: 100 concurrent → 1 DB query")
        else:
            results["details"].append(f"✗ Stampede NOT fixed: 100 concurrent → {db_hit_count} DB queries")
    except Exception as e:
        results["details"].append(f"✗ Stampede test error: {e}")

    # Test 2: Reconciliation backfill
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM audit_log WHERE id <= 50")
        initial_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM audit_log WHERE status = 'RECONCILED'")
        reconciled_count = cursor.fetchone()[0]
        conn.close()

        if initial_count == 50 and reconciled_count >= 10:
            results["reconciliation_complete"] = True
            results["details"].append(f"✓ Reconciliation complete: {initial_count} historical + {reconciled_count} RECONCILED")
        else:
            results["details"].append(f"✗ Reconciliation incomplete: {initial_count} historical, {reconciled_count} RECONCILED")
    except Exception as e:
        results["details"].append(f"✗ Reconciliation test error: {e}")

    return results


def check_constraint_satisfaction() -> dict:
    """Check hard constraints: MAX_CONNECTIONS=5, DB not deleted."""
    results = {"max_connections_ok": False, "db_preserved": False, "details": []}

    # MAX_CONNECTIONS == 5
    if MAX_CONNECTIONS == 5:
        results["max_connections_ok"] = True
        results["details"].append("✓ MAX_CONNECTIONS == 5 preserved")
    else:
        results["details"].append(f"✗ MAX_CONNECTIONS modified: {MAX_CONNECTIONS}")

    # DB file exists and has 50 records
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM audit_log WHERE id <= 50")
            count = cursor.fetchone()[0]
            conn.close()
            if count == 50:
                results["db_preserved"] = True
                results["details"].append("✓ Historical DB records preserved (50)")
            else:
                results["details"].append(f"✗ Historical records modified: {count}/50")
        except Exception as e:
            results["details"].append(f"✗ DB check error: {e}")
    else:
        results["details"].append("✗ Database file deleted!")

    return results


def check_robustness() -> dict:
    """Check robustness: no memory leak in lock registry."""
    results = {"no_memory_leak": False, "details": []}

    try:
        import asyncio
        cache = StampedeProtectedCache()
        asyncio.run(cache.init())

        async def mock_compute():
            return {"data": "ok"}

        async def run_test():
            for i in range(100):
                await cache.get_or_compute(f"temp_key_{i}", ttl=1, compute_fn=mock_compute)

        asyncio.run(run_test())

        # Check _active_locks property exists and is empty
        if hasattr(cache, '_active_locks'):
            active = len(cache._active_locks)
            if active == 0:
                results["no_memory_leak"] = True
                results["details"].append("✓ No memory leak: lock registry empty after use")
            else:
                results["details"].append(f"✗ Memory leak: {active} residual locks retained")
        else:
            results["details"].append("✗ _active_locks property missing")
    except Exception as e:
        results["details"].append(f"✗ Robustness test error: {e}")

    return results


def check_artifact_quality() -> dict:
    """Check artifact quality: POST_MORTEM.md exists and has required sections."""
    results = {"post_mortem_exists": False, "post_mortem_quality": False, "details": []}

    filepath = "/app/POST_MORTEM.md"
    if os.path.exists(filepath):
        results["post_mortem_exists"] = True
        results["details"].append("✓ POST_MORTEM.md exists")

        try:
            with open(filepath, "r") as f:
                content = f.read()

            word_count = len(content.split())
            has_root_cause = "Root Cause" in content
            has_fix_arch = "Fix Architecture" in content

            if word_count >= 80 and has_root_cause and has_fix_arch:
                results["post_mortem_quality"] = True
                results["details"].append(f"✓ POST_MORTEM.md quality: {word_count} words, required sections present")
            else:
                issues = []
                if word_count < 80:
                    issues.append(f"too brief ({word_count} words)")
                if not has_root_cause:
                    issues.append("missing 'Root Cause'")
                if not has_fix_arch:
                    issues.append("missing 'Fix Architecture'")
                results["details"].append(f"✗ POST_MORTEM.md issues: {', '.join(issues)}")
        except Exception as e:
            results["details"].append(f"✗ POST_MORTEM.md read error: {e}")
    else:
        results["details"].append("✗ POST_MORTEM.md missing")

    return results


def main():
    # Ensure Redis is running
    try:
        subprocess.run(["redis-cli", "ping"], capture_output=True, check=True)
    except subprocess.CalledProcessError:
        subprocess.run(["redis-server", "--daemonize", "yes"], capture_output=True)
        import time
        time.sleep(1)

    # Run reconciliation if script exists
    if os.path.exists("/app/scripts/reconcile_data.py"):
        subprocess.run(["python3", "/app/scripts/reconcile_data.py"], capture_output=True)

    # Run all checks
    print("Running multi-criterion verification...")

    functional = check_functional_correctness()
    constraint = check_constraint_satisfaction()
    robustness = check_robustness()
    artifact = check_artifact_quality()

    # Calculate scores (0.0 or 1.0 per criterion)
    functional_score = 1.0 if (functional["stampede_fixed"] and functional["reconciliation_complete"]) else 0.0
    constraint_score = 1.0 if (constraint["max_connections_ok"] and constraint["db_preserved"]) else 0.0
    robustness_score = 1.0 if robustness["no_memory_leak"] else 0.0
    artifact_score = 1.0 if (artifact["post_mortem_exists"] and artifact["post_mortem_quality"]) else 0.0

    # Overall: all must pass (AND logic)
    overall = 1.0 if all([functional_score, constraint_score, robustness_score, artifact_score]) else 0.0

    reward = {
        "overall": overall,
        "functional_correctness": functional_score,
        "constraint_satisfaction": constraint_score,
        "robustness": robustness_score,
        "artifact_quality": artifact_score
    }

    # Print details
    for d in functional["details"] + constraint["details"] + robustness["details"] + artifact["details"]:
        print(f"  {d}")

    print(f"\nScores:")
    print(f"  Overall: {overall}")
    print(f"  Functional Correctness: {functional_score}")
    print(f"  Constraint Satisfaction: {constraint_score}")
    print(f"  Robustness: {robustness_score}")
    print(f"  Artifact Quality: {artifact_score}")

    # Write reward.json
    os.makedirs(os.path.dirname(REWARD_FILE), exist_ok=True)
    with open(REWARD_FILE, "w") as f:
        json.dump(reward, f, indent=2)

    print(f"\nReward written to {REWARD_FILE}")

    # Exit code for backward compatibility
    sys.exit(0 if overall == 1.0 else 1)


if __name__ == "__main__":
    main()