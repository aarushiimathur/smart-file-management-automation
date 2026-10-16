from __future__ import annotations

import hashlib
import io
import json
from contextlib import contextmanager
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
DB_PATH = BASE_DIR / "documents.db"
LOG_PATH = LOG_DIR / "app.log"
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "10"))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".csv", ".xlsx", ".png", ".jpg", ".jpeg"}
CATEGORIES = ["Invoices", "Reports", "Certificates", "Other", "Needs Review"]
OCR_MIN_TEXT_CHARS = int(os.getenv("OCR_MIN_TEXT_CHARS", "50"))
OCR_MAX_PAGES = int(os.getenv("OCR_MAX_PAGES", "3"))

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


class DriveStorage(Protocol):
    def upload_file(self, filename: str, data: bytes, category: str, mime_type: str) -> dict[str, str]: ...
    def delete_file(self, file_id: str) -> None: ...


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_connection(db_path: Path = DB_PATH):
    """Commit successful operations and always close the SQLite connection."""
    conn = connect_db(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    """Create tables and migrate older local databases without deleting records."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with db_connection(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                category TEXT NOT NULL,
                sha256 TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL,
                archived_path TEXT NOT NULL,
                status TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                drive_file_id TEXT,
                drive_web_view_link TEXT,
                confidence REAL NOT NULL DEFAULT 0,
                classification_method TEXT NOT NULL DEFAULT 'unknown',
                evidence TEXT NOT NULL DEFAULT '[]'
            )"""
        )
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
        migrations = {
            "drive_file_id": "ALTER TABLE documents ADD COLUMN drive_file_id TEXT",
            "drive_web_view_link": "ALTER TABLE documents ADD COLUMN drive_web_view_link TEXT",
            "confidence": "ALTER TABLE documents ADD COLUMN confidence REAL NOT NULL DEFAULT 0",
            "classification_method": "ALTER TABLE documents ADD COLUMN classification_method TEXT NOT NULL DEFAULT 'unknown'",
            "evidence": "ALTER TABLE documents ADD COLUMN evidence TEXT NOT NULL DEFAULT '[]'",
        }
        for column, sql in migrations.items():
            if column not in existing:
                conn.execute(sql)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                file_name TEXT NOT NULL,
                category TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                sha256 TEXT,
                drive_file_id TEXT
            )"""
        )
        audit_cols = {row["name"] for row in conn.execute("PRAGMA table_info(audit_events)").fetchall()}
        if "drive_file_id" not in audit_cols:
            conn.execute("ALTER TABLE audit_events ADD COLUMN drive_file_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_name ON documents(original_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events(timestamp)")


def safe_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    return name[:160] or "uploaded_file"


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Multiple evidence phrases and weights make this more reliable than filename-only rules.
CATEGORY_PHRASES: dict[str, dict[str, int]] = {
    "Invoices": {
        "invoice number": 5, "invoice no": 5, "amount due": 4, "bill to": 3,
        "tax invoice": 5, "gstin": 3, "payment due": 3, "subtotal": 2,
        "purchase order": 3, "quantity": 1, "unit price": 2, "total amount": 2,
        "receipt number": 4, "payment received": 3,
    },
    "Reports": {
        "executive summary": 5, "methodology": 3, "findings": 3, "conclusion": 2,
        "quarterly performance": 4, "annual performance": 4, "key findings": 4,
        "recommendations": 2, "results and discussion": 4, "introduction": 1,
        "data analysis": 3, "objectives": 1, "background": 1,
    },
    "Certificates": {
        "this is to certify": 6, "certificate number": 5, "hereby certifies": 5,
        "has successfully completed": 4, "is awarded to": 4, "date of issue": 2,
        "certified that": 4, "certificate of completion": 5, "qualification awarded": 3,
    },
}


def _extract_pdf_text(data: bytes) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    try:
        import fitz  # PyMuPDF
        with fitz.open(stream=data, filetype="pdf") as pdf:
            pages = list(pdf)
            extracted = "\n".join(page.get_text("text") for page in pages).strip()
            if len(extracted) >= OCR_MIN_TEXT_CHARS:
                return extracted, "PDF text extraction", warnings
            # Scanned PDFs often have almost no text layer. OCR only the first few pages.
            try:
                import pytesseract
                from PIL import Image
                ocr_parts: list[str] = []
                for page in pages[:OCR_MAX_PAGES]:
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    image = Image.open(io.BytesIO(pix.tobytes("png")))
                    page_text = pytesseract.image_to_string(image).strip()
                    if page_text:
                        ocr_parts.append(page_text)
                ocr_text = "\n".join(ocr_parts).strip()
                if ocr_text:
                    return ocr_text, "OCR (Tesseract)", warnings
                warnings.append("No readable text was found by PDF extraction or OCR.")
                return extracted, "PDF extraction + OCR", warnings
            except Exception as exc:
                warnings.append(f"OCR was unavailable or failed: {type(exc).__name__}.")
                return extracted, "PDF text extraction (OCR unavailable)", warnings
    except Exception as exc:
        warnings.append(f"PDF could not be read: {type(exc).__name__}.")
        return "", "PDF extraction failed", warnings


def extract_document_text(filename: str, data: bytes) -> tuple[str, str, list[str]]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf_text(data)
    if suffix in {".txt", ".csv"}:
        try:
            return data.decode("utf-8", errors="replace"), "Text extraction", []
        except Exception as exc:
            return "", "Text extraction failed", [f"Could not read text: {type(exc).__name__}."]
    if suffix == ".docx":
        try:
            from docx import Document
            doc = Document(io.BytesIO(data))
            text = "\n".join(p.text for p in doc.paragraphs)
            for table in doc.tables:
                text += "\n" + "\n".join(" ".join(cell.text for cell in row.cells) for row in table.rows)
            return text, "DOCX text extraction", []
        except Exception as exc:
            return "", "DOCX extraction failed", [f"Could not read DOCX: {type(exc).__name__}."]
    if suffix == ".xlsx":
        try:
            from openpyxl import load_workbook
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            parts = []
            for sheet in workbook.worksheets:
                parts.append(sheet.title)
                for row in sheet.iter_rows(values_only=True):
                    parts.append(" ".join(str(value) for value in row if value is not None))
            workbook.close()
            return "\n".join(parts), "Spreadsheet text extraction", []
        except Exception as exc:
            return "", "Spreadsheet extraction failed", [f"Could not read spreadsheet: {type(exc).__name__}."]
    if suffix in {".png", ".jpg", ".jpeg"}:
        try:
            import pytesseract
            from PIL import Image
            image = Image.open(io.BytesIO(data))
            return pytesseract.image_to_string(image), "OCR (Tesseract)", []
        except Exception as exc:
            return "", "Image OCR failed", [f"OCR was unavailable or failed: {type(exc).__name__}."]
    return "", "No content extractor", ["No content extractor is configured for this file type."]


def classify_document(filename: str, text: str = "") -> dict[str, Any]:
    """Classify by content evidence first, using the filename only as a weak clue."""
    normalized_text = re.sub(r"\s+", " ", text.lower())
    compact_text = re.sub(r"[^a-z0-9]", "", normalized_text)
    normalized_name = Path(safe_filename(filename)).stem.lower().replace("_", " ").replace("-", " ")
    compact_name = re.sub(r"[^a-z0-9]", "", normalized_name)
    scores = {category: 0 for category in CATEGORY_PHRASES}
    evidence: dict[str, list[str]] = {category: [] for category in CATEGORY_PHRASES}
    for category, phrases in CATEGORY_PHRASES.items():
        for phrase, weight in phrases.items():
            compact_phrase = re.sub(r"[^a-z0-9]", "", phrase)
            if phrase in normalized_text or compact_phrase in compact_text:
                scores[category] += weight
                evidence[category].append(phrase)
            # Filename is intentionally weak: content should dominate.
            if phrase in normalized_name or compact_phrase in compact_name:
                scores[category] += 1
                evidence[category].append(f"filename:{phrase}")
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_category, best_score = ordered[0]
    second_score = ordered[1][1]
    total_content_chars = len(normalized_text.strip())
    # Confidence is a transparent heuristic, not a calibrated ML probability.
    if best_score < 4 or (best_score - second_score) < 2 or total_content_chars < 15:
        category = "Needs Review" if total_content_chars >= 15 or best_score > 0 else "Other"
        confidence = 0.0 if category == "Other" else min(0.49, best_score / 12)
    else:
        category = best_category
        confidence = min(0.98, 0.55 + best_score / 30 + (best_score - second_score) / 50)
    return {
        "category": category,
        "confidence": round(confidence, 2),
        "scores": scores,
        "evidence": evidence.get(category, []),
        "all_evidence": evidence,
    }


def _google_credentials(scopes: list[str]):
    credentials_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not credentials_path:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not configured.")
    path = Path(credentials_path)
    if not path.is_file():
        raise RuntimeError(f"Service-account JSON file not found: {path}")
    from google.oauth2.service_account import Credentials
    return Credentials.from_service_account_file(str(path), scopes=scopes)


class GoogleDriveStorage:
    """Uploads files to the user's Google Drive using Desktop OAuth credentials."""
    def __init__(self):
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow

        root_folder_id = os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID", "").strip()
        if not root_folder_id:
            raise RuntimeError("Set GOOGLE_DRIVE_ROOT_FOLDER_ID to the Drive folder where files should be archived.")

        oauth_value = os.getenv("GOOGLE_OAUTH_CLIENT_JSON", "").strip().strip('"').strip("'")
        if not oauth_value:
            raise RuntimeError("Set GOOGLE_OAUTH_CLIENT_JSON to the downloaded Desktop OAuth client JSON file.")
        oauth_path = Path(oauth_value).expanduser()
        if not oauth_path.is_absolute():
            oauth_path = BASE_DIR / oauth_path
        if not oauth_path.is_file():
            raise RuntimeError(f"OAuth client JSON file not found: {oauth_path}")

        token_value = os.getenv("GOOGLE_OAUTH_TOKEN_JSON", "token_drive.json").strip().strip('"').strip("'")
        token_path = Path(token_value).expanduser()
        if not token_path.is_absolute():
            token_path = BASE_DIR / token_path

        scopes = ["https://www.googleapis.com/auth/drive"]
        credentials = None
        if token_path.is_file():
            try:
                credentials = Credentials.from_authorized_user_file(str(token_path), scopes=scopes)
            except Exception:
                credentials = None

        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(oauth_path), scopes=scopes)
                credentials = flow.run_local_server(port=0, prompt="consent")
            token_path.write_text(credentials.to_json(), encoding="utf-8")

        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self.root_folder_id = root_folder_id
        self._folder_ids: dict[str, str] = {}
        self._media_upload_class = MediaIoBaseUpload

    def _category_folder_id(self, category: str) -> str:
        if category in self._folder_ids:
            return self._folder_ids[category]
        safe_category = category.replace("'", "\\'")
        query = (
            f"name='{safe_category}' and mimeType='application/vnd.google-apps.folder' "
            f"and '{self.root_folder_id}' in parents and trashed=false"
        )
        results = self.service.files().list(q=query, fields="files(id,name)", pageSize=100).execute()
        files = results.get("files", [])
        if files:
            folder_id = files[0]["id"]
        else:
            folder = self.service.files().create(
                body={"name": category, "mimeType": "application/vnd.google-apps.folder", "parents": [self.root_folder_id]},
                fields="id",
            ).execute()
            folder_id = folder["id"]
        self._folder_ids[category] = folder_id
        return folder_id

    def upload_file(self, filename: str, data: bytes, category: str, mime_type: str) -> dict[str, str]:
        folder_id = self._category_folder_id(category)
        media = self._media_upload_class(io.BytesIO(data), mimetype=mime_type, resumable=False)
        created = self.service.files().create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id,name,webViewLink",
        ).execute()
        return {
            "file_id": created["id"],
            "web_view_link": created.get("webViewLink", f"https://drive.google.com/file/d/{created['id']}/view"),
            "name": created.get("name", filename),
        }

    def delete_file(self, file_id: str) -> None:
        self.service.files().delete(fileId=file_id).execute()


