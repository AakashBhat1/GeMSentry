const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../gemsentry/google_sync_script.example.gs'), 'utf8');

function runtime(secret = 'test-secret') {
    const forbidden = new Proxy({}, {get() { throw new Error('Unexpected Google data access'); }});
    const context = vm.createContext({
        PropertiesService: {getScriptProperties: () => ({getProperty: () => secret})},
        SpreadsheetApp: forbidden, DriveApp: forbidden,
        ContentService: {MimeType: {JSON: 'json'}, createTextOutput: text => ({setMimeType: () => JSON.parse(text)})}
    });
    vm.runInContext(source, context);
    return context;
}

test('all GET requests are denied without accessing Sheets', () => {
    for (const action of ['ping', 'get_all', 'format_sheet', 'delete_tender']) {
        assert.equal(runtime().doGet({parameter: {action, webhook_secret: 'test-secret'}}).status, 'error');
    }
});

test('every POST action fails closed for missing or invalid secrets', () => {
    const actions = ['ping', 'get_all', 'init_tabs', 'append_tender', 'update_tech_spec',
        'upload_pdf_to_drive', 'upload_tech_spec_to_drive', 'delete_tender',
        'move_to_participated', 'format_sheet', 'sync_vendor_sheets'];
    for (const action of actions) {
        for (const secret of [undefined, '', 'wrong', 123]) {
            const result = runtime().doPost({postData: {contents: JSON.stringify({action, webhook_secret: secret})}});
            assert.equal(result.error, 'Unauthorized.');
        }
    }
    assert.equal(runtime(null).doPost({postData: {contents: JSON.stringify({webhook_secret: 'test-secret'})}}).error, 'Unauthorized.');
});

test('authenticated requests dispatch and use private vendor configuration', () => {
    const context = runtime();
    vm.runInContext("getAllFinalizedData = () => ({status: 'ok', records: []});", context);
    const result = context.doPost({postData: {contents: JSON.stringify({action: 'get_all', webhook_secret: 'test-secret',
        vendor_sheets: {drone: {name: 'Example supplier', spreadsheet_id: 'private-sheet'}}})}});
    assert.equal(result.status, 'ok');
    assert.equal(vm.runInContext('VENDOR_CONFIG.drone.vendor', context), 'Example supplier');
    assert.equal(vm.runInContext('VENDOR_CONFIG.drone.spreadsheetId', context), 'private-sheet');
});

test('invalid JSON never exposes a stack trace', () => {
    const result = runtime().doPost({postData: {contents: '{broken'}});
    assert.equal(result.status, 'error');
    assert.equal(result.stack, undefined);
});
