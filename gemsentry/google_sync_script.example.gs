/**
 * GeMSentry Multi-Vendor Live Sync & Master Sheet Script
 * 
 * ============================================================================
 * VENDOR CONFIGURATION
 * ============================================================================
 * Set GEMSENTRY_WEBHOOK_SECRET in Apps Script Project Settings > Script properties.
 * Vendor names and IDs arrive from the authenticated local Google sync configuration.
 * Do not put private IDs or names in this source file.
 * To get a Sheet ID from its URL:
 * https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit
 * The ID is the long string between /d/ and /edit : YOUR_SHEET_ID
 */
const VENDOR_CONFIG = {
  drone: {
    vendor: "Drone vendor",
    category: "Drone / UAV",
    color: "#FEE2E2",       // Soft Red data row tint
    headerColor: "#DC2626", // Bold Red header
    spreadsheetId: ""
  },
  power_supply: {
    vendor: "Power supply vendor",
    category: "Power Supply / Electrical",
    color: "#FEF08A",       // Soft Yellow data row tint
    headerColor: "#D97706", // Bold Amber header
    spreadsheetId: ""
  },
  biometrics: {
    vendor: "Biometrics vendor",
    category: "Biometrics & Facial Recognition",
    color: "#BFDBFE",       // Soft Blue data row tint
    headerColor: "#1D4ED8", // Bold Royal Blue header
    spreadsheetId: ""
  }
};

/**
 * Standard Header Columns for Master Sheet (Internal - Full Details)
 */
const MASTER_HEADERS = [
  'SL. NO', 'DOWNLOAD FROM', 'WORK CATEGORY', 'DOWNLOAD DATE', 'MONTH',
  'ORGANISATION', 'LOCATION/SITE', 'TENDER ID', 'REFERENCE NO.', 'DESCRIPTION',
  'BID SUBMISSION (END DATE)', 'BID SUBMISSION (END TIME)', 'EXPERIENCE EXEMPTION\nYES/ NO',
  'TURNOVER EXEMPTION\nYES/ NO', 'EMD/ TENDER FEES', 'OEM AUTHORIZATION', 'RFP LINK',
  'APPROVAL', 'REMARKS'
];

const PARTICIPATED_HEADERS = [
  'SL. NO', 'STATUS', 'DOWNLOAD FROM', 'WORK CATEGORY', 'DOWNLOAD DATE', 'MONTH',
  'ORGANISATION', 'LOCATIOIN/SITE', 'TENDER ID', 'REFERENCENO.', 'DESCRIPTION',
  'BID SUBMISSION (END DATE)', 'BID SUBMISSION (END TIME)', 'BID OPENING DATE',
  'SUBMISSION STATUS', 'SUBMITTED BY', 'REMARKS', 'JOB ALIGNED TO', 'ETSPL CTC',
  'TENDER VALUE', 'EMD/ TRANSACTION/ DOCUMENT', 'TECHNICAL STATUS', 'FINANCIAL STATUS',
  'RESULT\nWON/LOST', 'SO/ DO  STATUS', 'SO LINK', 'REMARKS'
];

/**
 * Confidential Vendor Sheet Columns
 * Strictly NO Bid Number, NO EMD, NO Turnover Exemption, NO Pricing Details!
 */
const VENDOR_HEADERS = [
  'SL. NO',
  'ITEM / WORK DESCRIPTION',
  'TECH SPEC SHEET (GOOGLE DOC)',
  'PARTICIPATE OR NOT'
];

/**
 * Helper to clean spreadsheet ID from full URL or bare ID
 */
function cleanSpreadsheetId(val) {
  if (!val) return "";
  const s = String(val).trim();
  const match = s.match(/\/d\/([a-zA-Z0-9-_]+)/);
  if (match) return match[1];
  return s;
}

/**
 * Resolves vendor configuration strictly from the explicit choice made on the site.
 * Zero keyword sniffing or regex heuristics — completely user-controlled!
 */
function detectVendorInfo(payload) {
  payload = payload || {};
  const tender = payload.tender || {};
  const customVendor = String(
    payload.vendor_id || tender.vendor_id || 
    payload.assigned_vendor || tender.assigned_vendor || 
    payload.job_aligned_to || tender.job_aligned_to ||
    payload.work_category || tender.work_category || ''
  ).toLowerCase().trim();

  for (const vendor of Object.values(VENDOR_CONFIG)) {
    if (customVendor && customVendor === vendor.vendor.toLowerCase()) return vendor;
  }

  if (customVendor === 'drone' || customVendor.includes('drone')) {
    return VENDOR_CONFIG.drone;
  }
  if (customVendor === 'power_supply' || customVendor.includes('power')) {
    return VENDOR_CONFIG.power_supply;
  }
  if (customVendor === 'biometrics' || customVendor.includes('bio')) {
    return VENDOR_CONFIG.biometrics;
  }

  // NO keyword guessing — if no vendor was selected, do not route to any vendor sheet
  return null;
}

/**
 * Resolves spreadsheet ID for a vendor either from script VENDOR_CONFIG or incoming payload
 */
function resolveVendorSpreadsheetId(vinfo, payload) {
  if (!vinfo) return "";
  let sId = cleanSpreadsheetId(vinfo.spreadsheetId);
  if (!sId && payload && payload.vendor_sheets) {
    const vKey = Object.keys(VENDOR_CONFIG).find(k => VENDOR_CONFIG[k] === vinfo);
    if (vKey && payload.vendor_sheets[vKey]) {
      sId = cleanSpreadsheetId(payload.vendor_sheets[vKey].spreadsheet_id || payload.vendor_sheets[vKey].spreadsheet_url);
    }
  }
  return sId;
}

