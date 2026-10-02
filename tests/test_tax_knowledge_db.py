from __future__ import annotations

import io
import tempfile
import sqlite3
import unittest
import zipfile
from pathlib import Path

import numpy as np

from taxedo.tax.knowledge import (
    DEFAULT_SECTIONS,
    DEFAULT_DB_PATH,
    _taxonomy_chunks,
    TaxKnowledgeDB,
    build_database,
    parse_estg_xml,
)


def _fake_embedder(texts: list[str]) -> np.ndarray:
    vectors = []
    for text in texts:
        lowered = text.lower()
        vectors.append(
            [
                float("monitor" in lowered or "arbeitsmittel" in lowered),
                float(
                    "cleaning" in lowered
                    or "haushaltsnahe dienstleistungen (extern)" in lowered
                ),
                float("groceries" in lowered or "lebenshaltungskosten" in lowered),
                0.1,
            ]
        )
    return np.asarray(vectors, dtype=np.float32)


def _phone_biased_embedder(texts: list[str]) -> np.ndarray:
    vectors = []
    for text in texts:
        lowered = text.lower()
        if "can i deduct a work monitor" in lowered:
            vectors.append([1.0, 0.0])
        elif "telefon / internet" in lowered:
            vectors.append([1.0, 0.0])
        elif "arbeitsmittel" in lowered or "monitor" in lowered:
            vectors.append([0.98, 0.2])
        else:
            vectors.append([0.0, 1.0])
    return np.asarray(vectors, dtype=np.float32)


def _flat_embedder(texts: list[str]) -> np.ndarray:
    return np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float32)


def _sample_xml() -> bytes:
    norms = [
        """
        <norm doknr="root">
          <metadaten>
            <standangabe><standkommentar>permanent test version</standkommentar></standangabe>
          </metadaten>
        </norm>
        """
    ]
    for section in DEFAULT_SECTIONS:
        slug = section.replace("§", "").strip()
        norms.append(
            f"""
        <norm doknr="doc-{slug}">
          <metadaten><enbez>{section}</enbez><titel>Test {slug}</titel></metadaten>
          <textdaten><text><Content>
            <P>Legal deduction text for section {section}.</P>
          </Content></text></textdaten>
        </norm>
        """
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<dokumente builddate="20260804215509">' + "".join(norms) + "</dokumente>"
    ).encode("utf-8")


def _sample_archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("estg.xml", _sample_xml())
    return buffer.getvalue()


class TaxKnowledgeDBTests(unittest.TestCase):
    def test_bundled_category_references_match_current_taxonomy(self):
        with sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True) as database:
            stored = database.execute(
                "SELECT id, text, search_terms FROM documents "
                "WHERE document_type='category_reference' ORDER BY id"
            ).fetchall()
        expected = sorted(
            (chunk["id"], chunk["text"], chunk["search_terms"])
            for chunk in _taxonomy_chunks()
        )
        self.assertEqual(stored, expected)

    def test_parser_extracts_only_configured_sections(self):
        parsed = parse_estg_xml(_sample_xml())
        self.assertEqual(
            {chunk["section"] for chunk in parsed.chunks}, set(DEFAULT_SECTIONS)
        )

    def test_database_search_uses_category_examples(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            db_path = Path(temporary_dir) / "tax.db"
            build_database(_sample_archive(), db_path=db_path, embedder=_fake_embedder)
            database = TaxKnowledgeDB(db_path, embedder=_fake_embedder)

            monitor_results = database.search("Work monitor for working at home")
            cleaning_results = database.search("professional cleaning service")

            self.assertTrue(db_path.is_file())
            self.assertEqual(database.metadata()["embedding_dimension"], "4")
            self.assertEqual(monitor_results[0]["section"], "§ 9")
            self.assertEqual(monitor_results[0]["document_type"], "category_reference")
            self.assertEqual(cleaning_results[0]["section"], "§ 35a")

    def test_lexical_ranking_breaks_semantic_ties(self):
        # Equal vectors isolate lexical ranking; this is not a semantic-quality test.
        with tempfile.TemporaryDirectory() as temporary_dir:
            db_path = Path(temporary_dir) / "tax.db"
            build_database(_sample_archive(), db_path=db_path, embedder=_flat_embedder)
            database = TaxKnowledgeDB(db_path, embedder=_flat_embedder)

            results = database.search(
                "Professional training course fees"
            )

            self.assertEqual(
                results[0]["id"], "category-werbungskosten-fortbildungskosten"
            )

    def test_multilingual_alias_contributes_to_hybrid_ranking(self):
        # Equal vectors isolate the multilingual alias contribution.
        with tempfile.TemporaryDirectory() as temporary_dir:
            db_path = Path(temporary_dir) / "tax.db"
            build_database(_sample_archive(), db_path=db_path, embedder=_flat_embedder)
            database = TaxKnowledgeDB(db_path, embedder=_flat_embedder)

            results = database.search("Private Haushaltsgeräte")

            self.assertEqual(
                results[0]["id"],
                "category-nicht-abzugsfähig-lebenshaltungskosten",
            )
            self.assertGreater(results[0]["alias_score"], 0)

    def test_lexical_evidence_overrides_misleading_semantic_match(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            db_path = Path(temporary_dir) / "tax.db"
            build_database(
                _sample_archive(),
                db_path=db_path,
                embedder=_phone_biased_embedder,
            )
            database = TaxKnowledgeDB(db_path, embedder=_phone_biased_embedder)

            results = database.search("Can I deduct a work monitor?", limit=100)

            closest_embedding = max(results, key=lambda result: result["semantic_score"])
            self.assertEqual(
                closest_embedding["id"], "category-werbungskosten-telefon-internet"
            )
            self.assertEqual(results[0]["id"], "category-werbungskosten-arbeitsmittel")

    def test_existing_database_is_not_replaced_implicitly(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            db_path = Path(temporary_dir) / "tax.db"
            build_database(_sample_archive(), db_path=db_path, embedder=_fake_embedder)
            with self.assertRaises(FileExistsError):
                build_database(
                    _sample_archive(), db_path=db_path, embedder=_fake_embedder
                )


if __name__ == "__main__":
    unittest.main()
