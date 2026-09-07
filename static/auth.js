// Global Fetch Interceptor for Token Authentication
        const originalFetch = window.fetch.bind(window);
        window.fetch = async function(url, options = {}) {
            options = {...(options || {})};
            const target = new URL(url instanceof Request ? url.url : url, window.location.href);
            const sameOrigin = target.origin === window.location.origin;
            options.headers = new Headers(options.headers || (url instanceof Request ? url.headers : undefined));
            const token = localStorage.getItem('gemsentry_auth_token');
            if (token && sameOrigin) options.headers.set('Authorization', `Bearer ${token}`);
            const res = await originalFetch(url, options);
            if (sameOrigin && res.status === 401 && !target.pathname.startsWith('/api/auth/')) {
                openAuthModal(true);
            }
            return res;
        };

        function authHeaders() {
            const token = localStorage.getItem('gemsentry_auth_token');
            return token ? {'Authorization': `Bearer ${token}`} : {};
        }

        async function downloadSummaryExcel() {
            // Fetched with the bearer header rather than ?token=, which would
            // leak the access key into server logs, browser history and any
            // outgoing Referer.
            try {
                const res = await originalFetch('/api/export/summary.xlsx', {headers: authHeaders()});
                if (!res.ok) {
                    if (res.status === 401) openAuthModal(true);
                    return;
                }
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `tender_summary_${new Date().toISOString().slice(0, 10)}.xlsx`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                URL.revokeObjectURL(url);
            } catch (err) {
                console.error('Summary download failed:', err);
            }
        }

        async function checkAuthRequirement() {
            try {
                const res = await originalFetch('/api/auth/status');
                const data = await res.json();
                const authBtn = document.getElementById('authKeyBtn');
                if (data.auth_required) {
                    if (authBtn) authBtn.style.display = 'flex';
                    const stored = localStorage.getItem('gemsentry_auth_token');
                    if (!stored) {
                        openAuthModal(true);
                    } else {
                        const verifyRes = await originalFetch('/api/auth/verify', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({token: stored})
                        });
                        if (!verifyRes.ok) {
                            openAuthModal(true);
                        }
                    }
                } else {
                    if (authBtn) authBtn.style.display = 'none';
                }
            } catch (err) {
                console.warn("Could not check auth status:", err);
            }
        }

        function openAuthModal(forced = false) {
            const modal = document.getElementById('authModal');
            const closeBtn = document.getElementById('authCloseBtn');
            const forgetBtn = document.getElementById('authForgetBtn');
            const input = document.getElementById('authTokenInput');
            const feedback = document.getElementById('authFeedback');
            if (!modal) return;
            if (feedback) feedback.style.display = 'none';
            if (input) {
                input.value = localStorage.getItem('gemsentry_auth_token') || '';
            }
            if (closeBtn) closeBtn.style.display = forced ? 'none' : 'block';
            if (forgetBtn) forgetBtn.style.display = localStorage.getItem('gemsentry_auth_token') ? 'inline-flex' : 'none';
            modal.style.display = 'flex';
            if (input) setTimeout(() => input.focus(), 100);
        }

        function closeAuthModal() {
            const modal = document.getElementById('authModal');
            if (modal) modal.style.display = 'none';
        }

        async function submitAuthToken(event) {
            event.preventDefault();
            const input = document.getElementById('authTokenInput');
            const feedback = document.getElementById('authFeedback');
            const submitBtn = document.getElementById('authSubmitBtn');
            const token = (input ? input.value : '').trim();
            if (!token) return;

            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.textContent = 'Verifying...';
            }

            try {
                const res = await originalFetch('/api/auth/verify', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({token: token})
                });
                const data = await res.json();
                if (res.ok && data.valid) {
                    localStorage.setItem('gemsentry_auth_token', token);
                    // The navigation cookie is set HttpOnly by /api/auth/verify;
                    // the page must not mint a script-readable duplicate.
                    if (feedback) {
                        feedback.style.display = 'block';
                        feedback.style.background = 'rgba(16, 185, 129, 0.15)';
                        feedback.style.color = '#34d399';
                        feedback.textContent = 'Access granted! Loading dashboard...';
                    }
                    setTimeout(() => {
                        closeAuthModal();
                        refreshData();
                        loadKeywordsForModal();
                        loadPresets();
                        loadScoringConfig();
                        loadCompanyProfile();
                    }, 400);
                } else {
                    if (feedback) {
                        feedback.style.display = 'block';
                        feedback.style.background = 'rgba(239, 68, 68, 0.15)';
                        feedback.style.color = '#f87171';
                        feedback.textContent = data.error || 'Invalid access key.';
                    }
                }
            } catch (err) {
                if (feedback) {
                    feedback.style.display = 'block';
                    feedback.style.background = 'rgba(239, 68, 68, 0.15)';
                    feedback.style.color = '#f87171';
                    feedback.textContent = 'Connection error. Please try again.';
                }
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Unlock Portal';
                }
            }
        }

        function forgetAuthToken() {
            localStorage.removeItem('gemsentry_auth_token');
            originalFetch('/api/auth/logout', {method: 'POST', headers: authHeaders()})
                .catch(() => {});
            const input = document.getElementById('authTokenInput');
            if (input) input.value = '';
            const feedback = document.getElementById('authFeedback');
            if (feedback) {
                feedback.style.display = 'block';
                feedback.style.background = 'rgba(239, 68, 68, 0.15)';
                feedback.style.color = '#f87171';
                feedback.textContent = 'Saved access key removed.';
            }
            const forgetBtn = document.getElementById('authForgetBtn');
            if (forgetBtn) forgetBtn.style.display = 'none';
        }
