const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

test('fetch adds credentials only to same-origin requests and preserves caller headers', async () => {
    const calls = [];
    const context = vm.createContext({URL, Headers, Request,
        localStorage: {getItem: () => 'test-access-key'},
        window: {location: {href: 'http://localhost:5000/', origin: 'http://localhost:5000'},
            fetch: async (...args) => { calls.push(args); return {status: 200}; }}
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/auth.js'), 'utf8'), context);
    const headers = {'Content-Type': 'application/json'};
    await context.window.fetch('/api/keywords', {headers});
    assert.equal(calls[0][1].headers.get('Authorization'), 'Bearer test-access-key');
    assert.equal(calls[0][1].headers.get('Content-Type'), 'application/json');
    assert.equal(headers.Authorization, undefined);
    await context.window.fetch('https://external.example/api');
    assert.equal(calls[1][1].headers.has('Authorization'), false);
    await context.window.fetch(new Request('http://localhost:5000/api/status', {headers: {'X-Test': 'retained'}}));
    assert.equal(calls[2][1].headers.get('X-Test'), 'retained');
});
