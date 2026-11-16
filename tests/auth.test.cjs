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

test('repeated openAuthModal(true) calls while #authModal is already open do NOT overwrite user typing in #authTokenInput or #authPasswordInput', () => {
    let focusCount = 0;
    const elements = {
        splashOverlay: { classList: { hidden: false, add(cls) { if (cls === 'hide') this.hidden = true; } } },
        authModal: { style: { display: 'none', zIndex: '' } },
        authCloseBtn: { style: { display: 'block' } },
        authForgetBtn: { style: { display: 'none' } },
        authFeedback: { style: { display: 'none' } },
        authUsernameInput: { value: '', focus() { focusCount += 1; } },
        authPasswordInput: { value: '', focus() { focusCount += 1; } },
        authTokenInput: { value: '', focus() { focusCount += 1; } }
    };

    const context = vm.createContext({
        URL,
        Headers,
        Request,
        setTimeout: (fn) => { fn(); return 1; },
        localStorage: { getItem: () => 'stale-stored-token' },
        document: {
            getElementById: (id) => elements[id] || null
        },
        window: {
            location: { href: 'http://localhost:5000/', origin: 'http://localhost:5000' },
            fetch: async () => ({ status: 200 })
        }
    });

    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/auth.js'), 'utf8'), context);

    // First call opens modal from closed state, hides splash, sets zIndex 10050, and does NOT prefill stale token
    context.openAuthModal(true);
    assert.equal(elements.authModal.style.display, 'flex');
    assert.equal(elements.authModal.style.zIndex, '10050');
    assert.equal(elements.splashOverlay.classList.hidden, true);
    assert.equal(elements.authTokenInput.value, '');
    assert.equal(elements.authPasswordInput.value, '');
    assert.equal(focusCount, 1);

    // Simulate user typing into both #authPasswordInput and #authTokenInput
    elements.authUsernameInput.value = 'hanmars';
    elements.authPasswordInput.value = 'my-partial-secret-pass';
    elements.authTokenInput.value = 'typing-master-key-123';

    // Repeated 401-triggered openAuthModal(true) calls while modal is already open
    context.openAuthModal(true);
    context.openAuthModal(true);

    // Must NOT wipe or modify what the user typed, and must NOT steal focus again
    assert.equal(elements.authUsernameInput.value, 'hanmars');
    assert.equal(elements.authPasswordInput.value, 'my-partial-secret-pass');
    assert.equal(elements.authTokenInput.value, 'typing-master-key-123');
    assert.equal(focusCount, 1);
});
