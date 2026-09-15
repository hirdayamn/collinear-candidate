import asyncio
import json
import redis.asyncio as redis

class StampedeProtectedCache:
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_url = redis_url
        self.redis = None

    async def init(self):
        self.redis = await redis.from_url(self.redis_url)

    async def get_or_compute(self, key: str, ttl: int, compute_fn):
        val = await self.redis.get(key)
        if val is not None:
            return json.loads(val)

        result = await compute_fn()
        await self.redis.set(key, json.dumps(result), ex=ttl)
        return result