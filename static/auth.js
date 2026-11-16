// Global Fetch Interceptor & Session/RBAC Authentication Manager
// Security: Strictly uses createElement / textContent / replaceChildren.
// TODO(security): Migrate Bearer header token from localStorage to an in-memory or Double-Submit CSRF token if XSS surface expands; currently paired with HttpOnly SameSite=Strict gemsentry_token cookie and required by tests/auth.test.cjs.
// TODO(security): Enforce strict Content-Security-Policy (CSP) with nonces and Subresource Integrity (SRI) for external font stylesheets.

window.__gemsentryAuthLocked = Boolean(window.__gemsentryAuthLocked);
window.__gemsentryCurrentUser = window.__gemsentryCurrentUser || null;
window.__gemsentryAuthTab = window.__gemsentryAuthTab || 'credentials';

const originalFetch = window.fetch.bind(window);
window.fetch = async function (url, options = {}) {
    options = { ...(options || {}) };
    const target = new URL(url instanceof Request ? url.url : url, window.location.href);
    const sameOrigin = target.origin === window.location.origin;
    options.headers = new Headers(options.headers || (url instanceof Request ? url.headers : undefined));
    const token = (typeof localStorage !== 'undefined' && localStorage && typeof localStorage.getItem === 'function')
        ? localStorage.getItem('gemsentry_auth_token')
        : null;
    if (token && sameOrigin) {
        options.headers.set('Authorization', `Bearer ${token}`);
    }
    const res = await originalFetch(url, options);
    if (sameOrigin && res.status === 401 && !target.pathname.startsWith('/api/auth/')) {
        window.__gemsentryAuthLocked = true;
        openAuthModal(true);
    }
    return res;
};

