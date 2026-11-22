import json, re, unicodedata
from pathlib import Path


class EntityResolver:
    def __init__(self, seed_path: str):
        data = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        self.aliases = {}
        for canonical, aliases in data.items():
            for raw in [canonical, *aliases]:
                self.aliases[self.normalize(raw)] = canonical

    @staticmethod
    def normalize(value: str) -> str:
        value = unicodedata.normalize("NFKC", value or "").lower()
        value = re.sub(r"[,.]+", " ", value)
        value = re.sub(r"\b(incorporated|inc|llc|corp|corporation|technologies|technology|labs|limited|ltd)\b", " ", value)
        return re.sub(r"[^a-z0-9]+", " ", value).strip()

    def resolve(self, raw_name: str) -> str:
        return self.aliases.get(self.normalize(raw_name), raw_name.strip())
