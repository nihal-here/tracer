import json
import logging
import redis
import os

logger = logging.getLogger(__name__)

# 1. Initialize the Redis client.
# By default, Docker Redis runs on localhost, port 6379, with database 0.
redis_client = redis.Redis(
    host=os.environ.get("REDIS_HOST", "localhost"),
    port=int(os.environ.get("REDIS_PORT", 6379)),
    db=0,
    decode_responses=True # This automatically decodes the byte responses to strings!
)


def get_cached_response(url: str) -> dict | list | None:
    """
    Check if the URL exists in the Redis cache.
    If it does, return the parsed JSON.
    If not, return None.
    """
    data=redis_client.get(url)
    if data:
        return json.loads(data)
    return None


def set_cached_response(url: str, data: dict | list, ttl_seconds: int = 3600):
    """
    Save the JSON data to Redis using the URL as the key.
    Set the key to expire after `ttl_seconds` (default 1 hour).
    """
    string_data=json.dumps(data)
    redis_client.setex(url,ttl_seconds,string_data)
