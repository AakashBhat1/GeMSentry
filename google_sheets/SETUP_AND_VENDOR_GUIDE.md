# GeMSentry Multi-Vendor Google Sheets & Tech Spec Guide

This guide explains how to connect your **3 separate vendor Google Sheets** and manage technical specifications via Google Docs without ever revealing confidential bid numbers, EMDs, or financial details to your vendors.

---

## 1. Confidential Vendor Separation Model

| Vendor | Category | Data Row Color | Header Color | What the Vendor Sees |
| :--- | :--- | :--- | :--- | :--- |
| **Rajiv Mittal** | Drone / UAV | **Soft Red** (`#FEE2E2`) | **Bold Red** (`#DC2626`) | **S.No, Item / Work Description, Tech Spec Link, Participate or Not Dropdown** |
| **Rajiv Tyagi** | Power Supply / Electrical | **Soft Yellow** (`#FEF08A`) | **Bold Amber** (`#D97706`) | **S.No, Item / Work Description, Tech Spec Link, Participate or Not Dropdown** |
| **Hanmars** | Biometrics & Face Rec | **Soft Blue** (`#BFDBFE`) | **Bold Blue** (`#1D4ED8`) | **S.No, Item / Work Description, Tech Spec Link, Participate or Not Dropdown** |

> [!IMPORTANT]
> **Strict Confidentiality & Zero Tender/Bid Disclosure Enforced:**
> - In all 3 vendor spreadsheets, **Bid Numbers, Tender IDs, EMD Amounts, Turnover Exemptions, and Commercial Values are completely omitted**.
> - The word **"Tender" or "Bid" is never mentioned** on the vendor sheets.
> - Vendors only receive:
>   1. `SL. NO`
>   2. `ITEM / WORK DESCRIPTION` (Title and scope of work)
>   3. `TECH SPEC SHEET (GOOGLE DOC)` (Clickable link to the technical specification document)
>   4. `PARTICIPATE OR NOT` (Dropdown: `YES` / `NO` for vendor response)
> - Your Master Sheet retains all 19 columns with full internal commercial details and vendor color coding.

---

## 2. Setting Up Your 3 Vendor Spreadsheets

1. In Google Drive, create 3 separate Google Sheets (or use existing ones):
   - Sheet 1: `ETSPL Requirements - Rajiv Mittal (Drone)`
   - Sheet 2: `ETSPL Requirements - Rajiv Tyagi (Power Supply)`
   - Sheet 3: `ETSPL Requirements - Hanmars (Biometrics)`
2. Copy the URL or ID of each sheet from your browser address bar:
   - Format: `https://docs.google.com/spreadsheets/d/`**`1WbeJJ8goLPGLryyJfcJNbiXtIxXjC9Z0g8viueh5oOk`**`/edit`
3. Enter these 3 IDs in the GeMSentry Dashboard:
   - Go to **GeMSentry Dashboard &rarr; Settings &rarr; Vendor Spreadsheets**
   - Paste each URL or ID in its corresponding box and click **Save Settings**.
   - *(Optional: You can also paste them directly into `VENDOR_CONFIG` at the top of `gemsentry/google_sync_script.gs`)*.

---

## 3. Deploying the Google Apps Script Web App

Only **ONE** deployment is required — on your **Master Google Sheet**:

1. Open your **Master Google Sheet** in your browser.
2. In the top menu, click **Extensions &rarr; Apps Script**.
3. Delete any default code in `Code.gs`.
4. Copy the entire contents of [`gemsentry/google_sync_script.gs`](file:///c:/dev/GEM/gemsentry/google_sync_script.gs) and paste it into the editor.
5. Click **Save (💾)**.
6. In the top right, click **Deploy &rarr; New deployment**.
7. Click the gear icon next to "Select type" and choose **Web app**:
   - **Description**: `GeMSentry Multi-Vendor Live Webhook`
   - **Execute as**: `Me (your email)`
   - **Who has access**: `Anyone` *(Important: required for GeMSentry webhooks to communicate)*
8. Click **Deploy**, click **Authorize access**, and copy the **Web app URL** (`https://script.google.com/macros/s/.../exec`).
9. Paste this URL into **GeMSentry Dashboard &rarr; Settings &rarr; Google Apps Script Webhook URL** and click **Save Settings**.

---

## 4. Tech Spec Sheet / Google Doc Review Workflow

1. **Shortlisting / Finalizing**:
   - When reviewing a tender on GeMSentry, click **⭐ Finalize**.
   - The assigned vendor is auto-detected (or choose manually).
   - If you don't have the spec sheet ready yet, leave it empty — it will show as *"Pending Technical Review"* on the vendor's sheet.
2. **Reviewing Later & Uploading Spec Sheet**:
   - When you have analyzed the tender requirements and created your spec sheet (Google Doc, PDF, Word doc):
   - Go to **GeMSentry Dashboard &rarr; ⭐ Finalized Tenders**.
   - Click **➕ Attach Spec Sheet** (or **✏️ Update**) next to the tender.
   - Choose either:
     - **Upload File**: Select your PDF / DOCX file (auto-saved and uploaded to Google Drive).
     - **Paste Google Doc Link**: Paste your Google Docs URL directly (`https://docs.google.com/document/d/...`).
   - Click **Save & Sync**.
   - The vendor's sheet is immediately updated with the clickable link: `📄 View Tech Spec Doc ↗`.
   - The vendor opens the link and selects **YES** or **NO** in their sheet.
   - Any response entered by the vendor is **never overwritten** when future syncs occur.