function authHeaders() {
    const token = (typeof localStorage !== 'undefined' && localStorage && typeof localStorage.getItem === 'function')
        ? localStorage.getItem('gemsentry_auth_token')
        : null;
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

async function downloadSummaryExcel() {
    // Fetched with the bearer header rather than ?token=, which would
    // leak the access key into server logs, browser history and any
    // outgoing Referer.
    try {
        const res = await originalFetch('/api/export/summary.xlsx', { headers: authHeaders() });
        if (!res.ok) {
            if (res.status === 401) openAuthModal(true);
            return;
        }
        if (typeof document === 'undefined') return;
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `tender_summary_${new Date().toISOString().slice(0, 10)}.xlsx`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    } catch (_err) {
        console.error('Summary download failed.');
    }
}

function updateUserSessionUI(user) {
    if (typeof document === 'undefined') return;
    const badge = document.getElementById('userSessionBadge');
    const signOutBtn = document.getElementById('signOutBtn');
    const authBtn = document.getElementById('authKeyBtn');

    const effectiveUser = user || window.__gemsentryCurrentUser;
    const isAdmin = !effectiveUser || effectiveUser.role === 'admin';

    if (badge) {
        if (effectiveUser) {
            badge.style.display = 'inline-flex';
            if (typeof badge.replaceChildren === 'function') {
                badge.replaceChildren();
            } else {
                while (badge.firstChild) badge.removeChild(badge.firstChild);
            }
            const nameSpan = document.createElement('span');
            nameSpan.textContent = effectiveUser.display_name || effectiveUser.username || 'User';
            const roleSpan = document.createElement('span');
            const role = effectiveUser.role || 'admin';
            roleSpan.className = `user-role-pill role-${role}`;
            roleSpan.textContent = effectiveUser.company_name
                ? `${effectiveUser.company_name} · ${role}`
                : role;
            badge.appendChild(nameSpan);
            badge.appendChild(roleSpan);
        } else {
            badge.style.display = 'none';
        }
    }

    if (signOutBtn) {
        signOutBtn.style.display = effectiveUser ? 'inline-flex' : 'none';
    }
    if (authBtn) {
        authBtn.style.display = effectiveUser ? 'none' : 'inline-flex';
    }

    // Toggle admin-only header buttons based on user role
    const adminIds = [
        'sourcesToggle',
        'profileToggle',
        'tuningToggle',
        'settingsToggle',
        'exportExcelBtn',
        'masterSheetBtn',
        'headerScrapePortalBtn'
    ];
    adminIds.forEach((id) => {
        const node = document.getElementById(id);
        if (node) {
            node.style.display = isAdmin ? '' : 'none';
        }
    });
}

async function checkAuthRequirement() {
    try {
        const res = await originalFetch('/api/auth/status', { headers: authHeaders() });
        const data = await res.json();
        const authBtn = typeof document !== 'undefined' ? document.getElementById('authKeyBtn') : null;

        if (data.authenticated && data.user) {
            window.__gemsentryAuthLocked = false;
            window.__gemsentryCurrentUser = data.user;
            updateUserSessionUI(data.user);
            return true;
        }

        if (data.auth_required) {
            if (authBtn) authBtn.style.display = 'inline-flex';
            const stored = (typeof localStorage !== 'undefined' && localStorage && typeof localStorage.getItem === 'function')
                ? localStorage.getItem('gemsentry_auth_token')
                : null;
            if (!stored) {
                window.__gemsentryAuthLocked = true;
                openAuthModal(true);
                return false;
            }
            const verifyRes = await originalFetch('/api/auth/verify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', ...authHeaders() },
                body: JSON.stringify({ token: stored })
            });
            if (!verifyRes.ok) {
                if (typeof localStorage !== 'undefined' && localStorage && typeof localStorage.removeItem === 'function') {
                    localStorage.removeItem('gemsentry_auth_token');
                }
                window.__gemsentryAuthLocked = true;
                openAuthModal(true);
                return false;
            }
            const vData = await verifyRes.json().catch(() => ({}));
            if (vData.session_token && typeof localStorage !== 'undefined' && localStorage && typeof localStorage.setItem === 'function') {
                localStorage.setItem('gemsentry_auth_token', vData.session_token);
            }
            window.__gemsentryAuthLocked = false;
            window.__gemsentryCurrentUser = vData.user || data.user || {
                username: 'etspl',
                display_name: 'ETSPL Admin',
                role: 'admin',
                company_id: 'etspl'
            };
            updateUserSessionUI(window.__gemsentryCurrentUser);
            return true;
        } else {
            if (authBtn) authBtn.style.display = 'none';
            window.__gemsentryAuthLocked = false;
            window.__gemsentryCurrentUser = data.user || {
                username: 'etspl',
                display_name: 'ETSPL Admin',
                role: 'admin',
                company_id: 'etspl'
            };
            updateUserSessionUI(window.__gemsentryCurrentUser);
            return true;
        }
    } catch (_err) {
        console.warn('Could not check authentication status.');
        return true;
    }
}

function switchAuthTab(tabName) {
    window.__gemsentryAuthTab = tabName === 'token' ? 'token' : 'credentials';
    if (typeof document === 'undefined') return;

    const tabCreds = document.getElementById('authTabCredentials');
    const tabToken = document.getElementById('authTabToken');
    const panelCreds = document.getElementById('authPanelCredentials');
    const panelToken = document.getElementById('authPanelToken');
    const feedback = document.getElementById('authFeedback');

    const isToken = window.__gemsentryAuthTab === 'token';
    if (tabCreds) tabCreds.classList.toggle('active', !isToken);
    if (tabToken) tabToken.classList.toggle('active', isToken);
    if (panelCreds) panelCreds.style.display = isToken ? 'none' : 'flex';
    if (panelToken) panelToken.style.display = isToken ? 'flex' : 'none';
    if (feedback) feedback.style.display = 'none';
}

function toggleAuthPasswordVisibility(inputId, btnId) {
    if (typeof document === 'undefined') return;
    const input = document.getElementById(inputId);
    const btn = document.getElementById(btnId);
    if (!input) return;
    const nextIsText = input.type === 'password';
    input.type = nextIsText ? 'text' : 'password';
    if (btn) {
        btn.textContent = nextIsText ? '🙈 Hide' : '👁️ Show';
        btn.setAttribute('aria-label', nextIsText ? 'Hide password' : 'Show password');
    }
}

