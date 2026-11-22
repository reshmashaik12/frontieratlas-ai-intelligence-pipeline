"""Place source-specific adapters here.

Each adapter must return only records linked to a fetched source URL. It must reject
missing provenance and never generate a startup, product, or metric from model text.
"""

from typing import Protocol, Iterable, Mapping, Any


class SourceAdapter(Protocol):
    async def discover(self) -> Iterable[str]: ...

    async def fetch(self, url: str) -> Mapping[str, Any]: ...
