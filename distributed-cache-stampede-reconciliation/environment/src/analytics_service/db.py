import sqlite3
import asyncio

DB_PATH = "/var/log/services/audit.db"
MAX_CONNECTIONS = 5  # HARD CONSTRAINT: Do not modify this constant

class DatabasePool:
    def __init__(self, db_path: str = DB_PATH, max_conns: int = MAX_CONNECTIONS):
        self.db_path = db_path
        self.semaphore = asyncio.Semaphore(max_conns)

    async def fetch_metric(self, metric_id: str):
        async with self.semaphore:
            await asyncio.sleep(0.05)
            conn = sqlite3.connect(self.db_path, timeout=1.0)
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM audit_log WHERE metric_name=?", (metric_id,))
            row = cursor.fetchone()
            conn.close()
            return {"metric": metric_id, "status": row[0] if row else "MISSING"}