function openAuthModal(forced = false) {
    if (forced) {
        window.__gemsentryAuthLocked = true;
    }
    if (typeof finishSplash === 'function') {
        try {
            finishSplash();
        } catch (_e) {}
    }
    if (typeof document === 'undefined') return;

    const splash = document.getElementById('splashOverlay');
    if (splash && splash.classList) {
        splash.classList.add('hide');
    }

    const modal = document.getElementById('authModal');
    if (!modal) return;
    modal.style.zIndex = '10050';

    const closeBtn = document.getElementById('authCloseBtn');
    const forgetBtn = document.getElementById('authForgetBtn');
    const tokenInput = document.getElementById('authTokenInput');
    const passInput = document.getElementById('authPasswordInput');
    const userInput = document.getElementById('authUsernameInput');
    const feedback = document.getElementById('authFeedback');

    // CRITICAL FIX: If #authModal is ALREADY open, return immediately without
    // clearing or overwriting what the user is typing or stealing focus!
    if (modal.style.display === 'flex') {
        if (forced && closeBtn) closeBtn.style.display = 'none';
        return;
    }

    if (feedback) feedback.style.display = 'none';
    // Never pre-fill localStorage garbage into password/token fields
    if (tokenInput) tokenInput.value = '';
    if (passInput) passInput.value = '';
    if (closeBtn) closeBtn.style.display = forced ? 'none' : 'block';

    const hasStored = (typeof localStorage !== 'undefined' && localStorage && typeof localStorage.getItem === 'function')
        ? Boolean(localStorage.getItem('gemsentry_auth_token'))
        : false;
    if (forgetBtn) forgetBtn.style.display = hasStored ? 'inline-flex' : 'none';

    modal.style.display = 'flex';

    const targetFocus = (window.__gemsentryAuthTab === 'token' || !userInput) ? tokenInput : userInput;
    if (targetFocus && typeof targetFocus.focus === 'function') {
        setTimeout(() => targetFocus.focus(), 80);
    }
}

function closeAuthModal() {
    if (typeof document === 'undefined') return;
    const modal = document.getElementById('authModal');
    if (modal) modal.style.display = 'none';
}

