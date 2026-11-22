import csv, json
from pathlib import Path


def export_csv(directory: str, name: str, rows):
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}.csv"
    decoded = [json.loads(r) if isinstance(r, str) else r for r in rows]
    if not decoded:
        path.write_text("", encoding="utf-8")
        return path
    fieldnames = sorted({key for row in decoded for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(
            {
                key: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                if isinstance(value, (dict, list))
                else value
                for key, value in row.items()
            }
            for row in decoded
        )
    return path
