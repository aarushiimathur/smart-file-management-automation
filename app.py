import json
import os
import sqlite3
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# Load this project's .env BEFORE importing automation.py, because automation.py
# reads configuration values when it is imported.
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)

from automation import (  # noqa: E402
    LOG_PATH,
    MAX_FILE_SIZE_MB,
    ALLOWED_EXTENSIONS,
    CATEGORIES,
    analyze_document,
    process_document,
    load_documents,
    load_events,
    init_db,
)

st.set_page_config(page_title="Smart File Archival", page_icon="📁", layout="wide")
init_db()
st.title("Smart File Management & Archival Automation")
st.caption("Content-based classification, OCR, Google Drive archival, and Google Sheets audit tracking.")


def google_configuration_status() -> tuple[bool, bool, str]:
    """Check required settings and credentials-file presence without exposing secrets."""
    drive_folder_id = os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID", "").strip()
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()
    service_value = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip().strip('"').strip("'")
    oauth_value = os.getenv("GOOGLE_OAUTH_CLIENT_JSON", "").strip().strip('"').strip("'")

    def resolved_file(value: str) -> bool:
        if not value:
            return False
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = BASE_DIR / path
        return path.is_file()

    service_exists = resolved_file(service_value)
    oauth_exists = resolved_file(oauth_value)
    drive_ready = bool(drive_folder_id and oauth_exists)
    sheets_ready = bool(spreadsheet_id and service_exists)

    if not drive_folder_id:
        detail = "GOOGLE_DRIVE_ROOT_FOLDER_ID is missing from the project's .env file."
    elif not oauth_value:
        detail = "GOOGLE_OAUTH_CLIENT_JSON is missing. Add the path to oauth_client.json in .env."
    elif not oauth_exists:
        detail = "The OAuth client JSON path is set, but the file could not be found. Check the path in .env."
    elif not service_exists:
        detail = "Drive OAuth is configured, but the service-account JSON needed for Sheets could not be found."
    elif not spreadsheet_id:
        detail = "Drive configuration is present. GOOGLE_SHEETS_SPREADSHEET_ID is missing, so Sheets auditing is not configured."
    else:
        detail = "Drive OAuth and Sheets service-account settings were found. Google will verify access during use."

    return drive_ready, sheets_ready, detail


drive_ready, sheets_ready, config_detail = google_configuration_status()

with st.sidebar:
    st.header("System status")
    st.write(f"**File limit:** {MAX_FILE_SIZE_MB} MB")
    st.write(f"**Allowed types:** {', '.join(sorted(ALLOWED_EXTENSIONS))}")
    st.write("**Google Drive:** " + ("Configured" if drive_ready else "Not configured"))
    st.write("**Google Sheets:** " + ("Configured" if sheets_ready else "Not configured"))
    st.caption("Drive uploads use your Google account (OAuth). Google Sheets audit logging uses the service account.")

if not drive_ready:
    st.warning(f"Google Drive is not ready. {config_detail}")
else:
    st.info("Google configuration was found. Live Drive/Sheets access is verified when an operation calls the APIs.")

try:
    documents = load_documents()
    events = load_events()
except sqlite3.Error as exc:
    st.error(f"Could not read the local database: {exc}")
    st.stop()

success_count = sum(1 for doc in documents if doc["status"] == "SUCCESS")
error_count = sum(1 for event in events if event["status"] in {"INVALID_FILE", "DUPLICATE", "ERROR", "STORAGE_NOT_CONFIGURED"})
col1, col2, col3, col4 = st.columns(4)
col1.metric("Archived documents", len(documents))
col2.metric("Successful Drive uploads", success_count)
col3.metric("Exceptions recorded", error_count)
col4.metric("Categories used", len(set(doc["category"] for doc in documents)) if documents else 0)

upload_tab, archive_tab, audit_tab, help_tab = st.tabs(["Upload & classify", "Drive archive", "Audit log", "How it works"])

