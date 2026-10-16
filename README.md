# Smart File Management and Archival Automation System

A Python + Streamlit automation application that extracts document content, classifies documents using weighted phrase evidence, applies OCR when needed, detects duplicate content, archives documents in Google Drive, stores searchable metadata in SQLite, and mirrors audit events to Google Sheets.

## What the system does

1. Accepts supported documents through a Streamlit web dashboard.
2. Validates file type, non-empty content, and the configured size limit.
3. Computes a SHA-256 content hash to identify duplicates even if the filename changes.
4. Extracts text from PDFs and supported document formats.
5. Uses Tesseract OCR when a scanned PDF has too little extractable text, and for supported image uploads.
6. Scores content phrases to suggest a category: `Invoices`, `Reports`, `Certificates`, `Other`, or `Needs Review`.
7. Uploads the original file to a category folder in Google Drive.
8. Stores metadata, Drive ID/link, extraction method, status, and audit events in SQLite.
9. Writes audit events to Google Sheets when the Sheets integration is available.
10. Exposes archive search/filtering, status metrics, and audit history in the dashboard.

The displayed confidence is a transparent heuristic, **not** a calibrated probability and not a trained machine-learning model. Weak or conflicting evidence should be reviewed rather than treated as certain.

## Technology stack

| Technology | Responsibility |
|---|---|
| Python | Workflow orchestration, validation, hashing, classification, logging, and exceptions |
| Streamlit | Browser-based dashboard, upload flow, classification preview, archive search, and audit display |
| PyMuPDF | PDF text extraction and page rendering for OCR |
| pytesseract + Tesseract OCR + Pillow | OCR for scanned PDFs and supported images |
| Google Drive API | Stores original documents in category folders |
| Google Sheets API | External audit trail |
| SQLite | Local searchable metadata and audit-event database |
| `google-auth-oauthlib` | User OAuth flow for Drive uploads |
| Google service-account credentials | Server-to-server access for Sheets audit logging |

## Important authentication design

**Drive and Sheets intentionally use different credentials:**

- **Google Drive uploads use your Google account through OAuth.** This avoids the storage-quota limitation encountered when trying to upload files as a service account.
- **Google Sheets audit logging uses the service account.** Share the audit spreadsheet with the service account's `client_email` and give it Editor access.
- The Drive root folder must be accessible to the Google account that completes OAuth sign-in.
- Never commit `oauth_client.json`, `token_drive.json`, `service_account.json`, or `.env` to Git. These files can grant access to your Google account or cloud resources.

## Requirements

- Python 3.10 or newer
- Google Cloud project with **Google Drive API** and **Google Sheets API** enabled
- A Google Drive root folder for archived documents
- A Google spreadsheet for audit events
- Desktop OAuth client JSON for Drive
- Service-account JSON key for Sheets
- Tesseract OCR installed on the machine if OCR features are needed

## Install and run on Windows PowerShell

Open PowerShell in the project folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
streamlit run app.py
```

Streamlit normally opens at `http://localhost:8501`.

If PowerShell blocks activation, use the Python executable in the virtual environment directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

## Install Tesseract OCR

On Windows, install a trusted Windows build of Tesseract OCR. Ensure `tesseract.exe` is on your `PATH`, or configure its installed path in `automation.py` if the project code supports that setting. Open a new terminal after installation.

On macOS, a common installation command is `brew install tesseract`. On Debian/Ubuntu, use `sudo apt install tesseract-ocr`.

OCR can be slower than ordinary text extraction and accuracy depends on scan quality, orientation, contrast, and language.

## Google Cloud setup

### A. Enable APIs

In Google Cloud Console, select the project that contains your OAuth client and credentials, then enable:
- Google Drive API
- Google Sheets API

### B. Create/configure Drive OAuth (your Google account)

1. Open Google Auth Platform / OAuth consent screen for the same Google Cloud project as the Desktop OAuth client.
2. If the app is in **Testing** mode, add the Google account you will use for sign-in under **Test users**.
3. Create an OAuth client of type **Desktop app**.
4. Download its JSON file and save it in the project folder as `oauth_client.json` (or another safe path you configure below).
5. The first time the app needs Drive access, a browser window opens. Sign in with the Google account that owns or can edit the Drive root folder and approve the requested Drive access.
6. The OAuth flow saves a local token file (by default `token_drive.json`) so you do not need to sign in on every run. Keep the token private.

The consent-screen display name is cosmetic. If it shows a different app name, verify that the OAuth client belongs to the intended Cloud project and that your Google account is listed as a test user. You do not need a new project solely to change the display name or add a tester.

### C. Configure the service account for Sheets

