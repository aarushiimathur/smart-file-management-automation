# Test Cases and Acceptance Checklist

## Purpose

This document separates automated unit/workflow tests from live integration evidence. A fake Drive adapter can test backend behavior without uploading real files; only a live test verifies the Google credentials and destination permissions.

## Automated test cases

| ID | Scenario | Expected result | Evidence/status |
|---|---|---|---|
| TC01 | Generic filename with report content | Content evidence selects Reports | Run automated suite |
| TC02 | Invoice phrases in content | Category is Invoices | Run automated suite |
| TC03 | Certificate phrases in content | Category is Certificates | Run automated suite |
| TC04 | Scanned PDF / no useful text layer | OCR attempts to extract text within configured page limit | Run automated suite with Tesseract available |
| TC05 | Weak/conflicting evidence | Needs Review rather than overconfident category | Run automated suite |
| TC06 | Unsupported extension | Rejected and recorded as invalid | Run automated suite |
| TC07 | Empty file | Rejected and recorded as invalid | Run automated suite |
| TC08 | Identical file bytes submitted twice | Second attempt rejected as duplicate | Run automated suite |
| TC09 | Simulated Drive outage | Error event; no false successful archive record | Run automated suite |
| TC10 | File exceeds configured size limit | Rejected and recorded as invalid | Run automated suite |

Run from the project root:

```powershell
python -m unittest discover -s tests -v
```

Only mark each row “Passed” after running the current test suite and reviewing its output. Tests with a fake Drive adapter do not prove live Google API access.

## Manual integration acceptance

| ID | Check | Expected result |
|---|---|---|
| IT01 | Launch Streamlit | Dashboard loads without a Python exception |
| IT02 | First Drive operation | OAuth browser flow opens if no valid token exists |
| IT03 | Sign in with an authorized Google account | OAuth completes and a local token is saved |
| IT04 | Upload a valid sample PDF | Dashboard reports success and displays a Drive link |
| IT05 | Open the Drive link | File opens from the intended category folder |
| IT06 | Inspect the audit spreadsheet | Matching `SUCCESS` row is present |
| IT07 | Inspect the audit row | Filename/category/status and Drive details are populated as available |
| IT08 | Repeat the same file bytes | Duplicate is detected and does not create another archive object |
| IT09 | Trigger an invalid-file case | Failure is reported/logged rather than treated as archived |
| IT10 | Sheets access is unavailable (optional controlled test) | Local/Drive result behavior matches implementation; failure is logged/surfaced |
| IT11 | Drive access is unavailable (optional controlled test) | App does not falsely claim successful cloud archival |

## Confirmed manual evidence from the current session

The screenshots supplied during the project session show:
- The Streamlit dashboard reporting one successful Drive upload.
- The tested PDF visible in the `smart-file-manager/Reports` folder in Google Drive.
- The Google Sheets audit log containing an earlier `ERROR` entry and a later `SUCCESS` entry for the tested PDF.

This is evidence of a successful live Drive upload and a successful audit write for that sample. It is **not** evidence that every test case or failure scenario has passed.


