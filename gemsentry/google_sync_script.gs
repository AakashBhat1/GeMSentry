/**
 * GeMSentry <-> Google Sheets & Google Drive Real-Time Sync Script
 * 
 * Instructions:
 * 1. Open your Google Sheet:
 *    https://docs.google.com/spreadsheets/d/1WbeJJ8goLPGLryyJfcJNbiXtIxXjC9Z0g8viueh5oOk/edit
 * 2. In top menu, click Extensions > Apps Script
 * 3. Replace any code in the editor with this entire file.
 * 4. Click "Deploy" (top right) > "Manage deployments" > Edit (pencil icon) > New Version > Deploy
 *    (or Deploy > New deployment > Web app if deploying first time).
 * 5. Set:
 *    - Execute as: Me
 *    - Who has access: Anyone
 * 6. Copy the Web app URL and paste into GeMSentry Dashboard > Google Sheet Tracker > Settings.
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
 * Click "Run" on testSetup in Google Apps Script editor to:
 * 1. Verify and create sheet structures & standard column widths
 * 2. Compulsorily sync any tenders in UNDER DETAILED STUDY into MASTER
 * 3. Clean any duplicate rows
 * 4. Neatly format all rows to 38px height, Arial 11pt, & clickable Google Drive RFP links!
 */
function testSetup() {
  const struct = ensureSheetStructure();
  const syncRes = syncDetailedStudyToMaster();
  deduplicateSheet('MASTER', false);
  deduplicateSheet('UNDER DETAILED STUDY', false);
  deduplicateSheet('(TENDER DETAILS (PARTICIPATED)', true);
  
  formatEntireSheet('MASTER');
  formatEntireSheet('UNDER DETAILED STUDY');
  formatEntireSheet('(TENDER DETAILS (PARTICIPATED)');
  
  Logger.log("Setup Result: " + JSON.stringify(struct));
  Logger.log("Sync to Master Result: " + JSON.stringify(syncRes));
  Logger.log("Spreadsheet Title: " + SpreadsheetApp.getActiveSpreadsheet().getName());
  return "SUCCESS: All tenders compulsorily verified in MASTER, duplicates cleaned, column widths set, & rows neatly formatted!";
}

function doGet(e) {
  const action = (e && e.parameter && e.parameter.action) || 'ping';
  if (action === 'ping') {
    return jsonResponse({
      status: 'ok',
      message: 'GeMSentry Google Sheet Webhook is active and connected.',
      spreadsheetId: SpreadsheetApp.getActiveSpreadsheet().getId(),
      timestamp: new Date().toISOString()
    });
  }
  if (action === 'get_all') {
    return jsonResponse(getAllFinalizedData());
  }
  if (action === 'format_sheet') {
    const sname = (e && e.parameter && e.parameter.sheet_name) || 'UNDER DETAILED STUDY';
    return jsonResponse(formatEntireSheet(sname));
  }
  return jsonResponse({ error: 'Unknown GET action: ' + action });
}