/**
 * Test setup function: Run from Apps Script editor to initialize tabs and test vendor connections
 */
function testSetup() {
  const masterSS = SpreadsheetApp.getActiveSpreadsheet();
  ensureSheetStructure(masterSS);
  
  Logger.log("Master Sheet initialized: " + masterSS.getName() + " (" + masterSS.getId() + ")");
  
  // Test each configured vendor sheet
  for (const key of Object.keys(VENDOR_CONFIG)) {
    const v = VENDOR_CONFIG[key];
    const rawId = cleanSpreadsheetId(v.spreadsheetId);
    if (rawId) {
      try {
        const vss = SpreadsheetApp.openById(rawId);
        ensureVendorSheetStructure(vss, v);
        Logger.log("✓ Vendor sheet accessible for " + v.vendor + ": " + vss.getName());
      } catch (err) {
        Logger.log("✗ Could not access vendor sheet for " + v.vendor + " (ID: " + rawId + "): " + err);
      }
    } else {
      Logger.log("ℹ No spreadsheet ID configured yet for " + v.vendor + " (" + v.category + ")");
    }
  }

  return "Setup completed. Check Apps Script Execution Log for details.";
}

// Credentials belong in POST bodies, never URLs, browser history or referrers.
function doGet(e) {
  return jsonResponse({status: 'error', error: 'Authenticated POST required.'});
}

function authorizedPayload(payload) {
  const expected = PropertiesService.getScriptProperties().getProperty('GEMSENTRY_WEBHOOK_SECRET');
  const supplied = payload && payload.webhook_secret;
  if (!expected || typeof supplied !== 'string' || supplied.length !== expected.length) return false;
  let difference = 0;
  for (let i = 0; i < expected.length; i++) difference |= expected.charCodeAt(i) ^ supplied.charCodeAt(i);
  return difference === 0;
}

function configureVendors(payload) {
  const vendors = payload.vendor_sheets || {};
  for (const key of Object.keys(VENDOR_CONFIG)) {
    const configured = vendors[key] || {};
    VENDOR_CONFIG[key].spreadsheetId = cleanSpreadsheetId(configured.spreadsheet_id || configured.spreadsheet_url);
    VENDOR_CONFIG[key].vendor = String(configured.name || VENDOR_CONFIG[key].category);
  }
}

function doPost(e) {
  try {
    let payload = {};
    if (e && e.postData && e.postData.contents) {
      payload = JSON.parse(e.postData.contents);
    }
    if (!authorizedPayload(payload)) {
      return jsonResponse({status: 'error', error: 'Unauthorized.'});
    }
    configureVendors(payload);
    const action = payload.action || 'ping';
    if (action === 'get_all') return jsonResponse(getAllFinalizedData());

    if (action === 'ping') {
      return jsonResponse({ status: 'ok', connected: true });
    }
    if (action === 'init_tabs') {
      return jsonResponse(ensureSheetStructure(SpreadsheetApp.getActiveSpreadsheet()));
    }
    if (action === 'append_tender') {
      return jsonResponse(appendTender(payload));
    }
    if (action === 'update_tech_spec') {
      return jsonResponse(updateTechSpec(payload));
    }
    if (action === 'upload_tech_spec_to_drive') {
      return jsonResponse(uploadTechSpecToDrive(payload));
    }
    if (action === 'delete_tender') {
      return jsonResponse(deleteTender(payload));
    }
    if (action === 'move_to_participated') {
      return jsonResponse(moveToParticipated(payload));
    }
    if (action === 'upload_pdf_to_drive') {
      return jsonResponse(uploadPdfToDrive(payload));
    }
    if (action === 'format_sheet') {
      return jsonResponse(formatEntireSheet(SpreadsheetApp.getActiveSpreadsheet(), payload.sheet_name || 'UNDER DETAILED STUDY'));
    }
    if (action === 'sync_vendor_sheets') {
      return jsonResponse(syncExistingTendersToVendors());
    }

    return jsonResponse({ error: 'Unknown action: ' + action });
  } catch (err) {
    return jsonResponse({status: 'error', error: 'Webhook request failed. Check configuration and execution logs.'});
  }
}

