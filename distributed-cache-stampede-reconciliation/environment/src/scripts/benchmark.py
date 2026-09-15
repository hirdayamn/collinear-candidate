import asyncio
import sys
import time
sys.path.insert(0, "/app")

from analytics_service.cache_manager import StampedeProtectedCache
from analytics_service.db import DatabasePool

async def run_benchmark():
    cache = StampedeProtectedCache()
    await cache.init()
    db = DatabasePool()

    print("Starting high-concurrency load benchmark (100 requests)...")

    async def get_data():
        return await cache.get_or_compute(
            "metric_1",
            ttl=10,
            compute_fn=lambda: db.fetch_metric("metric_1")
        )

    try:
        results = await asyncio.gather(*[get_data() for _ in range(100)])
        print(f"Successfully processed {len(results)} requests.")
    except Exception as e:
        print(f"Benchmark failed with error: {e}")

if __name__ == "__main__":
    asyncio.run(run_benchmark())