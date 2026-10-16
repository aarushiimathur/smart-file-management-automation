import io
import tempfile
import unittest
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

from automation import init_db, process_document, load_documents, load_events, analyze_document


class FakeDrive:
    def __init__(self):
        self.files = {}
        self.next_id = 1

    def upload_file(self, filename, data, category, mime_type):
        file_id = f"fake-drive-{self.next_id}"
        self.next_id += 1
        self.files[file_id] = {"filename": filename, "data": data, "category": category}
        return {"file_id": file_id, "web_view_link": f"https://drive.google.com/file/d/{file_id}/view", "name": filename}

    def delete_file(self, file_id):
        self.files.pop(file_id, None)


def make_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def make_scanned_pdf(text: str) -> bytes:
    image = Image.new("RGB", (1400, 700), "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 80), text, fill="black")
    image_bytes = io.BytesIO()
    image.save(image_bytes, format="PNG")
    doc = fitz.open()
    page = doc.new_page(width=700, height=350)
    page.insert_image(page.rect, stream=image_bytes.getvalue())
    data = doc.tobytes()
    doc.close()
    return data


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "test.db"
        self.drive = FakeDrive()
        init_db(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def process(self, name, data):
        return process_document(name, data, db_path=self.db, max_bytes=1024 * 1024,
                                sync_google_sheets=False, drive_storage=self.drive)

    def test_content_classification_without_filename_clue(self):
        data = make_pdf("Executive Summary Methodology Findings Conclusion Quarterly Performance")
        preview = analyze_document("document123.pdf", data)
        self.assertEqual(preview["category"], "Reports")
        self.assertIn("executive summary", preview["evidence"])
        result = self.process("document123.pdf", data)
        self.assertTrue(result["ok"])
        self.assertEqual(result["category"], "Reports")
        self.assertEqual(len(self.drive.files), 1)
        self.assertEqual(len(load_documents(self.db)), 1)

    def test_invoice_content_classification(self):
        data = make_pdf("Tax Invoice Invoice Number Amount Due Bill To GSTIN Payment Due")
        self.assertEqual(analyze_document("file_a.pdf", data)["category"], "Invoices")

    def test_certificate_content_classification(self):
        data = make_pdf("This is to certify that the candidate has successfully completed the course")
        self.assertEqual(analyze_document("file_b.pdf", data)["category"], "Certificates")

    def test_scanned_pdf_uses_ocr_when_available(self):
        data = make_scanned_pdf("This is to certify that the candidate has successfully completed the course")
        preview = analyze_document("scan_009.pdf", data)
        self.assertIn("OCR", preview["method"])
        self.assertEqual(preview["category"], "Certificates")

    def test_ambiguous_content_needs_review(self):
        data = make_pdf("General document content with some unrelated notes and details")
        self.assertEqual(analyze_document("random.pdf", data)["category"], "Needs Review")

    def test_invalid_extension_is_rejected(self):
        result = self.process("bad.exe", b"not allowed")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "INVALID_FILE")
        self.assertEqual(len(load_documents(self.db)), 0)
        self.assertEqual(len(load_events(self.db)), 1)

    def test_empty_file_is_rejected(self):
        result = self.process("empty.pdf", b"")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "INVALID_FILE")

    def test_duplicate_content_is_not_uploaded_twice(self):
        data = make_pdf("Invoice Number 555 Amount Due GSTIN")
        first = self.process("first.pdf", data)
        second = self.process("renamed.pdf", data)
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["status"], "DUPLICATE")
        self.assertEqual(len(load_documents(self.db)), 1)
        self.assertEqual(len(self.drive.files), 1)
        self.assertEqual(len(load_events(self.db)), 2)

    def test_drive_failure_is_handled_without_database_record(self):
        class BrokenDrive:
            def upload_file(self, *args, **kwargs):
                raise ConnectionError("simulated outage")
            def delete_file(self, file_id):
                pass
        result = process_document("document.pdf", make_pdf("Executive Summary Methodology Findings Conclusion"),
                                  db_path=self.db, sync_google_sheets=False, drive_storage=BrokenDrive())
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(len(load_documents(self.db)), 0)
        self.assertEqual(len(load_events(self.db)), 1)

    def test_size_limit(self):
        result = self.process("large.pdf", b"x" * (1024 * 1024 + 1))
        self.assertEqual(result["status"], "INVALID_FILE")


if __name__ == "__main__":
    unittest.main()
