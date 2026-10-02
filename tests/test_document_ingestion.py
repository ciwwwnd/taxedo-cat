import unittest
from taxedo.ingestion.documents import convert_document


class DocumentIngestionTests(unittest.TestCase):
    def test_unsupported_input_is_rejected(self):
        with self.assertRaises(ValueError):
            convert_document(b"ignored", "archive.zip", lambda _: "")

    def test_empty_content_is_rejected(self):
        with self.assertRaises(ValueError):
            convert_document(b"", "receipt.txt", lambda _: "")

    def test_mixed_pdf_keeps_text_and_scanned_page(self):
        import pymupdf

        with pymupdf.open() as doc:
            doc.new_page().insert_text((50, 50), "Invoice 123, Monitor, total EUR 249")
            doc.new_page()
            data = doc.tobytes()
        calls = []

        def ocr(data):
            calls.append(data)
            return "Scanned invoice: keyboard EUR 50"

        text = convert_document(data, "invoice.pdf", ocr)
        self.assertIn("Monitor", text)
        self.assertIn("keyboard", text)
        self.assertEqual(len(calls), 1)

    def test_logo_keeps_digital_text_layer_but_page_sized_scan_is_ocred(self):
        from io import BytesIO
        import pymupdf
        from PIL import Image
        image = BytesIO()
        Image.new("RGB", (20, 20), "white").save(image, format="PNG")
        for name, image_rect, expected, ocr_calls in (
            ("logo", pymupdf.Rect(72, 72, 112, 112), "Invoice 2026-0042", 0),
            ("scan", pymupdf.Rect(0, 160, 595, 842), "keyboard EUR 50", 1),
        ):
            with self.subTest(name), pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((72, 150), "Invoice 2026-0042, Monitor, total EUR 249.00")
                page.insert_image(image_rect, stream=image.getvalue())
                data = doc.tobytes()
                calls = []

                def ocr(content):
                    calls.append(content)
                    return "Scanned receipt: keyboard EUR 50"

                self.assertIn(expected, convert_document(data, "invoice.pdf", ocr))
                self.assertEqual(len(calls), ocr_calls)

    def test_windows_1252_csv_is_converted(self):
        data = "Empfänger,Betrag\nBürobedarf Müller,49.90\n".encode("cp1252")
        self.assertIn("Bürobedarf Müller", convert_document(data, "statement.csv", lambda _: ""))

    def test_unreadable_pdf_page_rejects_partial_receipt(self):
        import pymupdf

        with pymupdf.open() as doc:
            doc.new_page()
            data = doc.tobytes()
        with self.assertRaises(ValueError):
            convert_document(data, "invoice.pdf", lambda _: "")

    def test_image_files_use_ocr(self):
        from io import BytesIO
        from PIL import Image
        data = BytesIO()
        Image.new("RGB", (20, 20), "white").save(data, format="PNG")
        calls = []
        def ocr(content):
            calls.append(content)
            return "Receipt total 10.44 EUR"
        for extension in ("png", "jpg", "webp", "tiff"):
            self.assertIn("10.44", convert_document(data.getvalue(), f"receipt.{extension}", ocr))
        self.assertEqual(len(calls), 4)

    def test_large_photo_is_resized_for_ocr(self):
        from io import BytesIO
        from unittest.mock import patch
        from PIL import Image
        from taxedo.ingestion.documents import ocr_image
        from taxedo.security import OCR_IMAGE_PIXELS, OCR_TIMEOUT
        data = BytesIO()
        with Image.new("RGB", (6000, 4000), "white") as image:
            image.save(data, format="JPEG")
        def recognize(image, **kwargs):
            self.assertLessEqual(image.width * image.height, OCR_IMAGE_PIXELS)
            self.assertAlmostEqual(image.width / image.height, 1.5, places=2)
            self.assertEqual(kwargs["timeout"], OCR_TIMEOUT)
            return "Total 10.44 EUR"
        with patch("pytesseract.image_to_string", side_effect=recognize):
            self.assertEqual(ocr_image(data.getvalue()), "Total 10.44 EUR")

    def test_pdf_page_limit_is_enforced(self):
        import pymupdf
        from taxedo.security import MAX_PDF_PAGES
        with pymupdf.open() as doc:
            for _ in range(MAX_PDF_PAGES + 1):
                doc.new_page()
            data = doc.tobytes()
        with self.assertRaisesRegex(ValueError, "page limit"):
            convert_document(data, "invoice.pdf", lambda _: "")

    def test_alternative_layout_recovers_details_from_sparse_ocr(self):
        from io import BytesIO
        from unittest.mock import patch
        from PIL import Image
        from taxedo.ingestion.documents import ocr_image
        data = BytesIO()
        Image.new("RGB", (20, 20), "white").save(data, format="PNG")
        with patch("pytesseract.image_to_string", side_effect=["9A\nA", "Cornflakes 1,59\nTotal 10,44 EUR"]) as ocr:
            text = ocr_image(data.getvalue())
        self.assertIn("Cornflakes 1,59", text)
        self.assertIn("Total 10,44 EUR", text)
        self.assertIn("SAME image", text)
        self.assertEqual([call.kwargs["config"] for call in ocr.call_args_list], ["--psm 3", "--psm 6"])

    def test_one_ocr_timeout_preserves_other_reading(self):
        from io import BytesIO
        from unittest.mock import patch
        from PIL import Image
        from taxedo.ingestion.documents import ocr_image
        data = BytesIO()
        Image.new("RGB", (20, 20), "white").save(data, format="PNG")
        with patch("pytesseract.image_to_string", side_effect=[RuntimeError("timeout"), "Total 10.44 EUR"]):
            self.assertEqual(ocr_image(data.getvalue()), "Total 10.44 EUR")