def get_drive_storage() -> tuple[DriveStorage | None, str | None]:
    if not os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID", "").strip():
        return None, "Google Drive is not configured. Set GOOGLE_DRIVE_ROOT_FOLDER_ID."
    if not os.getenv("GOOGLE_OAUTH_CLIENT_JSON", "").strip():
        return None, "Google Drive OAuth is not configured. Set GOOGLE_OAUTH_CLIENT_JSON to your Desktop OAuth client JSON file."
    try:
        return GoogleDriveStorage(), None
    except Exception as exc:
        return None, f"Google Drive connection failed: {type(exc).__name__}: {exc}"


def _google_sheets_client():
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()
    credentials_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not spreadsheet_id or not credentials_path:
        return None, "Google Sheets is not configured; local audit logging is active."
    if not Path(credentials_path).is_file():
        return None, f"Service-account JSON file not found: {credentials_path}"
    try:
        import gspread
        credentials = _google_credentials([
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ])
        client = gspread.authorize(credentials)
        spreadsheet = client.open_by_key(spreadsheet_id)
        try:
            worksheet = spreadsheet.worksheet("Audit Log")
        except Exception:
            worksheet = spreadsheet.add_worksheet(title="Audit Log", rows=1000, cols=9)
            worksheet.append_row(["Timestamp", "File name", "Category", "Status", "Message", "SHA-256", "Drive file ID", "Drive link", "Source"])
        return worksheet, None
    except Exception as exc:
        return None, f"Google Sheets connection failed: {type(exc).__name__}: {exc}"