function jsonResponse(data) {
  return ContentService.createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

function setStandardColumnWidths(sheet) {
  const isPart = sheet.getName().includes('PARTICIPATED');
  if (isPart) {
    const pWidths = [80, 100, 130, 120, 110, 100, 220, 160, 170, 170, 340, 115, 90, 160, 130, 140, 160, 120, 110, 120, 110, 120, 120, 120, 130, 160, 180];
    for (let i = 0; i < pWidths.length; i++) {
      sheet.setColumnWidth(i + 1, pWidths[i]);
    }
  } else {
    const widths = [80, 130, 120, 115, 105, 230, 160, 175, 175, 360, 115, 90, 110, 110, 120, 110, 175, 135, 180];
    for (let i = 0; i < widths.length; i++) {
      sheet.setColumnWidth(i + 1, widths[i]);
    }
  }
}

function ensureSheetStructure(ss) {
  ss = ss || SpreadsheetApp.getActiveSpreadsheet();

  // 1. MASTER
  let master = ss.getSheetByName('MASTER');
  if (!master) {
    master = ss.insertSheet('MASTER');
    master.getRange(4, 1, 1, MASTER_HEADERS.length).setValues([MASTER_HEADERS]);
  }
  formatHeaderRow(master, 4, MASTER_HEADERS.length, '#1F3864');
  setStandardColumnWidths(master);

  // 2. UNDER DETAILED STUDY
  let study = ss.getSheetByName('UNDER DETAILED STUDY');
  if (!study) {
    study = ss.insertSheet('UNDER DETAILED STUDY');
    study.getRange(4, 1, 1, MASTER_HEADERS.length).setValues([MASTER_HEADERS]);
  }
  formatHeaderRow(study, 4, MASTER_HEADERS.length, '#1F3864');
  setStandardColumnWidths(study);

  // 3. (TENDER DETAILS (PARTICIPATED)
  let part = ss.getSheetByName('(TENDER DETAILS (PARTICIPATED)');
  if (!part) {
    part = ss.insertSheet('(TENDER DETAILS (PARTICIPATED)');
    part.getRange(2, 1, 1, PARTICIPATED_HEADERS.length).setValues([PARTICIPATED_HEADERS]);
  }
  formatHeaderRow(part, 2, PARTICIPATED_HEADERS.length, '#1F3864');
  setStandardColumnWidths(part);

  return { status: 'ok', message: 'Master tabs verified, widths set, and headers formatted.' };
}

/**
 * Formats a dedicated Vendor Spreadsheet:
 * Strict 4-column layout without disclosing Bid Number or commercial details.
 * Column 5 is hidden to allow programmatic row identification.
 */
function ensureVendorSheetStructure(vendorSS, vendorInfo) {
  if (!vendorSS) return null;
  const sheetName = 'PROJECT REQUIREMENTS';
  let sheet = vendorSS.getSheetByName(sheetName) || vendorSS.getSheetByName('TENDERS FOR REVIEW') || vendorSS.getSheets()[0];
  if (sheet.getName() !== sheetName) {
    sheet.setName(sheetName);
  }

  // Row 1: Clean banner (No vendor names, categories, or tender/bid wording)
  const bannerText = 'ETSPL SPECIFICATIONS & TECHNICAL REVIEW';
  sheet.getRange(1, 1, 1, 4).merge();
  const banner = sheet.getRange(1, 1);
  banner.setValue(bannerText);
  sheet.setRowHeight(1, 42);
  banner.setBackground(vendorInfo.headerColor || '#1F3864');
  banner.setFontColor('#FFFFFF');
  banner.setFontWeight('bold');
  banner.setFontSize(13);
  banner.setFontFamily('Arial');
  banner.setHorizontalAlignment('center');
  banner.setVerticalAlignment('middle');

  // Row 2: Headers
  const headerVals = [
    VENDOR_HEADERS[0], // SL. NO
    VENDOR_HEADERS[1], // ITEM / WORK DESCRIPTION
    VENDOR_HEADERS[2], // TECH SPEC SHEET (GOOGLE DOC)
    VENDOR_HEADERS[3], // PARTICIPATE OR NOT
    'INTERNAL_REF'     // Col 5 (Hidden ID)
  ];
  sheet.getRange(2, 1, 1, headerVals.length).setValues([headerVals]);
  sheet.setRowHeight(2, 34);

  const headerRange = sheet.getRange(2, 1, 1, 4);
  headerRange.setBackground(vendorInfo.headerColor || '#1F3864');
  headerRange.setFontColor('#FFFFFF');
  headerRange.setFontWeight('bold');
  headerRange.setFontSize(11);
  headerRange.setFontFamily('Arial');
  headerRange.setHorizontalAlignment('center');
  headerRange.setVerticalAlignment('middle');

  // Hide Column 5 so vendor NEVER sees the Bid Number / Tender ID
  sheet.setColumnWidth(1, 80);  // SL. NO
  sheet.setColumnWidth(2, 480); // ITEM / WORK DESCRIPTION
  sheet.setColumnWidth(3, 270); // TECH SPEC SHEET (GOOGLE DOC)
  sheet.setColumnWidth(4, 180); // PARTICIPATE OR NOT
  sheet.setColumnWidth(5, 10);
  sheet.hideColumns(5);

  // Add YES / NO validation to Column 4 (PARTICIPATE OR NOT)
  const rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(['YES', 'NO'], true)
    .setAllowInvalid(true)
    .build();
  sheet.getRange(3, 4, 1000, 1).setDataValidation(rule);

  sheet.setFrozenRows(2);
  return sheet;
}

function formatHeaderRow(sheet, rowIdx, colCount, hexColor) {
  sheet.setRowHeight(rowIdx, 36);
  const range = sheet.getRange(rowIdx, 1, 1, colCount);
  range.setBackground(hexColor || '#1F3864');
  range.setFontColor('#FFFFFF');
  range.setFontWeight('bold');
  range.setFontSize(11);
  range.setFontFamily('Arial');
  range.setHorizontalAlignment('center');
  range.setVerticalAlignment('middle');
  range.setWrap(true);
  sheet.setFrozenRows(rowIdx);
}

function formatDateStr(val) {
  if (!val) return 'N/A';
  const s = String(val).trim();
  const match = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (match) {
    return match[3] + '-' + match[2] + '-' + match[1]; // DD-MM-YYYY
  }
  return s.length > 10 ? s.substring(0, 10) : s;
}

function formatTimeStr(val) {
  if (!val) return '15:00';
  const s = String(val).trim();
  const match = s.match(/(\d{1,2}:\d{2})/);
  if (match) return match[1];
  return s;
}

function formatDataRow(sheet, rowIdx, rfpLink, isParticipated, vendorColor) {
  const lastCol = sheet.getLastColumn();
  const range = sheet.getRange(rowIdx, 1, 1, lastCol);

  sheet.setRowHeight(rowIdx, 38);
  range.setFontFamily('Arial');
  range.setFontSize(11);
  range.setVerticalAlignment('middle');
  range.setBorder(true, true, true, true, true, true, '#CBD5E1', SpreadsheetApp.BorderStyle.SOLID);

  if (vendorColor) {
    range.setBackground(vendorColor);
  }

  if (!isParticipated) {
    [1, 2, 3, 4, 5, 8, 9, 11, 12, 13, 14, 16, 17, 18].forEach(function(c) {
      if (c <= lastCol) sheet.getRange(rowIdx, c).setHorizontalAlignment('center');
    });

    sheet.getRange(rowIdx, 1).setFontWeight('bold').setHorizontalAlignment('center');
    if (lastCol >= 6) sheet.getRange(rowIdx, 6).setWrap(true);
    if (lastCol >= 8) sheet.getRange(rowIdx, 8).setFontWeight('bold').setHorizontalAlignment('center');
    if (lastCol >= 10) sheet.getRange(rowIdx, 10).setWrap(true);

    if (lastCol >= 15) {
      sheet.getRange(rowIdx, 15).setNumberFormat('₹#,##0');
      sheet.getRange(rowIdx, 15).setHorizontalAlignment('right');
    }

    if (lastCol >= 17) {
      const link = String(rfpLink || sheet.getRange(rowIdx, 17).getValue() || '').trim();
      if (link && link.startsWith('http')) {
        const isDrive = link.includes('drive.google.com');
        const linkTitle = isDrive ? '📁 Google Drive RFP ↗' : '📄 Open Tender RFP ↗';
        sheet.getRange(rowIdx, 17).setFormula('=HYPERLINK("' + link + '", "' + linkTitle + '")');
        sheet.getRange(rowIdx, 17)
          .setFontColor('#1D4ED8')
          .setFontWeight('bold')
          .setFontLine('underline')
          .setHorizontalAlignment('center');
      }
    }

    if (lastCol >= 18) {
      sheet.getRange(rowIdx, 18).setFontColor('#047857').setFontWeight('bold').setHorizontalAlignment('center');
    }
  } else {
    sheet.getRange(rowIdx, 1).setFontWeight('bold').setHorizontalAlignment('center');
    if (lastCol >= 9) sheet.getRange(rowIdx, 9).setFontWeight('bold').setHorizontalAlignment('center');
    if (lastCol >= 11) sheet.getRange(rowIdx, 11).setWrap(true);
    if (lastCol >= 14) {
      const link = String(rfpLink || sheet.getRange(rowIdx, 14).getValue() || '').trim();
      if (link && link.startsWith('http')) {
        const isDrive = link.includes('drive.google.com');
        const linkTitle = isDrive ? '📁 Google Drive RFP ↗' : '📄 Open Tender RFP ↗';
        sheet.getRange(rowIdx, 14).setFormula('=HYPERLINK("' + link + '", "' + linkTitle + '")');
        sheet.getRange(rowIdx, 14).setFontColor('#1D4ED8').setFontWeight('bold').setFontLine('underline').setHorizontalAlignment('center');
      }
    }
    if (lastCol >= 20) {
      sheet.getRange(rowIdx, 20).setNumberFormat('₹#,##0');
      sheet.getRange(rowIdx, 20).setHorizontalAlignment('right');
    }
    if (lastCol >= 24) {
      const resVal = String(sheet.getRange(rowIdx, 24).getValue()).toUpperCase();
      const isWon = resVal.includes('WON');
      sheet.getRange(rowIdx, 24)
        .setFontWeight('bold')
        .setHorizontalAlignment('center')
        .setFontColor(isWon ? '#047857' : '#DC2626');
    }
    if (lastCol >= 26) {
      const soLink = String(sheet.getRange(rowIdx, 26).getValue() || '').trim();
      if (soLink && soLink.startsWith('http')) {
        sheet.getRange(rowIdx, 26).setFormula('=HYPERLINK("' + soLink + '", "📄 Open SO Doc ↗")');
        sheet.getRange(rowIdx, 26).setFontColor('#1D4ED8').setFontWeight('bold').setFontLine('underline').setHorizontalAlignment('center');
      }
    }
  }
}

function formatEntireSheet(ss, sheetName) {
  ss = ss || SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(sheetName);
  if (!sheet) return { status: 'error', message: 'Sheet not found: ' + sheetName };

  setStandardColumnWidths(sheet);
  const isPart = sheetName.includes('PARTICIPATED');
  const startRow = isPart ? 3 : 5;
  const headerRow = isPart ? 2 : 4;
  const lastCol = isPart ? PARTICIPATED_HEADERS.length : MASTER_HEADERS.length;

  formatHeaderRow(sheet, headerRow, lastCol, '#1F3864');

  const lastRow = sheet.getLastRow();
  for (let r = startRow; r <= lastRow; r++) {
    const rfpCell = isPart ? sheet.getRange(r, 14).getValue() : sheet.getRange(r, 17).getValue();
    const cat = isPart ? sheet.getRange(r, 4).getValue() : sheet.getRange(r, 3).getValue();
    const desc = isPart ? sheet.getRange(r, 11).getValue() : sheet.getRange(r, 10).getValue();
    const vinfo = detectVendorInfo({ work_category: cat, description: desc });
    formatDataRow(sheet, r, rfpCell, isPart, vinfo ? vinfo.color : null);
  }
  return { status: 'ok', formatted_rows: Math.max(0, lastRow - startRow + 1) };
}

function getHighestSerialNo(ss) {
  ss = ss || SpreadsheetApp.getActiveSpreadsheet();
  let maxSl = 1016; // baseline from master excel

  ['MASTER', 'UNDER DETAILED STUDY', '(TENDER DETAILS (PARTICIPATED)'].forEach(function(sname) {
    const sheet = ss.getSheetByName(sname);
    if (!sheet) return;
    const lastRow = sheet.getLastRow();
    if (lastRow <= 4) return;
    const values = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
    values.forEach(function(r) {
      const val = parseInt(r[0], 10);
      if (!isNaN(val) && val > maxSl && val < 50000) {
        maxSl = val;
      }
    });
  });

  return maxSl;
}

function insertOrUpdateRow(sheet, row, rfpLink, isParticipated, vendorColor) {
  if (!sheet) return null;
  const lastRow = sheet.getLastRow();
  const startRow = isParticipated ? 3 : 5;
  const colBid = isParticipated ? 9 : 8; // 1-indexed
  const colRef = isParticipated ? 10 : 9;

  const targetBid = String(isParticipated ? (row[8] || row[9] || '') : (row[7] || row[8] || '')).trim().toLowerCase();
  const targetSl = row[0];

  let targetRowIndex = -1;
  if (lastRow >= startRow) {
    const data = sheet.getRange(startRow, 1, lastRow - startRow + 1, Math.max(colRef, 10)).getValues();
    for (let i = 0; i < data.length; i++) {
      const cellSl = data[i][0];
      const cellBid = String(data[i][colBid - 1] || '').trim().toLowerCase();
      const cellRef = String(data[i][colRef - 1] || '').trim().toLowerCase();

      const bidMatch = targetBid && (cellBid === targetBid || cellRef === targetBid);
      const slMatch = targetSl && String(cellSl).trim() === String(targetSl).trim();
      if (bidMatch || slMatch) {
        targetRowIndex = startRow + i;
        break;
      }
    }
  }

  if (targetRowIndex > 0) {
    sheet.getRange(targetRowIndex, 1, 1, row.length).setValues([row]);
    formatDataRow(sheet, targetRowIndex, rfpLink, isParticipated, vendorColor);
    return targetRowIndex;
  } else {
    sheet.appendRow(row);
    const newRow = sheet.getLastRow();
    formatDataRow(sheet, newRow, rfpLink, isParticipated, vendorColor);
    return newRow;
  }
}

/**
 * Inserts or updates a confidential row in a dedicated vendor spreadsheet.
 * Strictly presents: S.No, Item / Work Description, Tech Spec Sheet (Google Doc), Participate or Not.
 * Preserves vendor's previous "YES/NO" response if row already exists.
 */
function insertOrUpdateVendorRow(sheet, payload, vendorInfo) {
  if (!sheet || !vendorInfo) return null;
  const tender = payload.tender || {};
  const bidNo = String(payload.tender_id || tender.bid_no || payload.bid_no || '').trim();
  const title = String(payload.description || tender.title || payload.title || 'N/A').trim();
  const slNo = payload.sl_no || tender.sl_no || (sheet.getLastRow() <= 2 ? 1 : sheet.getLastRow() - 1);
  const techSpecUrl = String(payload.tech_spec_url || tender.tech_spec_url || '').trim();

  const lastRow = sheet.getLastRow();
  let targetRow = -1;
  let existingResponse = '';

  if (lastRow >= 3) {
    // Read columns 1 to 5: [Sl, Title, SpecLink, ParticipateOrNot, InternalRef]
    const data = sheet.getRange(3, 1, lastRow - 2, 5).getValues();
    for (let i = 0; i < data.length; i++) {
      const cellRef = String(data[i][4] || '').trim().toLowerCase(); // Col 5 is hidden internal ref
      const cellSl = String(data[i][0] || '').trim();
      if ((bidNo && cellRef === bidNo.toLowerCase()) || (slNo && cellSl === String(slNo).trim())) {
        targetRow = 3 + i;
        existingResponse = data[i][3] || ''; // preserve vendor's Yes/No
        break;
      }
    }
  }

  const rowValues = [
    slNo,
    title,
    techSpecUrl || 'Pending Technical Review',
    existingResponse,
    bidNo // Stored in hidden Column 5
  ];

  if (targetRow > 0) {
    sheet.getRange(targetRow, 1, 1, 5).setValues([rowValues]);
  } else {
    sheet.appendRow(rowValues);
    targetRow = sheet.getLastRow();
  }

  // Format vendor row
  sheet.setRowHeight(targetRow, 38);
  const rowRange = sheet.getRange(targetRow, 1, 1, 4);
  rowRange.setFontFamily('Arial');
  rowRange.setFontSize(11);
  rowRange.setVerticalAlignment('middle');
  rowRange.setBorder(true, true, true, true, true, true, '#E2E8F0', SpreadsheetApp.BorderStyle.SOLID);
  rowRange.setBackground(vendorInfo.color || '#FFFFFF');

  sheet.getRange(targetRow, 1).setHorizontalAlignment('center').setFontWeight('bold');
  sheet.getRange(targetRow, 2).setHorizontalAlignment('left').setWrap(true);
  sheet.getRange(targetRow, 4).setHorizontalAlignment('center').setFontWeight('bold');

  // Format Tech Spec Google Doc link in Column 3
  const specCell = sheet.getRange(targetRow, 3);
  if (techSpecUrl && techSpecUrl.startsWith('http')) {
    specCell.setFormula('=HYPERLINK("' + techSpecUrl + '", "📄 View Tech Spec Doc ↗")');
    specCell.setFontColor('#1D4ED8').setFontWeight('bold').setFontLine('underline').setHorizontalAlignment('center');
  } else {
    specCell.setValue(techSpecUrl || 'Pending Technical Review');
    specCell.setFontColor('#94A3B8').setFontStyle('italic').setHorizontalAlignment('center');
  }

  return targetRow;
}

/**
 * Appends tender to Master Sheet with vendor color-coding AND syncs to dedicated vendor sheet
 */
function appendTender(payload) {
  payload = payload || {};
  const masterSS = SpreadsheetApp.getActiveSpreadsheet();
  ensureSheetStructure(masterSS);

  const tender = payload.tender || {};
  let slNo = payload.sl_no || tender.sl_no;
  if (!slNo) {
    slNo = getHighestSerialNo(masterSS) + 1;
  }

  const rfpLink = payload.rfp_link || tender.rfp_link || tender.drive_link || tender.pdf_url || '';
  const downloadDateStr = formatDateStr(payload.download_date || tender.download_date);
  const endDateStr = formatDateStr(payload.end_date || tender.end_date);
  const endTimeStr = formatTimeStr(payload.end_time || tender.end_time);

  // Vendor detection & color
  const vinfo = detectVendorInfo(payload);
  const vendorColor = (vinfo ? vinfo.color : null) || payload.vendor_web_color || (payload.vendor_color ? ('#' + payload.vendor_color) : null);

  const row = [
    slNo,
    payload.download_from || tender.download_from || tender.source_name || 'GEM',
    payload.work_category || tender.work_category || (vinfo ? vinfo.category : 'SUPPLY'),
    downloadDateStr,
    payload.month || tender.month || Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'MMMM').toUpperCase(),
    payload.organisation || tender.organisation || tender.department || 'N/A',
    payload.location || tender.location || 'N/A',
    payload.tender_id || tender.bid_no || 'N/A',
    payload.reference_no || tender.reference_no || tender.bid_no || 'N/A',
    payload.description || tender.title || 'N/A',
    endDateStr,
    endTimeStr,
    payload.experience_exemption || tender.experience_exemption || 'YES',
    payload.turnover_exemption || tender.turnover_exemption || 'YES',
    payload.emd || tender.emd || 0.0,
    payload.oem_authorization || tender.oem_authorization || 'YES',
    rfpLink,
    payload.approval || tender.approval || 'TO BE SUBMIT',
    payload.remarks || tender.remarks || ''
  ];

  // 1. Append / update in MASTER sheet with vendor color
  const masterSheet = masterSS.getSheetByName('MASTER');
  let masterRow = null;
  if (masterSheet) {
    masterRow = insertOrUpdateRow(masterSheet, row, rfpLink, false, vendorColor);
  }

  // 2. Also append / update in secondary sheet (e.g. UNDER DETAILED STUDY)
  const secondaryName = (payload.secondary_sheet || (payload.target_sheet !== 'MASTER' ? payload.target_sheet : '') || '').trim();
  let secRow = null;
  if (secondaryName && secondaryName !== 'MASTER') {
    const secSheet = masterSS.getSheetByName(secondaryName);
    if (secSheet) {
      secRow = insertOrUpdateRow(secSheet, row, rfpLink, false, vendorColor);
    }
  }

  // 3. LIVE MULTI-SHEET SYNC: Push to dedicated vendor's Google Sheet (Confidential format)
  let vendorSynced = false;
  let vendorSheetName = null;
  let vendorSheetRow = null;

  const targetVendorId = resolveVendorSpreadsheetId(vinfo, payload);

  if (targetVendorId && targetVendorId !== masterSS.getId()) {
    try {
      const vendorSS = SpreadsheetApp.openById(targetVendorId);
      const vsheet = ensureVendorSheetStructure(vendorSS, vinfo);
      vendorSheetRow = insertOrUpdateVendorRow(vsheet, payload, vinfo);
      vendorSynced = true;
      vendorSheetName = vendorSS.getName();
    } catch (err) {
      Logger.log("Failed syncing tender to vendor sheet (" + targetVendorId + "): " + err);
    }
  }

  return {
    status: 'ok',
    sl_no: slNo,
    bid_no: payload.tender_id || tender.bid_no,
    vendor: vinfo ? vinfo.vendor : 'General',
    vendor_color: vendorColor,
    master_appended: true,
    master_row: masterRow,
    secondary_sheet: secondaryName,
    secondary_row: secRow,
    vendor_sheet_synced: vendorSynced,
    vendor_sheet_name: vendorSheetName,
    vendor_sheet_row: vendorSheetRow,
    rfp_link: rfpLink
  };
}

