# Exception Handling and Recovery Guide

## Purpose
Exception handling prevents invalid inputs or failed integrations from being reported as successful archival. The scenarios below follow the documented project design; verify exact status names and outcomes against the current code and test output before claiming every case has passed.

## 1. Unsupported file type
**Example:** A user selects a file extension that the application does not support.  
**Expected response:** Reject it before archival, show a clear message, record an invalid-file event where supported, and do not create a successful archive record. Demonstrate using a harmless test file or automated test.

## 2. Empty or invalid PDF
A zero-byte file or a file named `.pdf` that is not a valid PDF may fail processing. Reject it where validation is implemented, record the invalid upload, and do not report successful archival.

## 3. File exceeds the size limit
The supplied configuration uses `MAX_FILE_SIZE_MB=10`. An oversized file should be rejected before expensive processing/upload where possible, with a clear message and an audit event where supported.

## 4. Duplicate document
The backend computes a SHA-256 hash from the file bytes and checks SQLite for a matching hash. If one exists, reject the repeat upload and record the duplicate event where supported. SHA-256 detects identical bytes; two semantically identical documents saved with different bytes may not be detected.

## 5. Text extraction failure or poor OCR
Scanned, blurry, rotated, damaged, or protected files may not yield useful text. PyMuPDF extracts selectable PDF text; Tesseract OCR is the fallback when a PDF has too little extractable text and is also used for supported images. OCR is not guaranteed to be perfect. Weak or conflicting evidence should go to `Needs Review` where supported.

## 6. Ambiguous classification
Weighted phrases associated with Invoices, Reports, and Certificates contribute to category scores. Filename clues have lower weight than content. If evidence is weak or conflicting, the system should assign `Needs Review` rather than guess. The displayed confidence is a heuristic, not a calibrated probability or trained ML prediction.

## 7. Google Drive OAuth/upload failure
**Possible causes:** Incorrect OAuth client JSON path, wrong root folder ID, Drive API disabled, sign-in account lacks folder access, account not listed as a test user while the consent screen is in Testing mode, or an API/authorization error.  
**Expected response:** Record/show an error and do not claim successful archival if the Drive upload failed. Do not silently substitute a local project folder as the archive.  
**Troubleshooting:** Check `GOOGLE_OAUTH_CLIENT_JSON`, `GOOGLE_DRIVE_ROOT_FOLDER_ID`, Drive API enablement, the signed-in account's folder permissions, and the OAuth consent-screen test-user list.

## 8. Google Sheets audit-write failure
**Possible causes:** Wrong spreadsheet ID, missing/unreadable service-account JSON, Sheets API disabled, or spreadsheet not shared with the service account as Editor.  
**Expected response:** Preserve a successful Drive upload and SQLite record if they already succeeded; log or surface the Sheets sync problem where supported. A missing Sheets row alone does not prove that Drive upload failed.  
**Troubleshooting:** Check `GOOGLE_SHEETS_SPREADSHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, service-account Editor access, and Sheets API enablement.

## 9. SQLite/database failure after Drive upload
If Drive accepts the file but SQLite cannot save its metadata, the documented design attempts a compensating deletion of the uploaded Drive file. If cleanup fails, manual reconciliation may be required. Check the application log and any recorded Drive file ID; do not assume rollback succeeded.

## 10. Unexpected application error
Show a concise user-facing error and record diagnostic details in the application log where supported. Never expose OAuth tokens, private keys, or credential JSON contents in the UI or logs.

## Status interpretation
| Status | Meaning |
|---|---|
| `SUCCESS` | Operation was recorded as successful; verify the Drive link for live evidence. |
| `ERROR` | Processing or integration operation failed; inspect the message field. |
| `INVALID_FILE` | The file failed validation. |
| `DUPLICATE` | File bytes matched an existing SHA-256 hash. |
| `STORAGE_NOT_CONFIGURED` | Storage configuration was missing, if emitted by the current version. |

## Short viva answer
“Exception handling makes sure the application does not report a document as archived when a required step has failed. We validate files, reject duplicates using SHA-256, handle extraction and OCR limitations, and distinguish Drive upload failures from Sheets audit failures. SQLite provides a local record, while Sheets provides an external audit trail. If a database write fails after a Drive upload, the documented design attempts compensating cleanup.”

## Demo checklist
- [ ] Show a successful upload and its Drive link.
- [ ] Show the matching `SUCCESS` row in Google Sheets.
- [ ] Demonstrate duplicate or invalid-file handling, or show its test output.
- [ ] Explain `Needs Review` for ambiguous classification.
- [ ] Explain OAuth for Drive and service-account authentication for Sheets.
- [ ] Do not claim a scenario passed unless test output or live behavior confirms it.

## Security reminders
- Never commit `.env`, OAuth client secrets, OAuth token files, or service-account keys.
- Revoke/rotate credentials if they are exposed publicly.
- Use non-sensitive sample documents for demonstrations.
- Confirm the Drive file ID and database state before manually deleting anything.