def append_google_sheet_event(event: dict[str, Any]) -> tuple[bool, str]:
    worksheet, error = _google_sheets_client()
    if worksheet is None:
        return False, error or "Google Sheets unavailable."
    try:
        worksheet.append_row([
            event["timestamp"], event["file_name"], event["category"], event["status"],
            event["message"], event.get("sha256", ""), event.get("drive_file_id", ""),
            event.get("drive_web_view_link", ""), "Smart File Archival App",
        ], value_input_option="USER_ENTERED")
        return True, "Audit event sent to Google Sheets."
    except Exception as exc:
        return False, f"Google Sheets write failed: {type(exc).__name__}: {exc}"


def record_event(
    file_name: str, category: str, status: str, message: str, sha256: str = "",
    db_path: Path = DB_PATH, sync_google_sheets: bool = True,
    drive_file_id: str = "", drive_web_view_link: str = "",
) -> dict[str, Any]:
    event = {
        "timestamp": utc_now(), "file_name": safe_filename(file_name), "category": category,
        "status": status, "message": message, "sha256": sha256,
        "drive_file_id": drive_file_id, "drive_web_view_link": drive_web_view_link,
    }
    with db_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO audit_events(timestamp,file_name,category,status,message,sha256,drive_file_id) VALUES(?,?,?,?,?,?,?)",
            (event["timestamp"], event["file_name"], event["category"], event["status"], event["message"], event["sha256"], drive_file_id),
        )
    logging.info("%s | %s | %s | %s", status, event["file_name"], category, message)
    if sync_google_sheets:
        ok, sheet_message = append_google_sheet_event(event)
        event["sheets_synced"] = ok
        event["sheets_message"] = sheet_message
        if ok:
            logging.info("Google Sheets sync succeeded for %s", event["file_name"])
        else:
            logging.warning("Google Sheets sync unavailable for %s: %s", event["file_name"], sheet_message)
    else:
        event["sheets_synced"] = False
        event["sheets_message"] = "Google Sheets sync skipped for this operation."
    return event


