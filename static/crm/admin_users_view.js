/* ==========================================================================
   GeMSentry CRM Admin View — Companies, Vendor Sheets, Users, Roles & Cleanup
   Security: Strictly uses crmEl / crmClear / textContent.
   ========================================================================== */
(function () {
    'use strict';

    const el = window.crmEl;
    const clear = window.crmClear;

    const CRMAdminView = {
        async activate() {
            const state = window.CRMState;
            if (!state.isAdmin()) {
                state.switchMode('folders');
                return;
            }
            await Promise.all([
                state.loadBootstrap(),
                this.loadUsers()
            ]);
            this.render();
        },

        async loadUsers() {
            try {
                const res = await window.CRMApi.listUsers();
                window.CRMState.admin.users = res.users || (Array.isArray(res) ? res : []);
            } catch (err) {
                window.crmToast(err.message || 'Could not load users', 'error');
            }
        },

        render() {
            const root = document.getElementById('crmAdminWorkspace');
            if (!root) return;
            clear(root);

            const state = window.CRMState;

            // 1. Top Banner with Legacy Disk Folder Migration & Cleanup
            const migrateBtn = el('button', {
                type: 'button',
                className: 'btn btn-primary',
                style: { padding: '0.55rem 1.1rem', fontSize: '0.84rem' },
                text: '🔄 Scan & Clean Legacy Disk Folders',
                onClick: (e) => this.handleMigrateFolders(e.currentTarget)
            });

            const topBanner = el('div', {
                className: 'crm-admin-card',
                style: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap' }
            }, [
                el('div', { style: { display: 'flex', flexDirection: 'column', gap: '0.25rem' } }, [
                    el('h2', { style: { fontSize: '1.15rem', fontWeight: '700' }, text: '👥 Companies, Vendor Sheets, Users & Disk Hygiene' }),
                    el('p', { style: { fontSize: '0.82rem', color: 'var(--text-secondary)' }, text: 'Manage vendor companies, Google Sheet mappings, RBAC user credentials, and automatic disk folder deduplication.' })
                ]),
                migrateBtn
            ]);
            root.appendChild(topBanner);

            // Migration report banner if available
            if (state.admin.migrationReport) {
                const rep = state.admin.migrationReport;
                root.appendChild(
                    el('div', {
                        className: 'crm-admin-card',
                        style: { marginTop: '0.85rem', borderColor: 'var(--success-border)', backgroundColor: 'var(--success-bg)' }
                    }, [
                        el('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } }, [
                            el('strong', { style: { color: 'var(--success-color)', fontSize: '0.88rem' }, text: '✓ Folder Cleanup & CRM Import Report' }),
                            el('button', {
                                type: 'button',
                                className: 'btn-crm-toolbar',
                                text: 'Dismiss',
                                onClick: () => {
                                    state.admin.migrationReport = null;
                                    this.render();
                                }
                            })
                        ]),
                        el('p', {
                            style: { fontSize: '0.8rem', color: 'var(--text-primary)' },
                            text: JSON.stringify(rep)
                        })
                    ])
                );
            }

            // 2. Two-column grid: Companies on Left, Users on Right
            const grid = el('div', { className: 'crm-admin-grid' }, [
                this.buildCompaniesCard(),
                this.buildUsersCard()
            ]);
            root.appendChild(grid);
        },

        buildCompaniesCard() {
            const state = window.CRMState;
            const editing = state.admin.editingCompany || {
                id: '',
                name: '',
                disk_folder: '',
                type: 'vendor',
                category: '',
                google_sheet_url: '',
                color: '#3B82F6',
                confidential_mode: 1,
                auto_sync_master: 1
            };

            const idInput = el('input', { type: 'text', value: editing.id || '', placeholder: 'e.g. hanmars, rajiv_mital (auto if blank)' });
            const nameInput = el('input', { type: 'text', value: editing.name || '', required: true, placeholder: 'e.g. Hanmars Systems' });
            const folderInput = el('input', { type: 'text', value: editing.disk_folder || '', placeholder: 'e.g. hanmars' });
            const typeSelect = el('select', {}, [
                el('option', { value: 'vendor', selected: editing.type === 'vendor', text: 'Vendor (Confidential Scope)' }),
                el('option', { value: 'admin', selected: editing.type === 'admin', text: 'Admin / Core Team (ETSPL)' }),
                el('option', { value: 'internal', selected: editing.type === 'internal', text: 'Internal Division (Small / Misc)' })
            ]);
            const categoryInput = el('input', { type: 'text', value: editing.category || '', placeholder: 'e.g. Drone / UAV, Biometrics, Power Supply' });
            const colorInput = el('input', { type: 'text', value: editing.color || '#3B82F6', placeholder: '#10B981' });
            const sheetInput = el('input', { type: 'text', value: editing.google_sheet_url || editing.google_sheet_id || '', placeholder: 'https://docs.google.com/spreadsheets/d/...' });
            const confCheck = el('input', { type: 'checkbox', checked: Boolean(editing.confidential_mode) });
            const syncCheck = el('input', { type: 'checkbox', checked: editing.auto_sync_master !== 0 && editing.auto_sync_master !== false });

            const form = el('form', {
                onSubmit: async (e) => {
                    e.preventDefault();
                    const payload = {
                        id: idInput.value.trim() || undefined,
                        name: nameInput.value.trim(),
                        disk_folder: folderInput.value.trim() || nameInput.value.trim().toLowerCase(),
                        type: typeSelect.value,
                        category: categoryInput.value.trim(),
                        color: colorInput.value.trim() || '#3B82F6',
                        google_sheet_url: sheetInput.value.trim(),
                        google_sheet_id: sheetInput.value.trim(),
                        confidential_mode: confCheck.checked ? 1 : 0,
                        auto_sync_master: syncCheck.checked ? 1 : 0
                    };
                    try {
                        await window.CRMApi.upsertCompany(payload);
                        state.admin.editingCompany = null;
                        await state.loadBootstrap();
                        window.crmToast(`Saved company "${payload.name}".`, 'success');
                        this.render();
                    } catch (err) {
                        window.crmToast(err.message || 'Failed to save company', 'error');
                    }
                }
            }, [
                el('h4', { style: { fontSize: '0.88rem', marginBottom: '0.6rem', color: 'var(--primary-accent)' }, text: state.admin.editingCompany ? `✏️ Edit Company: ${editing.name}` : '➕ Add / Update Company & Vendor Sheet' }),
                el('div', { className: 'crm-form-grid' }, [
                    el('div', { className: 'crm-form-field' }, [el('label', { text: 'Company Name *' }), nameInput]),
                    el('div', { className: 'crm-form-field' }, [el('label', { text: 'Company ID / Slug' }), idInput]),
                    el('div', { className: 'crm-form-field' }, [el('label', { text: 'Disk Folder (under tenders/)' }), folderInput]),
                    el('div', { className: 'crm-form-field' }, [el('label', { text: 'Company Type' }), typeSelect]),
                    el('div', { className: 'crm-form-field' }, [el('label', { text: 'Category / Domain' }), categoryInput]),
                    el('div', { className: 'crm-form-field' }, [el('label', { text: 'Badge Color (Hex)' }), colorInput]),
                    el('div', { className: 'crm-form-field span-2' }, [el('label', { text: 'Dedicated Vendor Google Sheet URL or ID' }), sheetInput]),
                    el('div', { className: 'crm-form-field span-2', style: { flexDirection: 'row', gap: '1.25rem', alignItems: 'center', marginTop: '0.2rem' } }, [
                        el('label', { style: { display: 'inline-flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer', textTransform: 'none', fontSize: '0.8rem' } }, [
                            confCheck,
                            el('span', { text: 'Confidential Mode (Hide EMD & Est. Value from Vendor)' })
                        ]),
                        el('label', { style: { display: 'inline-flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer', textTransform: 'none', fontSize: '0.8rem' } }, [
                            syncCheck,
                            el('span', { text: 'Auto-sync Master & Sheet on Move' })
                        ])
                    ])
                ]),
                el('div', { style: { display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '0.75rem' } }, [
                    state.admin.editingCompany ? el('button', {
                        type: 'button',
                        className: 'btn btn-secondary',
                        style: { padding: '0.4rem 0.85rem', fontSize: '0.78rem' },
                        text: 'Cancel Edit',
                        onClick: () => {
                            state.admin.editingCompany = null;
                            this.render();
                        }
                    }) : null,
                    el('button', {
                        type: 'submit',
                        className: 'btn btn-primary',
                        style: { padding: '0.45rem 1rem', fontSize: '0.8rem' },
                        text: '💾 Save Company'
                    })
                ])
            ]);

            const tableRows = (state.companies || []).map((c) =>
                el('tr', {}, [
                    el('td', {}, [
                        el('div', { style: { display: 'inline-flex', alignItems: 'center', gap: '0.45rem', fontWeight: '700' } }, [
                            el('span', { className: 'crm-company-swatch', style: { backgroundColor: c.color || '#10B981' } }),
                            el('span', { text: c.name })
                        ])
                    ]),
                    el('td', { style: { fontFamily: 'var(--font-mono)', fontSize: '0.75rem' }, text: `tenders/${c.disk_folder || c.id}` }),
                    el('td', {}, [el('span', { className: 'crm-pill crm-pill-neutral', text: c.type || 'vendor' })]),
                    el('td', {}, [
                        el('span', {
                            className: `crm-pill ${c.confidential_mode ? 'crm-pill-review' : 'crm-pill-neutral'}`,
                            text: c.confidential_mode ? '🔒 Confidential' : 'Full View'
                        })
                    ]),
                    el('td', {}, [
                        el('button', {
                            type: 'button',
                            className: 'btn-crm-toolbar',
                            style: { padding: '0.22rem 0.5rem', fontSize: '0.73rem' },
                            text: '✏️ Edit',
                            onClick: () => {
                                state.admin.editingCompany = Object.assign({}, c);
                                this.render();
                            }
                        })
                    ])
                ])
            );

            const table = el('table', { className: 'crm-admin-table' }, [
                el('thead', {}, [
                    el('tr', {}, [
                        el('th', { text: 'Company' }),
                        el('th', { text: 'Disk Folder' }),
                        el('th', { text: 'Type' }),
                        el('th', { text: 'Privacy' }),
                        el('th', { text: 'Action' })
                    ])
                ]),
                el('tbody', {}, tableRows)
            ]);

            return el('div', { className: 'crm-admin-card' }, [
                el('h3', { style: { fontSize: '1rem', fontWeight: '700' }, text: '🏢 Companies & Vendor Sheets' }),
                el('div', { style: { overflowX: 'auto' } }, [table]),
                el('div', { style: { borderTop: '1px solid var(--border-color)', paddingTop: '0.85rem' } }, [form])
            ]);
        },

        buildUsersCard() {
            const state = window.CRMState;
            const users = state.admin.users || [];
            const companies = state.companies || [];
            const editing = state.admin.editingUser || {
                id: null,
                username: '',
                display_name: '',
                role: 'vendor',
                company_id: companies[0] ? companies[0].id : 'etspl',
                subfolder_scope: '',
                is_active: 1
            };

            const usernameInput = el('input', {
                type: 'text',
                value: editing.username || '',
                required: true,
                placeholder: 'e.g. hanmars, sandeep, rajiv_mital'
            });
            const displayInput = el('input', {
                type: 'text',
                value: editing.display_name || '',
                placeholder: 'e.g. Sandeep (Hanmars)'
            });
            const roleSelect = el('select', {}, [
                el('option', { value: 'vendor', selected: editing.role === 'vendor', text: 'vendor (Scoped to Own Company)' }),
                el('option', { value: 'member', selected: editing.role === 'member', text: 'member (Internal Team Member)' }),
                el('option', { value: 'admin', selected: editing.role === 'admin', text: 'admin (Full Portal & CRM Access)' })
            ]);
            const compSelect = el('select', {}, companies.map((c) =>
                el('option', { value: c.id, selected: c.id === editing.company_id, text: `${c.name} (${c.id})` })
            ));
            const scopeInput = el('input', {
                type: 'text',
                value: editing.subfolder_scope || '',
                placeholder: 'Optional subfolder slug (e.g. sandeep, mangesh)'
            });

            const passInput = el('input', {
                type: 'password',
                placeholder: editing.id ? 'Leave blank to keep existing, or enter new password (min 8 chars)' : 'Set password (min 8 chars)',
                autocomplete: 'new-password'
            });
            const eyeBtn = el('button', {
                type: 'button',
                className: 'password-eye-btn',
                text: '👁️ Show',
                onClick: () => {
                    const isPw = passInput.type === 'password';
                    passInput.type = isPw ? 'text' : 'password';
                    eyeBtn.textContent = isPw ? '🙈 Hide' : '👁️ Show';
                }
            });
            const activeCheck = el('input', {
                type: 'checkbox',
                checked: editing.is_active !== 0 && editing.is_active !== false
            });

            const form = el('form', {
                onSubmit: async (e) => {
                    e.preventDefault();
                    const payload = {
                        id: editing.id || undefined,
                        username: usernameInput.value.trim(),
                        display_name: displayInput.value.trim() || usernameInput.value.trim(),
                        role: roleSelect.value,
                        company_id: compSelect.value,
                        subfolder_scope: scopeInput.value.trim() || null,
                        is_active: activeCheck.checked ? 1 : 0
                    };
                    const pw = passInput.value;
                    if (pw && pw.trim()) {
                        payload.password = pw;
                    }
                    try {
                        await window.CRMApi.upsertUser(payload);
                        state.admin.editingUser = null;
                        await this.loadUsers();
                        window.crmToast(`Saved user "${payload.username}".`, 'success');
                        this.render();
                    } catch (err) {
                        window.crmToast(err.message || 'Failed to save user', 'error');
                    }
                }
            }, [
                el('h4', { style: { fontSize: '0.88rem', marginBottom: '0.6rem', color: 'var(--primary-accent)' }, text: editing.id ? `🔑 Edit User / Reset Password: ${editing.username}` : '➕ Create User / Set Password' }),
                el('div', { className: 'crm-form-grid' }, [
                    el('div', { className: 'crm-form-field' }, [el('label', { text: 'Username *' }), usernameInput]),
                    el('div', { className: 'crm-form-field' }, [el('label', { text: 'Display Name' }), displayInput]),
                    el('div', { className: 'crm-form-field' }, [el('label', { text: 'Role' }), roleSelect]),
                    el('div', { className: 'crm-form-field' }, [el('label', { text: 'Assigned Company' }), compSelect]),
                    el('div', { className: 'crm-form-field span-2' }, [el('label', { text: 'Optional Subfolder Scope (Restrict to 1 Subfolder)' }), scopeInput]),
                    el('div', { className: 'crm-form-field span-2' }, [
                        el('label', { text: 'Password (8+ characters, Scrypt Hashed)' }),
                        el('div', { className: 'password-input-wrap' }, [passInput, eyeBtn])
                    ]),
                    el('div', { className: 'crm-form-field span-2' }, [
                        el('label', { style: { display: 'inline-flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer', textTransform: 'none', fontSize: '0.8rem' } }, [
                            activeCheck,
                            el('span', { text: 'Account Active (Uncheck to revoke all active sessions immediately)' })
                        ])
                    ])
                ]),
                el('div', { style: { display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '0.75rem' } }, [
                    state.admin.editingUser ? el('button', {
                        type: 'button',
                        className: 'btn btn-secondary',
                        style: { padding: '0.4rem 0.85rem', fontSize: '0.78rem' },
                        text: 'Clear / New',
                        onClick: () => {
                            state.admin.editingUser = null;
                            this.render();
                        }
                    }) : null,
                    el('button', {
                        type: 'submit',
                        className: 'btn btn-primary',
                        style: { padding: '0.45rem 1rem', fontSize: '0.8rem' },
                        text: '💾 Save User & Password'
                    })
                ])
            ]);

            const userRows = users.map((u) =>
                el('tr', {}, [
                    el('td', { style: { fontFamily: 'var(--font-mono)', fontWeight: '700' }, text: u.username }),
                    el('td', {}, [
                        el('span', {
                            className: `user-role-pill role-${u.role || 'vendor'}`,
                            text: u.role || 'vendor'
                        })
                    ]),
                    el('td', { text: `${u.company_name || u.company_id || '-'}${u.subfolder_scope ? ' / ' + u.subfolder_scope : ''}` }),
                    el('td', {}, [
                        el('span', {
                            className: `crm-pill ${u.has_password ? 'crm-pill-pursue' : 'crm-pill-review'}`,
                            text: u.has_password ? '🔒 Set' : '⚠️ Unset'
                        })
                    ]),
                    el('td', {}, [
                        el('span', {
                            className: `crm-pill ${u.is_active !== 0 && u.is_active !== false ? 'crm-pill-pursue' : 'crm-pill-drop'}`,
                            text: u.is_active !== 0 && u.is_active !== false ? 'Active' : 'Disabled'
                        })
                    ]),
                    el('td', {}, [
                        el('button', {
                            type: 'button',
                            className: 'btn-crm-toolbar',
                            style: { padding: '0.22rem 0.5rem', fontSize: '0.73rem' },
                            text: '🔑 Edit / Password',
                            onClick: () => {
                                state.admin.editingUser = Object.assign({}, u);
                                this.render();
                            }
                        })
                    ])
                ])
            );

            const table = el('table', { className: 'crm-admin-table' }, [
                el('thead', {}, [
                    el('tr', {}, [
                        el('th', { text: 'Username' }),
                        el('th', { text: 'Role' }),
                        el('th', { text: 'Company / Scope' }),
                        el('th', { text: 'Password' }),
                        el('th', { text: 'Status' }),
                        el('th', { text: 'Action' })
                    ])
                ]),
                el('tbody', {}, userRows)
            ]);

            return el('div', { className: 'crm-admin-card' }, [
                el('h3', { style: { fontSize: '1rem', fontWeight: '700' }, text: '🔑 Users, Roles & Passwords' }),
                el('div', { style: { overflowX: 'auto' } }, [table]),
                el('div', { style: { borderTop: '1px solid var(--border-color)', paddingTop: '0.85rem' } }, [form])
            ]);
        },

        async handleMigrateFolders(btnEl) {
            const sure = await window.crmConfirm({
                title: 'Scan & Clean Legacy Disk Folders',
                message: 'This will:\n1. Back up all company folders to tenders/backups/\n2. Deduplicate copy-pasted folder trees and (1) duplicate PDFs\n3. Resolve "New folder" bundles into clean per-bid dossiers\n4. Import all dossiers and files into the CRM database.\n\nProceed now?',
                confirmText: 'Backup & Clean Folders'
            });
            if (!sure) return;

            const origText = btnEl ? btnEl.textContent : '';
            if (btnEl) {
                btnEl.disabled = true;
                btnEl.textContent = '⏳ Backing up & cleaning folders...';
            }

            try {
                const res = await window.CRMApi.migrateFolders(true);
                window.CRMState.admin.migrationReport = res.migration || res.report || res;
                await window.CRMState.loadBootstrap();
                window.crmToast('Disk folders backed up, cleaned, and imported into CRM!', 'success');
                this.render();
            } catch (err) {
                window.crmToast(err.message || 'Folder migration failed', 'error');
            } finally {
                if (btnEl) {
                    btnEl.disabled = false;
                    btnEl.textContent = origText;
                }
            }
        }
    };

    window.CRMAdminView = CRMAdminView;
})();
