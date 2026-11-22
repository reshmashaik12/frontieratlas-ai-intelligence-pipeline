import json

from src.entity_resolution import EntityResolver


def test_alias_resolution():
    resolver = EntityResolver("config/canonical_entities.json")
    assert resolver.resolve("Open AI") == "OpenAI"
    assert resolver.resolve("OpenAI, Inc.") == "OpenAI"


def test_unknown_entity_is_preserved():
    resolver = EntityResolver("config/canonical_entities.json")
    assert resolver.resolve("A New Company") == "A New Company"


def test_unlisted_near_match_is_not_fuzzily_mapped():
    resolver = EntityResolver("config/canonical_entities.json")
    assert resolver.resolve("OpenAI Research Group") == "OpenAI Research Group"


def test_every_curated_alias_maps_to_its_declared_canonical_entity():
    with open("config/canonical_entities.json", encoding="utf-8") as stream:
        seed = json.load(stream)
    resolver = EntityResolver("config/canonical_entities.json")
    for canonical, aliases in seed.items():
        for raw_name in [canonical, *aliases]:
            assert resolver.resolve(raw_name) == canonical
