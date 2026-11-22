import csv
import json

from src.export import export_csv


def test_nested_export_values_are_valid_json(tmp_path):
    path = export_csv(
        str(tmp_path),
        "records",
        [{
            "recordType": "PRODUCT",
            "content": {"productName": "Example", "tags": ["AI", "tools"]},
            "source": {"name": "Example source", "url": "https://example.com/item"},
        }],
    )
    with path.open(newline="", encoding="utf-8") as stream:
        row = next(csv.DictReader(stream))
    assert json.loads(row["content"]) == {
        "productName": "Example",
        "tags": ["AI", "tools"],
    }
    assert json.loads(row["source"])["url"] == "https://example.com/item"