with upload_tab:
    st.subheader("Upload and classify a document")
    uploaded = st.file_uploader("Choose a document", type=[ext.lstrip(".") for ext in sorted(ALLOWED_EXTENSIONS)], help=f"Maximum size: {MAX_FILE_SIZE_MB} MB")
    if uploaded is not None:
        file_bytes = uploaded.getvalue()
        st.write(f"**Selected:** {uploaded.name} · {uploaded.size:,} bytes")
        if st.button("Analyze document contents", key="analyze"):
            with st.spinner("Extracting text and checking for classification evidence..."):
                preview = analyze_document(uploaded.name, file_bytes)
            st.session_state["analysis_preview"] = {
                "name": uploaded.name,
                "category": preview["category"],
                "confidence": preview["confidence"],
                "method": preview["method"],
                "evidence": preview["evidence"],
                "warnings": preview["warnings"],
                "text_characters": preview["text_characters"],
            }
        preview = st.session_state.get("analysis_preview")
        if preview and preview.get("name") == uploaded.name:
            a, b, c = st.columns(3)
            a.metric("Suggested category", preview["category"])
            b.metric("Heuristic confidence", f"{preview['confidence']:.0%}")
            c.metric("Text characters extracted", f"{preview['text_characters']:,}")
            st.caption(f"Extraction method: {preview['method']}")
            if preview["evidence"]:
                st.write("**Evidence phrases:** " + ", ".join(preview["evidence"]))
            if preview["warnings"]:
                for warning in preview["warnings"]:
                    st.warning(warning)
            if preview["category"] == "Needs Review":
                st.info("The evidence is weak or conflicting. The file will be placed in the Needs Review folder instead of guessing.")
        if st.button("Process and upload to Google Drive", type="primary", disabled=not drive_ready):
            with st.spinner("Validating, classifying, uploading, and recording audit events..."):
                result = process_document(uploaded.name, file_bytes)
            if result["ok"]:
                st.success(f"Uploaded successfully to Google Drive under **{result['category']}**.")
                if result.get("drive_web_view_link"):
                    st.markdown(f"[Open document in Google Drive]({result['drive_web_view_link']})")
                st.caption(f"Classification: {result.get('method')} · confidence heuristic: {result.get('confidence', 0):.0%}")
            elif result["status"] == "DUPLICATE":
                st.warning(result["message"])
            else:
                st.error(result["message"])
            st.caption(f"Status: {result['status']} · SHA-256: {result.get('sha256') or 'not calculated'}")
            if result.get("sheets_synced"):
                st.info("Audit record sent to Google Sheets.")
            else:
                st.warning(f"Google Sheets sync: {result.get('sheets_message', 'not available')}")
            st.rerun()
    st.info("Demo: upload a text-based PDF without a category in its filename, then upload the same file again to demonstrate duplicate handling.")

with archive_tab:
    st.subheader("Search archived documents")
    search_text = st.text_input("Search by file name", placeholder="e.g. document123")
    category_filter = st.selectbox("Filter by category", ["All"] + CATEGORIES)
    filtered = documents
    if search_text:
        filtered = [d for d in filtered if search_text.lower() in d["original_name"].lower()]
    if category_filter != "All":
        filtered = [d for d in filtered if d["category"] == category_filter]
    if filtered:
        rows = []
        for d in filtered:
            rows.append({
                "ID": d["id"], "File name": d["original_name"], "Category": d["category"],
                "Size (bytes)": d["size_bytes"], "Confidence": f"{d['confidence']:.0%}",
                "Method": d["classification_method"], "Status": d["status"], "Uploaded": d["uploaded_at"],
                "Google Drive": d["drive_web_view_link"] or d["archived_path"],
            })
        st.dataframe(rows, use_container_width=True, hide_index=True, column_config={"Google Drive": st.column_config.LinkColumn("Google Drive", display_text="Open document")})
    else:
        st.write("No documents match the current filters.")

with audit_tab:
    st.subheader("Recent processing and exception events")
    if events:
        st.dataframe(
            [{"Timestamp": e["timestamp"], "File": e["file_name"], "Category": e["category"], "Status": e["status"], "Message": e["message"], "Drive file ID": e.get("drive_file_id", "")} for e in events],
            use_container_width=True, hide_index=True,
        )
    else:
        st.write("No audit events yet. Process a document to create the first event.")
    st.caption(f"Application logs: `{LOG_PATH}`")

with help_tab:
    st.subheader("End-to-end workflow")
    st.markdown("""
    1. **Upload:** Streamlit receives a document.
    2. **Validate:** Python checks file type, empty content, and size.
    3. **Extract:** PyMuPDF extracts PDF text; scanned PDFs use Tesseract OCR when available.
    4. **Classify:** Weighted phrase evidence identifies Invoices, Reports, or Certificates. Weak evidence becomes Needs Review.
    5. **Deduplicate:** SHA-256 is checked against prior SQLite records.
    6. **Archive:** Google Drive stores the actual file in a category folder.
    7. **Persist:** SQLite stores metadata, Drive file ID, link, confidence heuristic, and extraction method.
    8. **Audit:** Google Sheets receives successful and failed processing events when configured.
    9. **Recover:** API or database failures are logged; an upload is not silently replaced with a local copy.

    **Exception 1:** unsupported extension or zero-byte file.

    **Exception 2:** upload identical file contents twice; the second attempt is rejected as a duplicate.

    **OCR note:** Tesseract must be installed separately on the computer running the app. OCR quality depends on scan clarity and language configuration.
    """)