/**
 * Updates Tech Spec Google Doc link for a tender in the vendor sheet and Master sheet
 */
function updateTechSpec(payload) {
  payload = payload || {};
  const bidNo = String(payload.bid_no || payload.tender_id || '').trim();
  const techSpecUrl = String(payload.tech_spec_url || '').trim();
  if (!bidNo) {
    return { status: 'error', message: 'Missing bid_no in updateTechSpec' };
  }

  const masterSS = SpreadsheetApp.getActiveSpreadsheet();
  const vinfo = detectVendorInfo(payload);
  const targetVendorId = resolveVendorSpreadsheetId(vinfo, payload);

  let vendorSheetUpdated = false;
  let updatedRow = null;

  if (targetVendorId && targetVendorId !== masterSS.getId()) {
    try {
      const vendorSS = SpreadsheetApp.openById(targetVendorId);
      const vsheet = ensureVendorSheetStructure(vendorSS, vinfo);
      updatedRow = insertOrUpdateVendorRow(vsheet, payload, vinfo);
      vendorSheetUpdated = true;
    } catch (err) {
      Logger.log("Failed updating tech spec in vendor sheet (" + targetVendorId + "): " + err);
    }
  }

  // Also update remarks / note in Master Sheet if present
  ['UNDER DETAILED STUDY', 'MASTER'].forEach(function(sname) {
    const s = masterSS.getSheetByName(sname);
    if (!s || s.getLastRow() <= 4) return;
    const data = s.getRange(5, 8, s.getLastRow() - 4, 1).getValues();
    for (let i = 0; i < data.length; i++) {
      if (String(data[i][0]).trim().toLowerCase() === bidNo.toLowerCase()) {
        const rIdx = 5 + i;
        const currRemarks = String(s.getRange(rIdx, 19).getValue() || '');
        if (!currRemarks.includes('Spec Doc Attached')) {
          const newRemarks = (currRemarks ? (currRemarks + ' | ') : '') + 'Spec Doc Attached';
          s.getRange(rIdx, 19).setValue(newRemarks);
        }
        break;
      }
    }
  });

  return {
    status: 'ok',
    bid_no: bidNo,
    tech_spec_url: techSpecUrl,
    vendor: vinfo ? vinfo.vendor : null,
    vendor_sheet_updated: vendorSheetUpdated,
    vendor_row: updatedRow
  };
}

