/* ==========================================================================
   GeMSentry CRM Safe DOM Builder, Toast/Dialog UI & API Client
   Security: Strictly uses createElement / textContent / replaceChildren.
   In-page Toast & Modal dialogs only.
   ========================================================================== */
(function () {
    'use strict';

    function crmClear(el) {
        if (!el) return;
        if (typeof el.replaceChildren === 'function') {
            el.replaceChildren();
        } else {
            while (el.firstChild) {
                el.removeChild(el.firstChild);
            }
        }
    }

    function crmEl(tag, attrs, children) {
        const node = document.createElement(tag);
        if (attrs && typeof attrs === 'object') {
            Object.entries(attrs).forEach(([key, val]) => {
                if (val === undefined || val === null || val === false) return;
                if (key === 'className' || key === 'class') {
                    node.className = String(val);
                } else if (key === 'textContent' || key === 'text') {
                    node.textContent = String(val);
                } else if (key === 'style' && typeof val === 'object') {
                    Object.entries(val).forEach(([sKey, sVal]) => {
                        if (sVal !== undefined && sVal !== null) {
                            node.style[sKey] = String(sVal);
                        }
                    });
                } else if (key === 'dataset' && typeof val === 'object') {
                    Object.entries(val).forEach(([dKey, dVal]) => {
                        if (dVal !== undefined && dVal !== null) {
                            node.dataset[dKey] = String(dVal);
                        }
                    });
                } else if (key.startsWith('on') && typeof val === 'function') {
                    node.addEventListener(key.slice(2).toLowerCase(), val);
                } else if (key === 'checked' || key === 'disabled' || key === 'selected' || key === 'required' || key === 'open' || key === 'multiple') {
                    node[key] = Boolean(val);
                } else if (key === 'value') {
                    node.value = String(val);
                } else {
                    node.setAttribute(key, String(val));
                }
            });
        }
        const appendChildItem = (child) => {
            if (child === undefined || child === null || child === false) return;
            if (Array.isArray(child)) {
                child.forEach(appendChildItem);
            } else if (child instanceof Node) {
                node.appendChild(child);
            } else {
                node.appendChild(document.createTextNode(String(child)));
            }
        };
        if (children !== undefined) {
            appendChildItem(children);
        }
        return node;
    }

    function crmSvg(svgMarkup) {
        if (typeof DOMParser === 'undefined') return document.createElement('span');
        const doc = new DOMParser().parseFromString(svgMarkup, 'image/svg+xml');
        const root = doc.documentElement;
        if (!root || root.nodeName === 'parsererror') {
            return document.createElement('span');
        }
        return document.importNode(root, true);
    }

    function ensureToastContainer() {
        let container = document.getElementById('crmToastContainer');
        if (!container) {
            container = crmEl('div', {
                id: 'crmToastContainer',
                className: 'crm-toast-container',
                role: 'status',
                'aria-live': 'polite'
            });
            document.body.appendChild(container);
        }
        return container;
    }

    function crmToast(message, type, durationMs) {
        if (typeof document === 'undefined' || !document.body) return;
        const kind = type || 'info';
        const timeout = durationMs || 4200;
        const container = ensureToastContainer();
        const closeBtn = crmEl('button', {
            type: 'button',
            style: {
                background: 'none',
                border: 'none',
                color: 'var(--text-secondary)',
                cursor: 'pointer',
                fontSize: '1rem',
                lineHeight: '1',
                padding: '0 0.2rem'
            },
            text: '×',
            onClick: () => toast.remove()
        });
        const toast = crmEl('div', {
            className: `crm-toast toast-${kind}`
        }, [
            crmEl('span', { text: String(message || '') }),
            closeBtn
        ]);
        container.appendChild(toast);
        setTimeout(() => {
            if (toast.parentNode) {
                toast.remove();
            }
        }, timeout);
    }

    function crmConfirm(options) {
        if (typeof document === 'undefined' || !document.body) {
            return Promise.resolve(true);
        }
        const opts = typeof options === 'string' ? { message: options } : (options || {});
        return new Promise((resolve) => {
            const backdrop = crmEl('div', { className: 'crm-dialog-backdrop' });
            const cleanup = (result) => {
                if (backdrop.parentNode) backdrop.remove();
                resolve(result);
            };
            const cancelBtn = crmEl('button', {
                type: 'button',
                className: 'btn btn-secondary',
                text: opts.cancelText || 'Cancel',
                onClick: () => cleanup(false)
            });
            const confirmBtn = crmEl('button', {
                type: 'button',
                className: 'btn btn-primary',
                style: opts.danger ? { background: 'var(--failed-color)', borderColor: 'var(--failed-color)', color: '#fff' } : {},
                text: opts.confirmText || 'Confirm',
                onClick: () => cleanup(true)
            });
            const box = crmEl('div', {
                className: 'crm-dialog-box',
                role: 'dialog',
                'aria-modal': 'true'
            }, [
                crmEl('h3', {
                    style: { fontSize: '1.02rem', fontWeight: '700', color: 'var(--text-primary)' },
                    text: opts.title || 'Confirm Action'
                }),
                crmEl('p', {
                    style: { fontSize: '0.86rem', color: 'var(--text-secondary)', lineHeight: '1.45', whiteSpace: 'pre-line' },
                    text: opts.message || 'Are you sure you want to proceed?'
                }),
                crmEl('div', {
                    style: { display: 'flex', justifyContent: 'flex-end', gap: '0.6rem', marginTop: '0.35rem' }
                }, [cancelBtn, confirmBtn])
            ]);
            backdrop.appendChild(box);
            document.body.appendChild(backdrop);
            confirmBtn.focus();
        });
    }

    function crmPrompt(options) {
        if (typeof document === 'undefined' || !document.body) {
            return Promise.resolve(null);
        }
        const opts = typeof options === 'string' ? { message: options } : (options || {});
        return new Promise((resolve) => {
            const backdrop = crmEl('div', { className: 'crm-dialog-backdrop' });
            const input = crmEl('input', {
                type: 'text',
                value: opts.defaultValue || '',
                placeholder: opts.placeholder || '',
                style: {
                    width: '100%',
                    padding: '0.55rem 0.75rem',
                    borderRadius: '7px',
                    border: '1px solid var(--border-strong)',
                    background: 'var(--input-bg)',
                    color: 'var(--text-primary)',
                    fontSize: '0.86rem'
                }
            });
            const cleanup = (val) => {
                if (backdrop.parentNode) backdrop.remove();
                resolve(val);
            };
            const form = crmEl('form', {
                style: { display: 'flex', flexDirection: 'column', gap: '0.85rem' },
                onSubmit: (e) => {
                    e.preventDefault();
                    const v = input.value.trim();
                    if (!v) return;
                    cleanup(v);
                }
            }, [
                crmEl('h3', {
                    style: { fontSize: '1.02rem', fontWeight: '700', color: 'var(--text-primary)' },
                    text: opts.title || 'Enter Value'
                }),
                opts.message ? crmEl('p', {
                    style: { fontSize: '0.84rem', color: 'var(--text-secondary)' },
                    text: opts.message
                }) : null,
                input,
                crmEl('div', {
                    style: { display: 'flex', justifyContent: 'flex-end', gap: '0.6rem' }
                }, [
                    crmEl('button', {
                        type: 'button',
                        className: 'btn btn-secondary',
                        text: opts.cancelText || 'Cancel',
                        onClick: () => cleanup(null)
                    }),
                    crmEl('button', {
                        type: 'submit',
                        className: 'btn btn-primary',
                        text: opts.confirmText || 'Create'
                    })
                ])
            ]);
            const box = crmEl('div', { className: 'crm-dialog-box' }, [form]);
            backdrop.appendChild(box);
            document.body.appendChild(backdrop);
            setTimeout(() => input.focus(), 30);
        });
    }

    async function requestJson(url, options) {
        const opts = Object.assign({}, options || {});
        const headers = Object.assign({}, opts.headers || {});
        if (opts.body && !(opts.body instanceof FormData) && typeof opts.body === 'object') {
            headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(opts.body);
        }
        opts.headers = headers;
        const res = await window.fetch(url, opts);
        let data = {};
        try {
            data = await res.json();
        } catch (_e) {
            data = {};
        }
        if (!res.ok) {
            const errMsg = data.error || data.message || `Request failed (${res.status})`;
            const err = new Error(errMsg);
            err.status = res.status;
            err.payload = data;
            throw err;
        }
        return data;
    }

    const CRMApi = {
        getBootstrap() {
            return requestJson('/api/crm/bootstrap');
        },
        upsertCompany(payload) {
            return requestJson('/api/crm/companies', { method: 'POST', body: payload });
        },
        createSubfolder(payload) {
            return requestJson('/api/crm/subfolders', { method: 'POST', body: payload });
        },
        listTenders(params) {
            const qs = new URLSearchParams();
            Object.entries(params || {}).forEach(([k, v]) => {
                if (v !== undefined && v !== null && String(v).trim() !== '') {
                    qs.set(k, String(v).trim());
                }
            });
            const suffix = qs.toString() ? `?${qs.toString()}` : '';
            return requestJson(`/api/crm/tenders${suffix}`);
        },
        getTenderPreview(bidNo) {
            const encoded = encodeURIComponent(String(bidNo || '').trim());
            return requestJson(`/api/crm/tender-preview/${encoded}`);
        },
        moveTenders(payload) {
            return requestJson('/api/crm/move', { method: 'POST', body: payload });
        },
        fetchAnnexures(crmTenderId) {
            return requestJson(`/api/crm/tenders/${encodeURIComponent(crmTenderId)}/fetch-annexures`, {
                method: 'POST',
                body: {}
            });
        },
        uploadTenderFile(crmTenderId, file, fileKind) {
            const fd = new FormData();
            fd.append('file', file);
            fd.append('file_kind', fileKind || 'tech_spec');
            return requestJson(`/api/crm/tenders/${encodeURIComponent(crmTenderId)}/upload`, {
                method: 'POST',
                body: fd
            });
        },
        updateDecision(crmTenderId, payload) {
            return requestJson(`/api/crm/tenders/${encodeURIComponent(crmTenderId)}/decision`, {
                method: 'POST',
                body: payload
            });
        },
        removeTender(crmTenderId) {
            return requestJson(`/api/crm/tenders/${encodeURIComponent(crmTenderId)}/remove`, {
                method: 'POST',
                body: {}
            });
        },
        getCsvPreview(fileId) {
            return requestJson(`/api/crm/files/${encodeURIComponent(fileId)}/csv-preview`);
        },
        async downloadCompanyExcel(companyId, subfolderId) {
            const qs = subfolderId ? `?subfolder_id=${encodeURIComponent(subfolderId)}` : '';
            const url = `/api/crm/export/${encodeURIComponent(companyId)}.xlsx${qs}`;
            const res = await window.fetch(url);
            if (!res.ok) {
                throw new Error(`Failed to export Excel (${res.status})`);
            }
            const blob = await res.blob();
            const objUrl = URL.createObjectURL(blob);
            const a = crmEl('a', {
                href: objUrl,
                download: `${companyId}_crm_tenders_${new Date().toISOString().slice(0, 10)}.xlsx`
            });
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(objUrl);
        },
        listUsers() {
            return requestJson('/api/crm/users');
        },
        upsertUser(payload) {
            return requestJson('/api/crm/users', { method: 'POST', body: payload });
        },
        migrateFolders(force) {
            return requestJson('/api/crm/migrate-folders', {
                method: 'POST',
                body: { force: force !== false }
            });
        }
    };

    window.crmClear = crmClear;
    window.crmEl = crmEl;
    window.crmSvg = crmSvg;
    window.crmToast = crmToast;
    window.crmConfirm = crmConfirm;
    window.crmPrompt = crmPrompt;
    window.CRMApi = CRMApi;
})();