1. Create a service account and download its JSON key.
2. Store the JSON key somewhere private in the project folder, for example `service_account.json`, or use a protected credentials folder.
3. Share the **audit spreadsheet** with the service account's `client_email` and grant Editor access.
4. Copy the spreadsheet ID from the spreadsheet URL. It is the value between `/d/` and `/edit`.
5. The service account is used for Sheets auditing; do not rely on it for storing the uploaded documents in Drive.

### D. Configure the Drive folder

1. Create a root folder in your personal Google Drive, for example `smart-file-manager`.
2. Copy its folder ID from the URL after `/folders/`.
3. Sign in through the app with the Google account that can edit this folder. The app creates/uses category subfolders underneath the root folder.

## Environment configuration

Create a `.env` file in the project directory. Use your own IDs and local paths; never paste credentials into a public repository.

```dotenv
GOOGLE_DRIVE_ROOT_FOLDER_ID=your_drive_root_folder_id
GOOGLE_OAUTH_CLIENT_JSON=C:\path\to\smart_file_archival\oauth_client.json
GOOGLE_OAUTH_TOKEN_JSON=token_drive.json
GOOGLE_SHEETS_SPREADSHEET_ID=your_audit_spreadsheet_id
GOOGLE_SERVICE_ACCOUNT_JSON=C:\path\to\smart_file_archival\service_account.json
MAX_FILE_SIZE_MB=10
OCR_MIN_TEXT_CHARS=50
OCR_MAX_PAGES=3
```

Use absolute paths for the two credential JSON files if that is easiest on Windows. If your `.env` parser treats backslashes unexpectedly, use forward slashes in Windows paths, for example `C:/Users/YourUser/.../oauth_client.json`.

The application should load `.env` from the project folder before importing configuration-dependent code. Do not share a screenshot of `.env` if it exposes IDs or paths you want to keep private, and never share the contents of a credentials JSON or OAuth token.

## Run and verify the live integrations

1. Start the app with `streamlit run app.py`.
2. Upload a supported document and analyze its contents.
3. Process the document and complete the Google OAuth browser flow if prompted.
4. Confirm that the dashboard reports a successful upload and provides a Drive link.
5. Open the Drive link and verify the file is in the expected category folder.
6. Open the audit spreadsheet and verify a matching `SUCCESS` row with filename, category, Drive file ID/link, and related audit fields.
7. Check that a failed attempt is recorded as an error rather than falsely reported as archived.

**Evidence captured during this project run:** the dashboard showed one successful Drive upload, the PDF was visible in the `smart-file-manager/Reports` Drive folder, and the Google Sheets audit sheet contained an earlier `ERROR` row followed by a `SUCCESS` row for the tested PDF. These are manual integration observations from the supplied screenshots, not a claim that every possible scenario has been tested.

## Classification flow

- PDFs with a text layer are read with PyMuPDF.
- If a PDF has too little extractable text, the app can render up to the configured OCR page limit and run Tesseract.
- Supported TXT/CSV, DOCX, XLSX, and image formats use format-specific extraction/OCR.
- Weighted phrases provide evidence for Invoices, Reports, and Certificates. Filename clues have lower weight than document contents.
- Uncertain or conflicting evidence is assigned to `Needs Review`.

## Error handling and auditability

The system is designed to handle unsupported file types, empty files, oversized files, duplicate content, Drive/API failures, and database persistence failures. A Drive upload failure should not be displayed as a successful archive. If metadata persistence fails after a cloud upload, the backend attempts compensating cleanup when implemented by the current automation module. Google Sheets is an external audit mirror; a Sheets failure should not erase a successful Drive upload and SQLite record.

Verify these behaviors against the current implementation and tests before claiming any untested scenario as passed.

## Run automated tests

```powershell
python -m unittest discover -s tests -v
```

The test suite described in `docs/test_cases.md` uses a fake Drive adapter for core workflow tests, so it can exercise the local automation logic without live Google credentials. Unit tests do not replace a manual live integration check of Google Drive and Google Sheets.


## Project structure

```text
smart_file_archival/
├── app.py
├── automation.py
├── requirements.txt
├── .env                 # private
├── oauth_client.json    # private
├── token_drive.json     # private
├── service_account.json # private
├── README.md
├── Project_Report.pdf
├── architecture.md
├── demo_script.md
├── test_cases.md
└── tests/
    └── test_automation.py
```

The exact files present may differ slightly depending on your local project layout.

## Security, limitations, and future work

- This is an academic prototype, not a production document-management service.
- Keep OAuth client secrets, token files, service-account keys, spreadsheet IDs, and folder IDs private where appropriate.
- If a private key or token is exposed, revoke/rotate it and generate replacement credentials.
- The current classifier is rules-based; it is not a trained AI classifier.
- OCR can make mistakes, especially with poor-quality scans.
- Production deployment should add user authentication, role-based access, malware scanning, retention rules, stronger secrets management, backups, and monitoring.
- Future improvements could include multilingual OCR, feedback-driven classification, approval workflows, notification support, and more extensive integration tests.