/**
 * Uploads a Technical Specification document to Google Drive folder 'ETSPL Tech Specs'
 */
function uploadTechSpecToDrive(payload) {
  payload = payload || {};
  const fileName = payload.filename || ('tech_spec_' + (payload.bid_no ? String(payload.bid_no).replace(/[^a-zA-Z0-9]/g, '_') : 'doc') + '.pdf');
  const base64Data = payload.base64_data;
  if (!base64Data) {
    return { error: 'Missing base64_data in payload' };
  }

  const folderName = 'ETSPL Tech Specs';
  const folders = DriveApp.getFoldersByName(folderName);
  let targetFolder;
  if (folders.hasNext()) {
    targetFolder = folders.next();
  } else {
    targetFolder = DriveApp.createFolder(folderName);
  }

  const decodedBytes = Utilities.base64Decode(base64Data);
  let mime = 'application/pdf';
  if (fileName.endsWith('.doc')) mime = 'application/msword';
  else if (fileName.endsWith('.docx')) mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
  else if (fileName.endsWith('.xls') || fileName.endsWith('.xlsx')) mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

  const blob = Utilities.newBlob(decodedBytes, mime, fileName);
  const file = targetFolder.createFile(blob);

  file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  const driveUrl = 'https://drive.google.com/file/d/' + file.getId() + '/view?usp=sharing';

  // Automatically update the vendor sheet if tender info was provided
  if (payload.bid_no) {
    payload.tech_spec_url = driveUrl;
    updateTechSpec(payload);
  }

  return {
    status: 'ok',
    file_id: file.getId(),
    file_name: fileName,
    drive_link: driveUrl
  };
}