function doPost(e) {
  try {
    let payload = {};
    if (e && e.postData && e.postData.contents) {
      payload = JSON.parse(e.postData.contents);
    }
    const action = payload.action || 'ping';

    if (action === 'ping') {
      return jsonResponse({ status: 'ok', connected: true });
    }
    if (action === 'init_tabs') {
      return jsonResponse(ensureSheetStructure());
    }
    if (action === 'append_tender') {
      return jsonResponse(appendTender(payload));
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
      return jsonResponse(formatEntireSheet(payload.sheet_name || 'UNDER DETAILED STUDY'));
    }

    return jsonResponse({ error: 'Unknown action: ' + action });
  } catch (err) {
    return jsonResponse({ error: err.toString(), stack: err.stack });
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

function ensureSheetStructure() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

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

  return { status: 'ok', message: 'Tabs verified, widths set, and headers formatted.' };
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

function formatDataRow(sheet, rowIdx, rfpLink, isParticipated) {
  const lastCol = sheet.getLastColumn();
  const range = sheet.getRange(rowIdx, 1, 1, lastCol);

  // 38px row height for neat, breathable spacing
  sheet.setRowHeight(rowIdx, 38);
  range.setFontFamily('Arial');
  range.setFontSize(11);
  range.setVerticalAlignment('middle');
  range.setBorder(true, true, true, true, true, true, '#CBD5E1', SpreadsheetApp.BorderStyle.SOLID);

  if (!isParticipated) {
    // Center non-text columns
    [1, 2, 3, 4, 5, 8, 9, 11, 12, 13, 14, 16, 17, 18].forEach(function(c) {
      if (c <= lastCol) sheet.getRange(rowIdx, c).setHorizontalAlignment('center');
    });

    // Col 1 (SL. NO) bold & centered
    sheet.getRange(rowIdx, 1).setFontWeight('bold').setHorizontalAlignment('center');

    // Col 6 (Organisation) wrap text
    if (lastCol >= 6) sheet.getRange(rowIdx, 6).setWrap(true);

    // Col 8 (Tender ID) bold
    if (lastCol >= 8) sheet.getRange(rowIdx, 8).setFontWeight('bold').setHorizontalAlignment('center');

    // Col 10 (Description) wrap text
    if (lastCol >= 10) sheet.getRange(rowIdx, 10).setWrap(true);

    // Col 15 (EMD) formatted currency
    if (lastCol >= 15) {
      sheet.getRange(rowIdx, 15).setNumberFormat('₹#,##0');
      sheet.getRange(rowIdx, 15).setHorizontalAlignment('right');
    }

    // Col 17 (RFP LINK) - Clean, prominent Google Drive hyperlink
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

    // Col 18 (Approval)
    if (lastCol >= 18) {
      sheet.getRange(rowIdx, 18).setFontColor('#047857').setFontWeight('bold').setHorizontalAlignment('center');
    }
  } else {
    // Participated sheet
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

function formatEntireSheet(sheetName) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
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
    formatDataRow(sheet, r, rfpCell, isPart);
  }
  return { status: 'ok', formatted_rows: Math.max(0, lastRow - startRow + 1) };
}

function getHighestSerialNo() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
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

function insertOrUpdateRow(sheet, row, rfpLink, isParticipated) {
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
    formatDataRow(sheet, targetRowIndex, rfpLink, isParticipated);
    return targetRowIndex;
  } else {
    sheet.appendRow(row);
    const newRow = sheet.getLastRow();
    formatDataRow(sheet, newRow, rfpLink, isParticipated);
    return newRow;
  }
}

function deduplicateSheet(sheetName, isParticipated) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(sheetName);
  if (!sheet) return 0;
  const lastRow = sheet.getLastRow();
  const startRow = isParticipated ? 3 : 5;
  const colBid = isParticipated ? 9 : 8;

  if (lastRow < startRow) return 0;

  let deleted = 0;
  const seenBids = new Set();
  const seenSls = new Set();

  for (let r = lastRow; r >= startRow; r--) {
    const slVal = String(sheet.getRange(r, 1).getValue() || '').trim();
    const bidVal = String(sheet.getRange(r, colBid).getValue() || '').trim().toLowerCase();

    if (!bidVal && !slVal) {
      continue;
    }

    const isDupBid = bidVal && seenBids.has(bidVal);
    const isDupSl = slVal && seenSls.has(slVal);

    if (isDupBid || isDupSl) {
      sheet.deleteRow(r);
      deleted++;
    } else {
      if (bidVal) seenBids.add(bidVal);
      if (slVal) seenSls.add(slVal);
    }
  }
  return deleted;
}

function syncDetailedStudyToMaster() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const study = ss.getSheetByName('UNDER DETAILED STUDY');
  const master = ss.getSheetByName('MASTER');
  if (!study || !master) return { synced: 0 };

  const lastRow = study.getLastRow();
  if (lastRow < 5) return { synced: 0 };

  const studyData = study.getRange(5, 1, lastRow - 4, MASTER_HEADERS.length).getValues();
  let count = 0;

  studyData.forEach(function(row) {
    const bid = row[7] || row[8];
    if (bid && String(bid).trim()) {
      const rfpLink = row[16] || '';
      insertOrUpdateRow(master, row, rfpLink, false);
      count++;
    }
  });

  return { synced: count };
}

