from __future__ import annotations
import asyncio
from datetime import datetime, timezone

DEFAULT_WARNING_BYTES=5 * 1024**3
DEFAULT_CRITICAL_BYTES=8 * 1024**3

class StorageHealthMonitor:
    def __init__(self,database_url:str,warning_bytes:int=DEFAULT_WARNING_BYTES,critical_bytes:int=DEFAULT_CRITICAL_BYTES):
        self.database_url=str(database_url or "").strip()
        self.warning_bytes=int(warning_bytes)
        self.critical_bytes=int(critical_bytes)

    def _connect(self):
        import psycopg
        return psycopg.connect(self.database_url,connect_timeout=10)

    def _run(self):
        with self._connect() as c:
            with c.cursor() as cur:
                cur.execute("SELECT pg_database_size(current_database())")
                db_bytes=int(cur.fetchone()[0] or 0)
                cur.execute("""
                    SELECT relname, pg_total_relation_size(relid)
                    FROM pg_catalog.pg_statio_user_tables
                    ORDER BY pg_total_relation_size(relid) DESC
                    LIMIT 20
                """)
                largest=[{"table":r[0],"bytes":int(r[1] or 0)} for r in cur.fetchall()]
                counts={}
                for table in ("commodity_candles","universe_candles","fno_option_chain_snapshots"):
                    cur.execute("SELECT to_regclass(%s)",(table,))
                    exists=cur.fetchone()[0] is not None
                    if not exists:
                        counts[table]=0
                        continue
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    counts[table]=int(cur.fetchone()[0] or 0)
                freshness={}
                for table,col in (("commodity_candles","candle_at"),("universe_candles","candle_at"),("fno_option_chain_snapshots","observed_at")):
                    cur.execute("SELECT to_regclass(%s)",(table,))
                    if cur.fetchone()[0] is None:
                        freshness[table]=None
                        continue
                    cur.execute(f"SELECT MAX({col}) FROM {table}")
                    v=cur.fetchone()[0]
                    freshness[table]=v.isoformat() if v else None
        status="CRITICAL" if db_bytes>=self.critical_bytes else "WARNING" if db_bytes>=self.warning_bytes else "OK"
        return {"status":status,"checked_at":datetime.now(timezone.utc).isoformat(),"database_bytes":db_bytes,
                "warning_bytes":self.warning_bytes,"critical_bytes":self.critical_bytes,
                "largest_relations":largest,"row_counts":counts,"freshest_timestamp":freshness}

    async def run(self):
        return await asyncio.to_thread(self._run)
