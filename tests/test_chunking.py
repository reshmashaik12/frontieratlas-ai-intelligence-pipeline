from src.chunking import semantic_chunks


def test_small_payload_is_one_chunk():
    assert semantic_chunks("Hello world.") == ["Hello world."]


def test_chunks_respect_limit():
    text = "A complete sentence. " * 3000
    chunks = semantic_chunks(text, max_chars=1000, overlap=50)
    assert len(chunks) > 1
    assert all(len(chunk) <= 1000 for chunk in chunks)