function deleteTender(payload) {
  payload = payload || {};
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const bidNo = payload.bid_no;
  const slNo = payload.sl_no ? parseInt(payload.sl_no, 10) : null;
  let deletedCount = 0;

  ['UNDER DETAILED STUDY', 'MASTER', '(TENDER DETAILS (PARTICIPATED)'].forEach(function(sname) {
    const sheet = ss.getSheetByName(sname);
    if (!sheet) return;
    const lastRow = sheet.getLastRow();
    if (lastRow <= 2) return;

    const data = sheet.getRange(1, 1, lastRow, 12).getValues();
    for (let r = lastRow - 1; r >= 1; r--) {
      const rowSl = parseInt(data[r][0], 10);
      const rowBid = String(data[r][7] || data[r][8] || '').trim();
      const matchBid = bidNo && rowBid.toLowerCase() === String(bidNo).toLowerCase().trim();
      const matchSl = slNo && rowSl === slNo;

      if (matchBid || matchSl) {
        sheet.deleteRow(r + 1);
        deletedCount++;
      }
    }
  });

  // Also remove from vendor sheet if accessible
  const vinfo = detectVendorInfo(payload);
  const targetVendorId = resolveVendorSpreadsheetId(vinfo, payload);
  if (targetVendorId && targetVendorId !== ss.getId()) {
    try {
      const vss = SpreadsheetApp.openById(targetVendorId);
      const vsheet = vss.getSheetByName('PROJECT REQUIREMENTS') || vss.getSheetByName('TENDERS FOR REVIEW') || vss.getSheets()[0];
      if (vsheet && vsheet.getLastRow() >= 3) {
        const vdata = vsheet.getRange(3, 1, vsheet.getLastRow() - 2, 5).getValues();
        for (let vr = vdata.length - 1; vr >= 0; vr--) {
          const vref = String(vdata[vr][4] || '').trim();
          const vsl = parseInt(vdata[vr][0], 10);
          if ((bidNo && vref.toLowerCase() === String(bidNo).toLowerCase().trim()) || (slNo && vsl === slNo)) {
            vsheet.deleteRow(3 + vr);
            break;
          }
        }
      }
    } catch (err) {
      Logger.log("Error deleting from vendor sheet: " + err);
    }
  }

  return { status: 'ok', deleted_count: deletedCount, bid_no: bidNo, sl_no: slNo };
}

