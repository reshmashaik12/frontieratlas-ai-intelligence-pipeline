import aiosqlite, hashlib, json
from datetime import datetime, timezone
from dateutil import parser
from itertools import islice

WRITE_BATCH_SIZE = 1_000


def _batches(rows, size):
    iterator = iter(rows)
    while True:
        batch = list(islice(iterator, size))
        if not batch:
            return
        yield batch

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
  url TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL,
  record_type TEXT NOT NULL,
  first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
  id INTEGER PRIMARY KEY,
  record_type TEXT NOT NULL,
  source_url TEXT NOT NULL,
  source_name TEXT,
  collected_at TEXT NOT NULL,
  payload TEXT NOT NULL,
  UNIQUE(record_type, source_url)
);
CREATE TABLE IF NOT EXISTS entity_map (
  raw_name TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  source_url TEXT NOT NULL,
  PRIMARY KEY(raw_name, source_url)
);
"""

class Storage:
    def __init__(self, path="data/frontier.db"):
        self.path = path

    async def initialize(self):
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    @staticmethod
    def fingerprint(url: str, payload: str):
        return hashlib.sha256(f"{url}\n{payload}".encode()).hexdigest()

    async def put(self, record_type: str, url: str, payload: str, source_name="", collected_at=""):
        fp = self.fingerprint(url, payload)
        async with aiosqlite.connect(self.path) as db:
            try:
                await db.execute("INSERT INTO seen(url,content_hash,record_type,first_seen) VALUES(?,?,?,?)",
                                 (url, fp, record_type, collected_at))
                await db.execute("INSERT OR IGNORE INTO records(record_type,source_url,source_name,collected_at,payload) VALUES(?,?,?,?,?)",
                                 (record_type, url, source_name, collected_at, payload))
                await db.commit()
                return True
            except aiosqlite.IntegrityError:
                cur = await db.execute("SELECT content_hash FROM seen WHERE url=?", (url,))
                old = await cur.fetchone()
                if old and old[0] != fp:
                    await db.execute("UPDATE seen SET content_hash=? WHERE url=?", (fp, url))
                    await db.execute("UPDATE records SET payload=? WHERE record_type=? AND source_url=?", (payload, record_type, url))
                    await db.commit()
                    return True
                if old and old[0] == fp:
                    await db.execute(
                        "INSERT OR IGNORE INTO records(record_type,source_url,source_name,collected_at,payload) VALUES(?,?,?,?,?)",
                        (record_type, url, source_name, collected_at, payload),
                    )
                    await db.commit()
                return False

    async def put_many(self, rows):
        async with aiosqlite.connect(self.path) as db:
            for batch in _batches(rows, WRITE_BATCH_SIZE):
                await db.execute("BEGIN")
                try:
                    await db.executemany(
                        """
                        INSERT INTO seen(url, content_hash, record_type, first_seen)
                        VALUES(?,?,?,?)
                        ON CONFLICT(url) DO UPDATE SET
                            content_hash = excluded.content_hash,
                            record_type = excluded.record_type
                        """,
                        [
                            (
                                url,
                                self.fingerprint(url, payload),
                                record_type,
                                collected_at,
                            )
                            for record_type, url, payload, _source_name, collected_at in batch
                        ],
                    )
                    await db.executemany(
                        """
                        INSERT INTO records(record_type, source_url, source_name, collected_at, payload)
                        VALUES(?,?,?,?,?)
                        ON CONFLICT(record_type, source_url) DO UPDATE SET
                            source_name = excluded.source_name,
                            collected_at = excluded.collected_at,
                            payload = excluded.payload
                        """,
                        [
                            (record_type, url, source_name, collected_at, payload)
                            for record_type, url, payload, source_name, collected_at in batch
                        ],
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise

    async def put_entity_mapping(self, raw_name: str, canonical_name: str, source_url: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO entity_map(raw_name,canonical_name,source_url) VALUES(?,?,?)",
                (raw_name, canonical_name, source_url),
            )
            await db.commit()

    async def put_entity_mappings(self, mappings):
        async with aiosqlite.connect(self.path) as db:
            for batch in _batches(mappings, WRITE_BATCH_SIZE):
                await db.executemany(
                    "INSERT OR IGNORE INTO entity_map(raw_name,canonical_name,source_url) VALUES(?,?,?)",
                    batch,
                )
                await db.commit()

    async def normalize_startup_records(self):
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT id, payload FROM records WHERE record_type = 'STARTUP'"
            )
            rows = await cursor.fetchall()
            updates = []
            for record_id, payload in rows:
                record = json.loads(payload)
                content = record["content"]
                data = content.get("data")
                if data is None:
                    data = {}
                if not isinstance(data, dict):
                    raise ValueError(f"Startup record {record_id} has invalid content.data")
                original_content = json.dumps(content, ensure_ascii=False)
                employee_count = content.pop("employeeCount", None)
                product_source_url = content.pop("productSourceUrl", None)
                if employee_count is not None:
                    data["employeeCount"] = employee_count
                if product_source_url is not None:
                    data["productSourceUrl"] = product_source_url
                if data.get("employeeCount") is None:
                    data.pop("employeeCount", None)
                content["data"] = data
                if json.dumps(content, ensure_ascii=False) != original_content:
                    updates.append((json.dumps(record, ensure_ascii=False), record_id))
            if updates:
                await db.executemany(
                    "UPDATE records SET payload = ? WHERE id = ?",
                    updates,
                )
                await db.commit()

    async def export_rows(self, record_type):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT collected_at, payload FROM records WHERE record_type=?",
                (record_type,),
            )
            rows = []
            for collected_at, payload in await cur.fetchall():
                record = json.loads(payload)
                if not record.get("collectedAt"):
                    record["collectedAt"] = collected_at
                rows.append(json.dumps(record, ensure_ascii=False))
            return rows

    async def export_entity_mappings(self):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT raw_name, canonical_name, source_url FROM entity_map ORDER BY raw_name, source_url"
            )
            return [
                {"rawName": raw, "canonicalName": canonical, "sourceUrl": source_url}
                for raw, canonical, source_url in await cur.fetchall()
            ]

    async def purge_older_than(self, record_type: str, cutoff: str):
        async with aiosqlite.connect(self.path) as db:
            cutoff_at = parser.parse(cutoff)
            if cutoff_at.tzinfo is None:
                cutoff_at = cutoff_at.replace(tzinfo=timezone.utc)
            cutoff_at = cutoff_at.astimezone(timezone.utc)
            cur = await db.execute(
                "SELECT source_url, payload FROM records WHERE record_type = ?",
                (record_type,),
            )
            stale_urls = []
            for source_url, payload_text in await cur.fetchall():
                try:
                    payload = json.loads(payload_text)
                    date_value = payload.get("content", {}).get("date")
                    date_at = parser.parse(date_value) if date_value else None
                    if date_at is None:
                        stale_urls.append(source_url)
                        continue
                    if date_at.tzinfo is None:
                        date_at = date_at.replace(tzinfo=timezone.utc)
                    date_at = date_at.astimezone(timezone.utc)
                    if date_at < cutoff_at:
                        stale_urls.append(source_url)
                except (ValueError, TypeError, json.JSONDecodeError):
                    stale_urls.append(source_url)
            if stale_urls:
                placeholders = ",".join("?" for _ in stale_urls)
                await db.execute(
                    f"DELETE FROM records WHERE record_type = ? AND source_url IN ({placeholders})",
                    (record_type, *stale_urls),
                )
                await db.execute(
                    f"DELETE FROM seen WHERE url IN ({placeholders})",
                    stale_urls,
                )
            await db.commit()
