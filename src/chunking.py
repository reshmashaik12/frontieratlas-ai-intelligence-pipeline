import re


def semantic_chunks(text: str, max_chars: int = 24_000, overlap: int = 500):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_chars:
        return [text]
    chunks = []
    start = 0
    boundaries = ".!?\n"
    while start < len(text):
        end = min(start + max_chars, len(text))
        cut = max([text.rfind(ch, start, end) for ch in boundaries]) if end < len(text) else end
        if cut <= start:
            cut = end
        chunks.append(text[start:cut + 1].strip())
        start = max(cut + 1 - overlap, start + 1)
    return chunks
