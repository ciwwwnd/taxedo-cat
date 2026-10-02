from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

import numpy as np

from taxedo.tax.categories import TAX_CATEGORIES
from taxedo.paths import KNOWLEDGE_DB, MODEL_CACHE_DIR as DEFAULT_MODEL_CACHE_DIR


SOURCE_URL = "https://www.gesetze-im-internet.de/estg/xml.zip"
DEFAULT_DB_PATH = KNOWLEDGE_DB
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
RAG_VERSION = "taxedo-rag materiality-gate-v7.3"
MODEL_CACHE_DIR = Path(os.getenv("EMBEDDING_CACHE_DIR", str(DEFAULT_MODEL_CACHE_DIR)))
DEFAULT_SECTIONS = (
    "§ 7",
    "§ 9",
    "§ 9a",
    "§ 10",
    "§ 10b",
    "§ 12",
    "§ 20",
    "§ 21",
    "§ 33",
    "§ 35a",
)

CATEGORY_SECTIONS = {
    "Werbungskosten": "§ 9",
    "Sonderausgaben": "§ 10",
    "Außergewöhnliche Belastungen": "§ 33",
    "Haushaltsnahe Dienstleistungen": "§ 35a",
    "Vermietung und Verpachtung": "§ 21",
    "Kapitalerträge": "§ 20",
    "Nicht abzugsfähig": "§ 12",
}

Embedder = Callable[[list[str]], np.ndarray]
_embedding_model = None

HYBRID_LEXICAL_WEIGHT = 0.60
CATEGORY_REFERENCE_BOOST = 0.10
CATEGORY_ALIAS_WEIGHT = 0.20
_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_SEARCH_STOPWORDS = {
    "a",
    "absetzen",
    "abziehbar",
    "am",
    "an",
    "and",
    "are",
    "as",
    "at",
    "auf",
    "can",
    "das",
    "deduct",
    "deductible",
    "dem",
    "den",
    "der",
    "des",
    "die",
    "do",
    "does",
    "ein",
    "eine",
    "einem",
    "einen",
    "einer",
    "for",
    "from",
    "für",
    "i",
    "ich",
    "im",
    "in",
    "is",
    "ist",
    "kann",
    "mein",
    "meine",
    "mit",
    "my",
    "of",
    "on",
    "oder",
    "sind",
    "steuer",
    "tax",
    "the",
    "this",
    "to",
    "und",
    "von",
    "with",
    "zu",
}


@dataclass(frozen=True)
class ParsedTaxSource:
    build_date: str
    status: str
    content_sha256: str
    chunks: list[dict]


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from fastembed import TextEmbedding

        _embedding_model = TextEmbedding(
            model_name=EMBEDDING_MODEL,
            cache_dir=str(MODEL_CACHE_DIR),
        )
    return _embedding_model


