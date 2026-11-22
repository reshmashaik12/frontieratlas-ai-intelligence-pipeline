import json

import pytest

from src.storage import Storage


@pytest.mark.asyncio
async def test_batch_upsert_updates_records_idempotently(tmp_path):
    storage = Storage(str(tmp_path / "records.db"))
    await storage.initialize()
    url = "https://example.com/item"
    await storage.put_many([
        ("PRODUCT", url, json.dumps({"version": 1}), "Example", "2026-11-22T00:00:00+00:00")
    ])
    await storage.put_many([
        ("PRODUCT", url, json.dumps({"version": 2}), "Example", "2026-11-22T01:00:00+00:00")
    ])
    rows = await storage.export_rows("PRODUCT")
    assert len(rows) == 1
    exported = json.loads(rows[0])
    assert exported["version"] == 2
    assert exported["collectedAt"] == "2026-11-22T01:00:00+00:00"

    mapping = ("Example", "Example Inc.", url)
    await storage.put_entity_mappings([mapping, mapping])
    assert await storage.export_entity_mappings() == [{
        "rawName": "Example",
        "canonicalName": "Example Inc.",
        "sourceUrl": url,
    }]


@pytest.mark.asyncio
async def test_batch_storage_accepts_generators_across_multiple_chunks(tmp_path):
    storage = Storage(str(tmp_path / "large-batch.db"))
    await storage.initialize()
    collected_at = "2026-11-22T00:00:00+00:00"
    rows = (
        (
            "BENCHMARK",
            f"https://synthetic.invalid/record/{index}",
            json.dumps({"index": index}),
            "isolated test data",
            collected_at,
        )
        for index in range(2_105)
    )
    await storage.put_many(rows)
    assert len(await storage.export_rows("BENCHMARK")) == 2_105


@pytest.mark.asyncio
async def test_startup_migration_nests_optional_fields_without_inventing_values(tmp_path):
    storage = Storage(str(tmp_path / "startup-migration.db"))
    await storage.initialize()
    url = "https://github.com/example"
    old_record = {
        "schemaVersion": "1.0",
        "recordType": "STARTUP",
        "source": {"name": "GitHub", "url": url},
        "content": {
            "entityName": "Example",
            "employeeCount": None,
            "productSourceUrl": "https://github.com/example/project",
        },
        "collectedAt": "2026-11-22T00:00:00+00:00",
    }
    await storage.put_many([(
        "STARTUP", url, json.dumps(old_record), "GitHub",
        "2026-11-22T00:00:00+00:00",
    )])
    await storage.normalize_startup_records()
    record = json.loads((await storage.export_rows("STARTUP"))[0])
    assert record["content"]["entityName"] == "Example"
    assert record["content"]["data"] == {
        "productSourceUrl": "https://github.com/example/project",
    }
    assert "employeeCount" not in record["content"]["data"]


@pytest.mark.asyncio
async def test_export_rows_preserves_storage_collection_timestamp(tmp_path):
    storage = Storage(str(tmp_path / "collected-at.db"))
    await storage.initialize()
    collected_at = "2026-11-22T01:00:00+00:00"
    record = {
        "schemaVersion": "1.0",
        "recordType": "STARTUP",
        "source": {"name": "GitHub", "url": "https://github.com/example"},
        "content": {"entityName": "Example", "data": {}},
    }
    await storage.put_many([(
        "STARTUP", "https://github.com/example", json.dumps(record),
        "GitHub", collected_at,
    )])
    exported = json.loads((await storage.export_rows("STARTUP"))[0])
    assert exported["collectedAt"] == collected_at
