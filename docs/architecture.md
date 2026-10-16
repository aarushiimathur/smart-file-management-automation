# Architecture and Workflow

## 1. System overview

The Smart File Management and Archival Automation System is a Python automation workflow exposed through a Streamlit dashboard. It separates document bytes, searchable metadata, and the external audit trail:

- **Google Drive** stores the original document.
- **SQLite** stores metadata, hashes, status, extraction method, Drive identifiers/links, and local audit events.
- **Google Sheets** mirrors audit events for external inspection.
- **Streamlit** provides upload, analysis preview, archive search, status metrics, and audit views.

## 2. Components

| Component | Responsibility |
|---|---|
| `app.py` / Streamlit | User interface, file upload, analysis preview, archive search, audit log, status metrics |
| `automation.py` | Validation, extraction, OCR fallback, weighted phrase scoring, SHA-256 duplicate checks, cloud upload, database operations, audit logging, exceptions |
| PyMuPDF | PDF text extraction and rendering pages for OCR |
| Tesseract OCR / pytesseract / Pillow | OCR for scanned PDFs and supported images |
| Google Drive API + user OAuth | Uploads the original document into a category folder in the user's Drive |
| SQLite | Persists searchable document metadata and audit events |
| Google Sheets API + service account | Appends/mirrors audit events to an external spreadsheet |

## 3. Authentication design

The project uses two authentication methods for different purposes.

1. **Drive: OAuth as the user.** The user signs in through the browser using a Desktop OAuth client. This is the identity that uploads documents into the user's Drive folder. The local OAuth token is sensitive and must not be committed.
2. **Sheets: service account.** A service account authenticates the spreadsheet audit integration. The target spreadsheet must be shared with the service account's email with Editor access.

A service account generally does not have personal Drive storage quota. Using the user's OAuth identity for file uploads avoids the storage-quota failure encountered during integration testing.

## 4. End-to-end workflow

1. The user selects a file in Streamlit.
2. The application checks supported extension, non-empty bytes, and configured size limit.
3. The backend computes a SHA-256 hash and checks SQLite for existing content.
4. Text is extracted with a format-specific extractor.
5. For a scanned PDF or PDF with too little text, pages within the configured limit are rendered and processed with Tesseract OCR.
6. Weighted phrase rules score category evidence. Filename clues receive less weight than content.
7. The classifier returns a category such as `Invoices`, `Reports`, `Certificates`, `Other`, or `Needs Review`.
8. The original document is uploaded to the appropriate Google Drive category folder.
9. The database stores metadata and Drive identifiers/link.
10. The system records an audit event in SQLite and attempts to mirror it to Google Sheets.
11. The UI shows the result and makes archived metadata searchable.

```text
User
  |
  v
Streamlit upload / analysis preview
  |
  v
Validation -> SHA-256 duplicate check
  |
  v
Text extraction -> OCR fallback if needed
  |
  v
Weighted phrase classification
  |
  v
Google Drive category folder (OAuth user)
  |
  v
SQLite metadata + local audit event
  |
  v
Google Sheets audit mirror (service account)
```

## 5. Classification

Weighted phrase rules look for document evidence, for example invoice identifiers and amounts due, report headings such as methodology/findings/conclusion, and certificate phrases such as “this is to certify”. Filename evidence is intentionally weaker. The displayed confidence is a heuristic score, not a calibrated probability or a trained machine-learning result. Weak or conflicting evidence should result in `Needs Review`.

## 6. Data and audit fields

The audit sheet observed in the live project includes columns for timestamp, file name, category, status, message, SHA-256, Drive file ID, Drive link, and source. SQLite is the primary local metadata/audit store; Sheets is an external mirror.

## 7. Failure behavior

- **Invalid/empty/oversized input:** reject the file and record an appropriate event.
- **Duplicate content:** reject a repeated SHA-256 hash before a second archive upload.
- **Drive/API failure:** record an error and do not claim successful archival.
- **Sheets failure:** preserve local metadata/audit state and report/log the sync failure where supported; a Sheets outage should not undo successful Drive storage.
- **Database failure after Drive upload:** the implementation may attempt a compensating Drive deletion. If cleanup fails, manual reconciliation may be needed.

The precise behavior must be confirmed by the implementation and automated tests before claiming each failure path has passed.

## 8. Configuration and deployment

Required environment settings include:

- `GOOGLE_DRIVE_ROOT_FOLDER_ID`
- `GOOGLE_OAUTH_CLIENT_JSON`
- `GOOGLE_OAUTH_TOKEN_JSON` (optional; defaults to `token_drive.json` if supported)
- `GOOGLE_SHEETS_SPREADSHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `MAX_FILE_SIZE_MB`
- `OCR_MIN_TEXT_CHARS`
- `OCR_MAX_PAGES`

The first Drive operation may open a browser for OAuth authorization. If the OAuth consent screen is in Testing mode, the account used for sign-in must be listed as a test user. Tesseract must be installed on the host if OCR is required.

## 9. Limitations and future enhancements

The prototype uses rules rather than a trained classifier; OCR accuracy depends on scan quality and language; authentication and role-based access are not a full production user-management system. Future work includes multilingual OCR, approval workflows, improved secrets management, stronger test coverage, notifications, backups, and monitoring.
