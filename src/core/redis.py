import json
import redis
from typing import Optional, Any
from src.core.config import settings

# Global connection pool
_redis_pool: Optional[redis.ConnectionPool] = None

def get_redis_pool() -> redis.ConnectionPool:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5.0
        )
    return _redis_pool

def get_redis_client() -> redis.Redis:
    pool = get_redis_pool()
    return redis.Redis(connection_pool=pool)

def cache_set(key: str, value: Any, ttl_seconds: int = 3600) -> bool:
    """Stores a serialized JSON value in Redis with a TTL."""
    try:
        r = get_redis_client()
        serialized = json.dumps(value)
        return bool(r.setex(key, ttl_seconds, serialized))
    except Exception as e:
        print(f"Redis Cache Set Error for key {key}: {e}")
        return False

def cache_get(key: str) -> Optional[Any]:
    """Retrieves and deserializes a JSON value from Redis."""
    try:
        r = get_redis_client()
        val = r.get(key)
        if val:
            return json.loads(val)
    except Exception as e:
        print(f"Redis Cache Get Error for key {key}: {e}")
    return None

def set_job_status(job_id: str, status_data: dict, ttl_seconds: int = 86400) -> bool:
    """Updates job status tracking information in Redis with a 24h TTL."""
    try:
        r = get_redis_client()
        key = f"job:{job_id}"
        serialized = json.dumps(status_data)
        return bool(r.setex(key, ttl_seconds, serialized))
    except Exception as e:
        print(f"Redis Set Job Status Error for job {job_id}: {e}")
        return False

def get_job_status(job_id: str) -> Optional[dict]:
    """Retrieves job status tracking information from Redis."""
    try:
        r = get_redis_client()
        key = f"job:{job_id}"
        val = r.get(key)
        if val:
            return json.loads(val)
    except Exception as e:
        print(f"Redis Get Job Status Error for job {job_id}: {e}")
    return None
