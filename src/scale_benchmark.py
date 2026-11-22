import argparse
import asyncio
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .storage import Storage


def synthetic_rows(record_count, collected_at):
    for index in range(record_count):
        yield (
            "BENCHMARK",
            f"https://synthetic.invalid/record/{index}",
            json.dumps({"synthetic": True, "index": index}),
            "isolated synthetic benchmark",
            collected_at,
        )


async def benchmark(record_count):
    with tempfile.TemporaryDirectory(prefix="frontier-scale-") as directory:
        database_path = Path(directory) / "benchmark.sqlite"
        storage = Storage(str(database_path))
        await storage.initialize()
        started = time.perf_counter()
        await storage.put_many(
            synthetic_rows(record_count, datetime.now(timezone.utc).isoformat())
        )
        elapsed = time.perf_counter() - started
        async with aiosqlite.connect(database_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM records")
            stored_records = (await cursor.fetchone())[0]
        if stored_records != record_count:
            raise RuntimeError(
                f"Expected {record_count} benchmark rows; stored {stored_records}"
            )
        return {
            "database_write_only": True,
            "synthetic_records": stored_records,
            "source_records_ingested": 0,
            "elapsed_seconds": round(elapsed, 2),
            "records_per_second": round(stored_records / elapsed, 2),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Benchmark bounded SQLite batches using isolated synthetic data."
    )
    parser.add_argument("--records", type=int, default=500_000)
    args = parser.parse_args()
    if args.records < 1:
        parser.error("--records must be greater than zero")
    print(json.dumps(asyncio.run(benchmark(args.records)), indent=2))