async function submitAuthToken(event) {
    if (event && typeof event.preventDefault === 'function') {
        event.preventDefault();
    }
    if (typeof document === 'undefined') return;

    const tokenInput = document.getElementById('authTokenInput');
    const userInput = document.getElementById('authUsernameInput');
    const passInput = document.getElementById('authPasswordInput');
    const feedback = document.getElementById('authFeedback');
    const submitBtn = document.getElementById('authSubmitBtn');

    const rawToken = (tokenInput ? tokenInput.value : '').trim();
    const rawUsername = (userInput ? userInput.value : '').trim();
    const rawPassword = passInput ? passInput.value : '';

    let payload = null;
    if (window.__gemsentryAuthTab === 'token' || (!rawUsername && rawToken)) {
        if (!rawToken) {
            if (feedback) {
                feedback.style.display = 'block';
                feedback.style.background = 'rgba(239, 68, 68, 0.15)';
                feedback.style.color = '#f87171';
                feedback.textContent = 'Please enter the master access key.';
            }
            return;
        }
        payload = { token: rawToken };
    } else {
        if (!rawUsername || !rawPassword) {
            if (feedback) {
                feedback.style.display = 'block';
                feedback.style.background = 'rgba(239, 68, 68, 0.15)';
                feedback.style.color = '#f87171';
                feedback.textContent = 'Please enter both username and password.';
            }
            return;
        }
        payload = { username: rawUsername, password: rawPassword };
    }

    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = 'Verifying...';
    }

    try {
        const res = await originalFetch('/api/auth/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok && data.valid) {
            const sessionTok = data.session_token || rawToken || '';
            if (sessionTok && typeof localStorage !== 'undefined' && localStorage && typeof localStorage.setItem === 'function') {
                localStorage.setItem('gemsentry_auth_token', sessionTok);
            }
            window.__gemsentryAuthLocked = false;
            window.__gemsentryCurrentUser = data.user || {
                username: rawUsername || 'etspl',
                display_name: (data.user && data.user.display_name) || rawUsername || 'ETSPL Admin',
                role: (data.user && data.user.role) || 'admin',
                company_id: (data.user && data.user.company_id) || 'etspl'
            };
            updateUserSessionUI(window.__gemsentryCurrentUser);

            if (passInput) passInput.value = '';
            if (tokenInput) tokenInput.value = '';

            if (feedback) {
                feedback.style.display = 'block';
                feedback.style.background = 'rgba(16, 185, 129, 0.15)';
                feedback.style.color = '#34d399';
                feedback.textContent = 'Access granted! Loading workspace...';
            }
            setTimeout(() => {
                closeAuthModal();
                if (typeof window.initializeAuthenticatedApp === 'function') {
                    window.initializeAuthenticatedApp(true);
                } else {
                    if (typeof refreshData === 'function') refreshData();
                    if (typeof loadKeywordsForModal === 'function') loadKeywordsForModal();
                    if (typeof loadPresets === 'function') loadPresets();
                    if (typeof loadScoringConfig === 'function') loadScoringConfig();
                    if (typeof loadCompanyProfile === 'function') loadCompanyProfile();
                }
            }, 250);
        } else {
            if (feedback) {
                feedback.style.display = 'block';
                feedback.style.background = 'rgba(239, 68, 68, 0.15)';
                feedback.style.color = '#f87171';
                feedback.textContent = data.error || 'Invalid credentials or access key.';
            }
        }
    } catch (_err) {
        if (feedback) {
            feedback.style.display = 'block';
            feedback.style.background = 'rgba(239, 68, 68, 0.15)';
            feedback.style.color = '#f87171';
            feedback.textContent = 'Connection error. Please try again.';
        }
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Sign In & Unlock Portal';
        }
    }
}

async function signOutUser() {
    try {
        await originalFetch('/api/auth/logout', { method: 'POST', headers: authHeaders() });
    } catch (_e) {}
    if (typeof localStorage !== 'undefined' && localStorage && typeof localStorage.removeItem === 'function') {
        localStorage.removeItem('gemsentry_auth_token');
    }
    window.__gemsentryCurrentUser = null;
    window.__gemsentryAuthLocked = true;
    if (window.CRMState && typeof window.CRMState.reset === 'function') {
        window.CRMState.reset();
    }
    updateUserSessionUI(null);
    openAuthModal(true);
}

function forgetAuthToken() {
    if (typeof localStorage !== 'undefined' && localStorage && typeof localStorage.removeItem === 'function') {
        localStorage.removeItem('gemsentry_auth_token');
    }
    originalFetch('/api/auth/logout', { method: 'POST', headers: authHeaders() })
        .catch(() => {});
    if (typeof document === 'undefined') return;
    const tokenInput = document.getElementById('authTokenInput');
    if (tokenInput) tokenInput.value = '';
    const passInput = document.getElementById('authPasswordInput');
    if (passInput) passInput.value = '';
    const feedback = document.getElementById('authFeedback');
    if (feedback) {
        feedback.style.display = 'block';
        feedback.style.background = 'rgba(239, 68, 68, 0.15)';
        feedback.style.color = '#f87171';
        feedback.textContent = 'Saved session removed.';
    }
    const forgetBtn = document.getElementById('authForgetBtn');
    if (forgetBtn) forgetBtn.style.display = 'none';
}

window.openAuthModal = openAuthModal;
window.closeAuthModal = closeAuthModal;
window.submitAuthToken = submitAuthToken;
window.switchAuthTab = switchAuthTab;
window.toggleAuthPasswordVisibility = toggleAuthPasswordVisibility;
window.signOutUser = signOutUser;
window.forgetAuthToken = forgetAuthToken;
window.checkAuthRequirement = checkAuthRequirement;
window.updateUserSessionUI = updateUserSessionUI;
