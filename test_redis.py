import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.core.redis import cache_set, cache_get, set_job_status, get_job_status, get_redis_client

def test_redis_operations():
    print("=== Testing Redis Core Operations ===")
    try:
        # Check connection
        r = get_redis_client()
        ping = r.ping()
        print(f"Redis Ping: {ping}")
        
        # Test Cache Set/Get
        test_data = {"key": "value", "number": 42}
        set_ok = cache_set("test_cache_key", test_data, ttl_seconds=10)
        print(f"Cache Set Status: {set_ok}")
        
        get_val = cache_get("test_cache_key")
        print(f"Cache Retrieved Value: {get_val}")
        assert get_val == test_data
        
        # Test Job Status Set/Get
        job_id = "test_job_123"
        job_status = {"job_id": job_id, "status": "running", "progress": 45}
        job_ok = set_job_status(job_id, job_status, ttl_seconds=10)
        print(f"Job Status Set Status: {job_ok}")
        
        get_job = get_job_status(job_id)
        print(f"Job Status Retrieved Value: {get_job}")
        assert get_job == job_status
        
        print("Redis Operations Test: PASSED")
    except Exception as e:
        print(f"Redis Test Failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_redis_operations()
