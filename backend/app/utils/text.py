import re
import unicodedata


def clean_text(text: str, max_len: int | None = None) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("\u00a0", " ")
    normalized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", normalized)
    normalized = re.sub(r"\r\n?", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    normalized = "\n".join(line.strip() for line in normalized.split("\n"))
    normalized = normalized.strip()
    if max_len and len(normalized) > max_len:
        return normalized[:max_len]
    return normalized


def split_paragraph_chunks(text: str, size: int, overlap: int) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > size:
            if current:
                chunks.append(current)
                current = ""
            start = 0
            while start < len(paragraph):
                end = start + size
                chunks.append(paragraph[start:end])
                if end >= len(paragraph):
                    break
                start = max(end - overlap, start + 1)
            continue

        if not current:
            current = paragraph
            continue

        candidate = f"{current}\n\n{paragraph}"
        if len(candidate) <= size:
            current = candidate
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap > 0 else ""
            current = f"{tail}\n\n{paragraph}".strip()
            if len(current) > size:
                chunks.append(current[:size])
                current = current[size - overlap :]

    if current:
        chunks.append(current)

    return [c.strip() for c in chunks if c.strip()]


def extract_section_title(text: str, fallback_title: str = "Источник") -> str:
    lines = [line.strip() for line in (text or "").split("\n") if line.strip()]
    if not lines:
        return fallback_title

    heading_patterns = (
        r"^#{1,6}\s+.+",
        r"^\d+(\.\d+)*[\)\.]?\s+\S+",
        r"^[IVXLCDM]+[\)\.]?\s+\S+",
    )

    for line in lines[:20]:
        candidate = re.sub(r"^#{1,6}\s*", "", line).strip()
        if len(candidate) < 3 or len(candidate) > 140:
            continue
        if any(re.match(pattern, line, flags=re.IGNORECASE) for pattern in heading_patterns):
            return candidate
        if candidate.endswith((".", ";", "!", "?")):
            continue
        if candidate.count(" ") <= 14:
            return candidate

    return fallback_title