function appendTender(payload) {
  payload = payload || {};
  ensureSheetStructure();
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  const tender = payload.tender || {};
  let slNo = payload.sl_no || tender.sl_no;
  if (!slNo) {
    slNo = getHighestSerialNo() + 1;
  }

  // Ensure Google Drive or RFP link is properly captured
  const rfpLink = payload.rfp_link || tender.rfp_link || tender.drive_link || tender.pdf_url || '';

  // Clean dates without UTC timezone shift
  const downloadDateStr = formatDateStr(payload.download_date || tender.download_date);
  const endDateStr = formatDateStr(payload.end_date || tender.end_date);
  const endTimeStr = formatTimeStr(payload.end_time || tender.end_time);

  // Map 19 columns
  const row = [
    slNo,
    payload.download_from || tender.download_from || tender.source_name || 'GEM',
    payload.work_category || tender.work_category || 'SUPPLY',
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

  // 1. Compulsorily append / update in MASTER sheet
  const masterSheet = ss.getSheetByName('MASTER');
  let masterRow = null;
  if (masterSheet) {
    masterRow = insertOrUpdateRow(masterSheet, row, rfpLink, false);
  }

  // 2. Also append / update in secondary sheet if specified (e.g. UNDER DETAILED STUDY)
  const secondaryName = (payload.secondary_sheet || (payload.target_sheet !== 'MASTER' ? payload.target_sheet : '') || '').trim();
  let secRow = null;
  if (secondaryName && secondaryName !== 'MASTER') {
    const secSheet = ss.getSheetByName(secondaryName);
    if (secSheet) {
      secRow = insertOrUpdateRow(secSheet, row, rfpLink, false);
    }
  }

  return {
    status: 'ok',
    sl_no: slNo,
    bid_no: payload.tender_id || tender.bid_no,
    master_appended: true,
    master_row: masterRow,
    secondary_sheet: secondaryName,
    secondary_row: secRow,
    rfp_link: rfpLink
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

  return { status: 'ok', deleted_count: deletedCount, bid_no: bidNo, sl_no: slNo };
}

function moveToParticipated(payload) {
  payload = payload || {};
  ensureSheetStructure();
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const partSheet = ss.getSheetByName('(TENDER DETAILS (PARTICIPATED)');

  const tender = payload.tender || {};
  const slNo = payload.sl_no || tender.sl_no || (getHighestSerialNo() + 1);
  const rfpLink = payload.drive_link || payload.rfp_link || tender.drive_link || tender.rfp_link || '';

  const downloadDateStr = formatDateStr(payload.download_date || tender.download_date);
  const endDateStr = formatDateStr(payload.end_date || tender.end_date);
  const endTimeStr = formatTimeStr(payload.end_time || tender.end_time);

  const row = [
    slNo,
    payload.tender_type || tender.tender_type || 'RFP',
    payload.download_from || tender.download_from || 'GEM',
    payload.work_category || tender.work_category || 'SUPPLY',
    downloadDateStr,
    payload.month || tender.month || Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'MMMM').toUpperCase(),
    payload.organisation || tender.organisation || tender.department || 'N/A',
    payload.location || tender.location || 'N/A',
    payload.tender_id || tender.bid_no || 'N/A',
    payload.reference_no || tender.bid_no || 'N/A',
    payload.description || tender.title || 'N/A',
    endDateStr,
    endTimeStr,
    rfpLink,
    payload.submission_status || 'SUBMITTED',
    payload.submitted_by || 'SUBMITTED BY ETSPL',
    payload.remarks || tender.remarks || '',
    payload.job_aligned_to || '',
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
  formatDataRow(partSheet, newRow, rfpLink, true);

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

  // Look for or create folder "ETSPL Tenders"
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

  // Set to anyone with link viewable
  file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  const driveUrl = 'https://drive.google.com/file/d/' + file.getId() + '/view?usp=drive_link';

  return {
    status: 'ok',
    file_id: file.getId(),
    file_name: fileName,
    drive_link: driveUrl
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