def _mime_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".txt": "text/plain", ".csv": "text/csv", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(suffix, "application/octet-stream")


def analyze_document(filename: str, data: bytes) -> dict[str, Any]:
    text, method, warnings = extract_document_text(filename, data)
    result = classify_document(filename, text)
    return {**result, "method": method, "warnings": warnings, "text_characters": len(text), "extracted_text": text}


def process_document(
    filename: str, data: bytes, archive_dir: Path | None = None, db_path: Path = DB_PATH,
    max_bytes: int = MAX_FILE_SIZE_BYTES, sync_google_sheets: bool = True,
    drive_storage: DriveStorage | None = None,
) -> dict[str, Any]:
    """Validate, content-classify, deduplicate, upload to Drive, and audit one file."""
    original_name = safe_filename(filename)
    suffix = Path(original_name).suffix.lower()
    category = "Needs Review"
    analysis: dict[str, Any] = {"confidence": 0.0, "method": "not analyzed", "evidence": [], "warnings": []}

    def fail(status: str, message: str, digest: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        event = record_event(original_name, category, status, message, digest, db_path, sync_google_sheets)
        return {"ok": False, "status": status, "message": message, "category": category,
                "file_name": original_name, **analysis, **event, **(extra or {})}

    if not suffix or suffix not in ALLOWED_EXTENSIONS:
        return fail("INVALID_FILE", f"Unsupported file type '{suffix or '(no extension)'}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}.")
    if not data:
        return fail("INVALID_FILE", "The uploaded file is empty (0 bytes).")
    if suffix == ".pdf" and not data.lstrip().startswith(b"%PDF-"):
        return fail("INVALID_FILE", "The file has a .pdf extension but does not appear to contain a valid PDF header.")
    if len(data) > max_bytes:
        return fail("INVALID_FILE", f"File exceeds the {max_bytes // (1024 * 1024)} MB size limit.")

    digest = file_sha256(data)
    with db_connection(db_path) as conn:
        duplicate = conn.execute("SELECT id, drive_web_view_link FROM documents WHERE sha256 = ?", (digest,)).fetchone()
    if duplicate:
        return fail("DUPLICATE", f"An identical document is already archived (record #{duplicate['id']}). No second copy was uploaded.", digest,
                    {"existing_drive_link": duplicate["drive_web_view_link"]})

    analysis = analyze_document(original_name, data)
    category = analysis["category"]
    storage = drive_storage
    if storage is None:
        storage, drive_error = get_drive_storage()
        if storage is None:
            return fail("STORAGE_NOT_CONFIGURED", drive_error or "Google Drive is unavailable; no local copy was created.", digest)

    drive_file: dict[str, str] | None = None
    try:
        # The file is stored in Drive, not in the application directory.
        drive_file = storage.upload_file(original_name, data, category, _mime_type(original_name))
        uploaded_at = utc_now()
        try:
            with db_connection(db_path) as conn:
                conn.execute(
                    """INSERT INTO documents(original_name,stored_name,category,sha256,size_bytes,archived_path,status,uploaded_at,
                       drive_file_id,drive_web_view_link,confidence,classification_method,evidence)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (original_name, drive_file.get("name", original_name), category, digest, len(data),
                     drive_file.get("web_view_link", ""), "SUCCESS", uploaded_at, drive_file["file_id"],
                     drive_file.get("web_view_link", ""), analysis["confidence"], analysis["method"], json.dumps(analysis["evidence"])),
                )
        except Exception:
            # Compensating action: remove the cloud object if metadata could not be committed.
            try:
                storage.delete_file(drive_file["file_id"])
            except Exception:
                logging.exception("CRITICAL: Drive file %s could not be cleaned up after database failure", drive_file.get("file_id"))
            raise
        message = f"Uploaded to Google Drive / {category}."
        event = record_event(original_name, category, "SUCCESS", message, digest, db_path, sync_google_sheets,
                             drive_file["file_id"], drive_file.get("web_view_link", ""))
        return {"ok": True, "status": "SUCCESS", "message": message, "category": category,
                "file_name": original_name, "stored_name": drive_file.get("name", original_name),
                "archived_path": drive_file.get("web_view_link", ""), "drive_file_id": drive_file["file_id"],
                "drive_web_view_link": drive_file.get("web_view_link", ""), "sha256": digest, **analysis, **event}
    except sqlite3.IntegrityError:
        if drive_file:
            try:
                storage.delete_file(drive_file["file_id"])
            except Exception:
                logging.exception("Could not remove Drive file after duplicate race")
        return fail("DUPLICATE", "An identical document was processed concurrently. The extra Drive copy was removed when possible.", digest)
    except Exception as exc:
        logging.exception("Unexpected processing failure for %s", original_name)
        return fail("ERROR", f"The document could not be archived ({type(exc).__name__}). Check the application log.", digest)


def load_documents(db_path: Path = DB_PATH):
    with db_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT id, original_name, category, size_bytes, status, uploaded_at, archived_path, sha256,
               drive_file_id, drive_web_view_link, confidence, classification_method, evidence
               FROM documents ORDER BY id DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def load_events(db_path: Path = DB_PATH, limit: int = 100):
    with db_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT timestamp, file_name, category, status, message, sha256, drive_file_id FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]