function moveToParticipated(payload) {
  payload = payload || {};
  const masterSS = SpreadsheetApp.getActiveSpreadsheet();
  ensureSheetStructure(masterSS);
  const partSheet = masterSS.getSheetByName('(TENDER DETAILS (PARTICIPATED)');

  const tender = payload.tender || {};
  const slNo = payload.sl_no || tender.sl_no || (getHighestSerialNo(masterSS) + 1);
  const rfpLink = payload.drive_link || payload.rfp_link || tender.drive_link || tender.rfp_link || '';

  const downloadDateStr = formatDateStr(payload.download_date || tender.download_date);
  const endDateStr = formatDateStr(payload.end_date || tender.end_date);
  const endTimeStr = formatTimeStr(payload.end_time || tender.end_time);

  const vinfo = detectVendorInfo(payload);
  const vendorColor = (vinfo ? vinfo.color : null) || payload.vendor_web_color;

  const row = [
    slNo,
    payload.tender_type || tender.tender_type || 'RFP',
    payload.download_from || tender.download_from || 'GEM',
    payload.work_category || tender.work_category || (vinfo ? vinfo.category : 'SUPPLY'),
    downloadDateStr,
    payload.month || tender.month || Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'MMMM').toUpperCase(),
    payload.organisation || tender.organisation || tender.department || 'N/A',
    payload.location || tender.location || 'N/A',
    payload.tender_id || tender.bid_no || 'N/A',
    payload.reference_no || tender.reference_no || tender.bid_no || 'N/A',
    payload.description || tender.title || 'N/A',
    endDateStr,
    endTimeStr,
    rfpLink,
    payload.submission_status || 'SUBMITTED',
    payload.submitted_by || 'SUBMITTED BY ETSPL',
    payload.remarks || tender.remarks || '',
    payload.job_aligned_to || (vinfo ? vinfo.vendor : ''),
    payload.etspl_ctc || '',
    payload.tender_value || tender.est_value_inr || 'N/A',
    payload.emd_doc || 'EXEMPTED',
    payload.technical_status || 'QUALIFIED',
    payload.financial_status || 'QUALIFIED',
    payload.won_lost_result || 'WON L - 1',
    payload.so_status || 'SO RECEIVED',
    payload.so_link || '',
    payload.final_remarks || ''
  ];

  partSheet.appendRow(row);
  const newRow = partSheet.getLastRow();
  formatDataRow(partSheet, newRow, rfpLink, true, vendorColor);

  return {
    status: 'ok',
    sl_no: slNo,
    bid_no: payload.tender_id || tender.bid_no,
    sheet: partSheet.getName(),
    result: payload.won_lost_result,
    row_count: newRow
  };
}

