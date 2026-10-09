"""Knowledge documents: Markdown files with YAML front matter.

The Operation team owns these. Files under `knowledge/<persona>/` are the
version-controlled baseline; documents edited through the admin API are stored
in the database and override files with the same id.
"""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


@dataclass
class KnowledgeDoc:
    id: str
    persona: str  # driver | client | customer | all
    title: str
    body: str
    tags: list[str] = field(default_factory=list)
    # "internal" docs are never retrieved into chat (operator-only notes).
    visibility: str = "public"
    updated_at: str | None = None
    owner: str | None = None
    source: str = "file"


@dataclass
class Chunk:
    id: str
    doc_id: str
    persona: str
    title: str
    text: str
    doc_title: str = ""
    # Contextual Retrieval: a short LLM-written description of where this chunk sits in
    # its document, prepended for indexing only (the model still reads `text`).
    context: str = ""

    @property
    def index_text(self) -> str:
        return f"{self.context}\n{self.title}\n{self.text}" if self.context else f"{self.title}\n{self.text}"

    @property
    def content_hash(self) -> str:
        """Changes whenever anything stored in the index changes."""
        raw = f"{self.persona}\n{self.doc_title}\n{self.context}\n{self.title}\n{self.text}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


def parse_markdown(raw: str, *, default_id: str, default_persona: str, source: str = "file") -> KnowledgeDoc:
    m = _FRONT_MATTER_RE.match(raw)
    meta, body = ({}, raw) if not m else (yaml.safe_load(m.group(1)) or {}, m.group(2))
    title = meta.get("title") or _first_heading(body) or default_id
    return KnowledgeDoc(
        id=str(meta.get("id", default_id)),
        persona=str(meta.get("persona", default_persona)),
        title=str(title),
        body=body.strip(),
        tags=[str(t) for t in meta.get("tags", [])],
        visibility=str(meta.get("visibility", "public")),
        updated_at=str(meta["updated_at"]) if meta.get("updated_at") else None,
        owner=meta.get("owner"),
        source=source,
    )


def load_directory(root: Path) -> list[KnowledgeDoc]:
    docs = []
    for path in sorted(root.glob("*/*.md")):
        persona = path.parent.name
        docs.append(
            parse_markdown(path.read_text(encoding="utf-8"), default_id=path.stem, default_persona=persona)
        )
    return docs


def chunk_doc(doc: KnowledgeDoc, max_chars: int = 900) -> list[Chunk]:
    """Split on `##` sections, then pack paragraphs up to `max_chars`."""
    sections = re.split(r"\n(?=##\s)", doc.body)
    chunks: list[Chunk] = []
    for section in sections:
        heading = ""
        if section.startswith("##"):
            heading, _, section = section.partition("\n")
            heading = heading.lstrip("#").strip()
        title = f"{doc.title} — {heading}" if heading else doc.title
        buf = ""
        for para in re.split(r"\n\s*\n", section.strip()):
            if buf and len(buf) + len(para) > max_chars:
                chunks.append(_mk(doc, title, buf, len(chunks)))
                buf = ""
            buf = f"{buf}\n\n{para}" if buf else para
        if buf.strip():
            chunks.append(_mk(doc, title, buf, len(chunks)))
    return chunks


def _mk(doc: KnowledgeDoc, title: str, text: str, idx: int) -> Chunk:
    return Chunk(
        id=f"{doc.id}#{idx}", doc_id=doc.id, persona=doc.persona, title=title, text=text.strip(), doc_title=doc.title
    )


def _first_heading(body: str) -> str | None:
    m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    return m.group(1).strip() if m else None
