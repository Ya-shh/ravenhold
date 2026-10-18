"""FalkorDB connection. One graph per tenant (FalkorDB multigraph)."""

import os
import threading
import time

import redis
from dotenv import load_dotenv
from falkordb import FalkorDB

load_dotenv()

_db = None


# redis-py's default pool raises "Too many connections" once 100 are in use. Under load (many visitors, parallel
# workers) a blocking pool is what we want: a query waits briefly for a free connection instead of failing.
POOL_SIZE = int(os.getenv("RAVENHOLD_DB_POOL", "32"))
POOL_WAIT_S = 30


def client() -> FalkorDB:
    global _db
    if _db is None:
        url = os.getenv("FALKORDB_URL")
        if url:
            url = url.replace("falkors://", "rediss://", 1).replace("falkor://", "redis://", 1)
            pool = redis.BlockingConnectionPool.from_url(url, max_connections=POOL_SIZE, timeout=POOL_WAIT_S,
                                                         decode_responses=True, socket_timeout=None)
        else:
            # Separate fields avoid URL-encoding problems with special characters in passwords.
            ssl = os.getenv("FALKORDB_SSL", "").lower() in ("1", "true", "yes")
            pool = redis.BlockingConnectionPool(
                connection_class=redis.SSLConnection if ssl else redis.Connection,
                host=os.getenv("FALKORDB_HOST", "localhost"),
                port=int(os.getenv("FALKORDB_PORT", "6380")),
                username=os.getenv("FALKORDB_USERNAME") or None,
                password=os.getenv("FALKORDB_PASSWORD") or None,
                decode_responses=True, max_connections=POOL_SIZE, timeout=POOL_WAIT_S,
                socket_timeout=None,  # as FalkorDB's own client: queries carry their own server-side timeouts
            )
        _db = FalkorDB(connection_pool=pool)
    return _db


def graph(tenant: str):
    return client().select_graph(tenant)


_copy_lock = threading.Lock()


def copy_graph(src: str, dst: str, attempts: int = 6) -> None:
    """GRAPH.COPY forks the server, so FalkorDB allows one at a time ("could not fork" otherwise, also during a
    background save). Serialise copies in this process and retry briefly."""
    for i in range(attempts):
        try:
            with _copy_lock:
                graph(src).copy(dst)
            return
        except Exception as e:
            if "fork" not in str(e).lower() or i == attempts - 1:
                raise
            time.sleep(0.3 * (i + 1))


def rows(result) -> list[dict]:
    """Turn a FalkorDB result into a list of dicts keyed by column name."""
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in result.header]
    cols = [c.decode() if isinstance(c, bytes) else c for c in cols]
    return [dict(zip(cols, r)) for r in result.result_set]