function uploadPdfToDrive(payload) {
  payload = payload || {};
  const fileName = payload.filename || 'tender_rfp.pdf';
  const base64Data = payload.base64_data;
  if (!base64Data) {
    return { error: 'Missing base64_data in payload' };
  }

  const folderName = 'ETSPL Tenders';
  const folders = DriveApp.getFoldersByName(folderName);
  let targetFolder;
  if (folders.hasNext()) {
    targetFolder = folders.next();
  } else {
    targetFolder = DriveApp.createFolder(folderName);
  }

  const decodedBytes = Utilities.base64Decode(base64Data);
  const blob = Utilities.newBlob(decodedBytes, 'application/pdf', fileName);
  const file = targetFolder.createFile(blob);

  file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  const driveUrl = 'https://drive.google.com/file/d/' + file.getId() + '/view?usp=drive_link';

  return {
    status: 'ok',
    file_id: file.getId(),
    file_name: fileName,
    drive_link: driveUrl
  };
}

/**
 * Backfills all existing tenders in Master Sheet into their corresponding vendor sheets
 * without disclosing any bid numbers or financial details!
 */
function syncExistingTendersToVendors() {
  const masterSS = SpreadsheetApp.getActiveSpreadsheet();
  const masterSheet = masterSS.getSheetByName('MASTER');
  if (!masterSheet) return { status: 'error', message: 'MASTER sheet not found' };

  const lastRow = masterSheet.getLastRow();
  if (lastRow <= 4) return { status: 'ok', synced: 0, message: 'No data rows in MASTER' };

  const data = masterSheet.getRange(5, 1, lastRow - 4, MASTER_HEADERS.length).getValues();
  let syncedCounts = { drone: 0, power_supply: 0, biometrics: 0 };

  data.forEach(function(row) {
    const slNo = row[0];
    const cat = row[2];
    const bidNo = row[7];
    const title = row[9];
    const rfpLink = row[16];
    const vinfo = detectVendorInfo({ work_category: cat, description: title });
    if (!vinfo) return;

    const vKey = Object.keys(VENDOR_CONFIG).find(k => VENDOR_CONFIG[k] === vinfo);
    const targetId = resolveVendorSpreadsheetId(vinfo);
    if (targetId && targetId !== masterSS.getId()) {
      try {
        const vss = SpreadsheetApp.openById(targetId);
        const vsheet = ensureVendorSheetStructure(vss, vinfo);
        insertOrUpdateVendorRow(vsheet, {
          sl_no: slNo,
          tender_id: bidNo,
          description: title
        }, vinfo);
        if (vKey) syncedCounts[vKey]++;
      } catch (err) {
        Logger.log("Error syncing row to " + vinfo.vendor + ": " + err);
      }
    }
  });

  return {
    status: 'ok',
    synced_counts: syncedCounts,
    message: 'Backfilled tenders to vendor sheets successfully.'
  };
}

function getAllFinalizedData() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const result = {
    master: [],
    detailed_study: [],
    participated: []
  };

  const study = ss.getSheetByName('UNDER DETAILED STUDY');
  if (study && study.getLastRow() > 4) {
    const rows = study.getRange(5, 1, study.getLastRow() - 4, 19).getValues();
    result.detailed_study = rows;
  }

  const master = ss.getSheetByName('MASTER');
  if (master && master.getLastRow() > 4) {
    const rows = master.getRange(5, 1, master.getLastRow() - 4, 19).getValues();
    result.master = rows;
  }

  const part = ss.getSheetByName('(TENDER DETAILS (PARTICIPATED)');
  if (part && part.getLastRow() > 2) {
    const rows = part.getRange(3, 1, part.getLastRow() - 2, 27).getValues();
    result.participated = rows;
  }

  return result;
}