def embed_texts(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.empty((0, 0), dtype=np.float32)
    vectors = np.asarray(list(_get_embedding_model().embed(texts)), dtype=np.float32)
    return _normalize_vectors(vectors)


def _normalize_vectors(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim == 1:
        vectors = vectors.reshape(1, -1)
    if vectors.ndim != 2 or not vectors.size or not np.isfinite(vectors).all():
        raise ValueError("Embeddings must be a nonempty finite matrix")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if (norms == 0).any():
        raise ValueError("Embeddings must have nonzero norm")
    return vectors / norms


def warm_embedding_model() -> None:
    embed_texts(["German tax deduction knowledge"])


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _category_document_id(category_name: str, subcategory_name: str) -> str:
    slug_source = f"{category_name}-{subcategory_name}".lower()
    slug = re.sub(r"[^a-z0-9äöüß]+", "-", slug_source).strip("-")
    return f"category-{slug}"


CATEGORY_SEARCH_ALIASES = {
    _category_document_id(category_name, subcategory_name): tuple(
        str(alias) for alias in subcategory.get("retrieval_aliases", [])
    )
    for category_name, category in TAX_CATEGORIES.items()
    for subcategory_name, subcategory in category.get("subcategories", {}).items()
    if subcategory.get("retrieval_aliases")
}


def _search_tokens(value: str) -> set[str]:
    return {
        token
        for token in _TOKEN_PATTERN.findall(value.casefold())
        if len(token) > 1 and token not in _SEARCH_STOPWORDS
    }


def _token_matches(query_token: str, document_tokens: set[str]) -> bool:
    if query_token in document_tokens:
        return True
    if len(query_token) < 5:
        return False
    return any(
        document_token.startswith(query_token) or query_token.startswith(document_token)
        for document_token in document_tokens
        if len(document_token) >= 5
    )


def _lexical_scores(query: str, documents: list[dict]) -> list[float]:
    query_tokens = _search_tokens(query)
    if not query_tokens:
        return [0.0] * len(documents)

    document_tokens = [
        _search_tokens(
            f"{document['title']} {document['text']} {document['search_terms']} "
            f"{' '.join(CATEGORY_SEARCH_ALIASES.get(document['id'], ()))}"
        )
        for document in documents
    ]
    document_frequencies = {
        token: sum(_token_matches(token, tokens) for tokens in document_tokens)
        for token in query_tokens
    }
    document_count = len(documents)
    token_weights = {
        token: math.log((document_count + 1) / (frequency + 1)) + 1
        for token, frequency in document_frequencies.items()
    }
    maximum_score = sum(token_weights.values())
    return [
        sum(
            token_weights[token]
            for token in query_tokens
            if _token_matches(token, tokens)
        )
        / maximum_score
        for tokens in document_tokens
    ]


def _category_alias_scores(query: str, documents: list[dict]) -> list[float]:
    query_tokens = _search_tokens(query)
    scores = []
    for document in documents:
        aliases = CATEGORY_SEARCH_ALIASES.get(document["id"], ())
        coverage = []
        for alias in aliases:
            alias_tokens = _search_tokens(alias)
            if not alias_tokens:
                continue
            matches = sum(
                _token_matches(alias_token, query_tokens)
                for alias_token in alias_tokens
            )
            coverage.append(matches / len(alias_tokens))
        scores.append(max(coverage, default=0.0))
    return scores


def _element_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return _normalize_space(" ".join(element.itertext()))


def _format_build_date(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return parsed.isoformat().replace("+00:00", "Z")
    except ValueError:
        return value


def _section_slug(section: str) -> str:
    slug = section.replace("§", "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", slug).strip("-")


def _word_chunks(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        if current and len(" ".join([*current, word])) > max_chars:
            chunks.append(" ".join(current))
            overlap: list[str] = []
            overlap_length = 0
            for previous_word in reversed(current):
                added = len(previous_word) + (1 if overlap else 0)
                if overlap and overlap_length + added > overlap_chars:
                    break
                overlap.insert(0, previous_word)
                overlap_length += added
            current = overlap
        current.append(word)

    final_chunk = " ".join(current)
    if final_chunk and (not chunks or final_chunk != chunks[-1]):
        chunks.append(final_chunk)
    return chunks


def _extract_main_text(norm: ET.Element) -> str:
    text_node = norm.find("./textdaten/text")
    if text_node is None:
        return ""
    paragraphs = [_element_text(node) for node in text_node.findall(".//P")]
    paragraphs = [paragraph for paragraph in paragraphs if paragraph]
    return "\n\n".join(paragraphs) if paragraphs else _element_text(text_node)


def _taxonomy_chunks() -> list[dict]:
    chunks: list[dict] = []
    for category_name, category in TAX_CATEGORIES.items():
        section = CATEGORY_SECTIONS.get(category_name)
        if not section:
            continue
        for subcategory_name, subcategory in category.get("subcategories", {}).items():
            examples = [str(example) for example in subcategory.get("examples", [])]
            aliases = [str(alias) for alias in subcategory.get("retrieval_aliases", [])]
            description = str(subcategory.get("description", ""))
            text = (
                f"Category: {category_name}. Subcategory: {subcategory_name}. "
                f"Rule: {description}. Examples: {', '.join(examples)}. "
                f"Search aliases: {', '.join(aliases)}."
            )
            chunks.append(
                {
                    "id": _category_document_id(category_name, subcategory_name),
                    "document_type": "category_reference",
                    "law": "EStG",
                    "section": section,
                    "title": f"{category_name} > {subcategory_name}",
                    "text": text,
                    "search_terms": " ".join(
                        [
                            category_name,
                            subcategory_name,
                            description,
                            *examples,
                            *aliases,
                        ]
                    ),
                    "source_url": (
                        "https://www.gesetze-im-internet.de/estg/"
                        f"__{_section_slug(section)}.html"
                    ),
                    "source_document_id": "taxedo/tax/categories.py",
                    "chunk_index": 1,
                }
            )
    return chunks


def parse_estg_xml(
    xml_bytes: bytes,
    *,
    sections: tuple[str, ...] = DEFAULT_SECTIONS,
    source_url: str = SOURCE_URL,
    max_chunk_chars: int = 1800,
    overlap_chars: int = 250,
) -> ParsedTaxSource:
    root = ET.fromstring(xml_bytes)
    build_date = _format_build_date(root.get("builddate", "unknown"))

    first_norm = root.find("norm")
    status_parts = []
    if first_norm is not None:
        status_parts = [
            _element_text(node)
            for node in first_norm.findall("./metadaten/standangabe/standkommentar")
        ]
    status = " ".join(part for part in status_parts if part)

    selected = set(sections)
    parsed_sections: list[dict] = []
    for norm in root.findall("norm"):
        metadata = norm.find("metadaten")
        if metadata is None:
            continue
        section = _normalize_space(metadata.findtext("enbez", ""))
        if section not in selected:
            continue
        text = _extract_main_text(norm)
        if not text:
            continue
        title = _normalize_space(metadata.findtext("titel", ""))
        parsed_sections.append(
            {
                "section": section,
                "title": title or f"Einkommensteuergesetz {section}",
                "document_id": norm.get("doknr", ""),
                "text": text,
            }
        )

    found_sections = {item["section"] for item in parsed_sections}
    missing = [section for section in sections if section not in found_sections]
    if missing:
        raise ValueError(
            f"Official XML did not contain expected sections: {', '.join(missing)}"
        )

    fingerprint = json.dumps(
        parsed_sections,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    content_sha256 = hashlib.sha256(fingerprint).hexdigest()

    chunks: list[dict] = []
    for parsed_section in parsed_sections:
        section = parsed_section["section"]
        slug = _section_slug(section)
        section_url = f"https://www.gesetze-im-internet.de/estg/__{slug}.html"
        section_chunks = _word_chunks(
            parsed_section["text"], max_chunk_chars, overlap_chars
        )
        for index, text in enumerate(section_chunks, start=1):
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            chunks.append(
                {
                    "id": f"estg-{slug}-{index:03d}-{text_hash[:10]}",
                    "document_type": "official_law",
                    "law": "EStG",
                    "section": section,
                    "title": parsed_section["title"],
                    "text": text,
                    "search_terms": f"{section} {parsed_section['title']}",
                    "source_url": section_url,
                    "source_document_id": parsed_section["document_id"],
                    "chunk_index": index,
                }
            )

    return ParsedTaxSource(build_date, status, content_sha256, chunks)


def _extract_xml(archive_bytes: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        xml_names = [
            name for name in archive.namelist() if name.lower().endswith(".xml")
        ]
        if len(xml_names) != 1:
            raise ValueError(f"Expected one XML document, found {len(xml_names)}")
        return archive.read(xml_names[0])


def download_archive(source_url: str = SOURCE_URL, timeout: int = 30) -> bytes:
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "taxedo-cat tax-knowledge-builder/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def build_database(
    archive_bytes: bytes,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    source_url: str = SOURCE_URL,
    overwrite: bool = False,
    embedder: Embedder = embed_texts,
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> ParsedTaxSource:
    if bool(valid_from) != bool(valid_to):
        raise ValueError("Supply both reviewed validity dates, or neither")
    if valid_from:
        datetime.strptime(valid_from, "%Y-%m-%d")
        datetime.strptime(valid_to, "%Y-%m-%d")
        if valid_from > valid_to:
            raise ValueError("Validity interval is reversed")
    if db_path.exists() and not overwrite:
        raise FileExistsError(
            f"{db_path} already exists. It is intentionally permanent; "
            "use --force to replace it."
        )

    parsed = parse_estg_xml(_extract_xml(archive_bytes), source_url=source_url)
    documents = [*parsed.chunks, *_taxonomy_chunks()]
    embedding_inputs = [
        f"{document['title']}\n{document['text']}\nConcepts: {document['search_terms']}"
        for document in documents
    ]
    embeddings = _normalize_vectors(embedder(embedding_inputs))
    if len(embeddings) != len(documents):
        raise ValueError("Embedding model returned the wrong number of vectors")
    embedding_dimension = int(embeddings.shape[1])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = db_path.with_suffix(db_path.suffix + ".tmp")
    if temporary_path.exists():
        temporary_path.unlink()

    connection = sqlite3.connect(temporary_path)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE documents (
                id TEXT PRIMARY KEY,
                document_type TEXT NOT NULL,
                law TEXT NOT NULL,
                section TEXT NOT NULL,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                search_terms TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_document_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                embedding BLOB NOT NULL
            );
        """
        )

        metadata = {
            "schema_version": "2",
            "source_url": source_url,
            "source_build_date": parsed.build_date,
            "source_status": parsed.status,
            "content_sha256": parsed.content_sha256,
            "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "chunk_count": str(len(documents)),
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dimension": str(embedding_dimension),
        }
        if valid_from:
            metadata.update(valid_from=valid_from, valid_to=valid_to)
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)", metadata.items()
        )

        for chunk, embedding in zip(documents, embeddings, strict=True):
            values = (
                chunk["id"],
                chunk["document_type"],
                chunk["law"],
                chunk["section"],
                chunk["title"],
                chunk["text"],
                chunk["search_terms"],
                chunk["source_url"],
                chunk["source_document_id"],
                chunk["chunk_index"],
                sqlite3.Binary(embedding.astype(np.float32).tobytes()),
            )
            connection.execute(
                """
                INSERT INTO documents(
                    id, document_type, law, section, title, text, search_terms,
                    source_url, source_document_id, chunk_index, embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()

    os.replace(temporary_path, db_path)
    return parsed


class TaxKnowledgeDB:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        embedder: Embedder = embed_texts,
    ):
        self.db_path = db_path
        self.embedder = embedder

    @property
    def available(self) -> bool:
        return self.db_path.exists()

    def search(self, query: str, limit: int = 4) -> list[dict]:
        if not self.available or not query.strip() or limit < 1:
            return []

        metadata = self.metadata()
        if metadata.get("embedding_model") != EMBEDDING_MODEL:
            raise RuntimeError(
                "Tax database embedding model does not match the application model"
            )
        dimension = int(metadata["embedding_dimension"])
        query_vector = _normalize_vectors(self.embedder([query]))[0]
        if query_vector.size != dimension:
            raise RuntimeError(
                f"Expected a {dimension}-dimensional query embedding, "
                f"got {query_vector.size}"
            )

        connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute("SELECT * FROM documents").fetchall()
        finally:
            connection.close()

        scored: list[dict] = []
        for row in rows:
            document = dict(row)
            vector = np.frombuffer(document.pop("embedding"), dtype=np.float32)
            if (
                vector.size != dimension
                or not np.isfinite(vector).all()
                or not np.any(vector)
            ):
                raise RuntimeError(f"Invalid embedding stored for {document['id']}")
            document["semantic_score"] = float(np.dot(query_vector, vector))
            scored.append(document)

        for document, lexical_score, alias_score in zip(
            scored,
            _lexical_scores(query, scored),
            _category_alias_scores(query, scored),
            strict=True,
        ):
            document["lexical_score"] = lexical_score
            document["alias_score"] = alias_score
            category_boost = (
                CATEGORY_REFERENCE_BOOST
                if document["document_type"] == "category_reference"
                else 0.0
            )
            document["score"] = (
                document["semantic_score"]
                + HYBRID_LEXICAL_WEIGHT * lexical_score
                + CATEGORY_ALIAS_WEIGHT * alias_score
                + category_boost
            )
        scored.sort(key=lambda document: document["score"], reverse=True)
        return scored[:limit]

    def metadata(self) -> dict[str, str]:
        if not self.available:
            return {}
        connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            metadata = dict(
                connection.execute("SELECT key, value FROM metadata").fetchall()
            )
            metadata["database_sha256"] = hashlib.sha256(
                self.db_path.read_bytes()
            ).hexdigest()
            return metadata
        finally:
            connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build or query the fixed tax knowledge database."
    )
    parser.add_argument("--version", action="version", version=RAG_VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--source-url", default=SOURCE_URL)
    build_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    build_parser.add_argument("--timeout", type=int, default=30)
    build_parser.add_argument("--force", action="store_true")
    build_parser.add_argument(
        "--valid-from",
        help="Start date of operator-reviewed corpus applicability (YYYY-MM-DD)",
    )
    build_parser.add_argument(
        "--valid-to",
        help="End date of operator-reviewed corpus applicability (YYYY-MM-DD)",
    )

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    search_parser.add_argument("--limit", type=int, default=4)

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            archive = download_archive(args.source_url, args.timeout)
            build_database(
                archive,
                db_path=args.db,
                source_url=args.source_url,
                overwrite=args.force,
                valid_from=args.valid_from,
                valid_to=args.valid_to,
            )
            document_count = TaxKnowledgeDB(args.db).metadata()["chunk_count"]
            print(
                f"Built permanent tax database: {args.db} ({document_count} documents)"
            )
        else:
            results = TaxKnowledgeDB(args.db).search(args.query, args.limit)
            print(json.dumps(results, ensure_ascii=False, indent=2))
    except Exception as error:
        print(f"Tax knowledge command failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
