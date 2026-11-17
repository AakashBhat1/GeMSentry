/* ==========================================================================
   GeMSentry CRM Companies & Folders 3-Column Explorer
   Left: Company & Subfolder Tree + New Subfolder + Export Excel (.xlsx)
   Right: Searchable Dossier List + Stage/Decision Filters + Sync Badges
   Clicking a dossier opens the full-screen Tender Page (tender_page.js).
   Security: Strictly uses crmEl / crmClear / textContent.
   ========================================================================== */
(function () {
    'use strict';

    const el = window.crmEl;
    const clear = window.crmClear;

    const CRMFoldersView = {
        initialized: false,
        searchTimer: null,

        async activate() {
            const state = window.CRMState;
            if (!state.initialized) {
                await state.loadBootstrap();
            }
            this.renderShell();
            await this.loadDossiers();
        },

        renderShell() {
            const root = document.getElementById('crmFoldersWorkspace');
            if (!root) return;

            let layout = document.getElementById('crmFoldersLayout');
            if (!layout || !root.contains(layout)) {
                clear(root);
                const treePane = el('div', { id: 'crmFoldersTreePane', className: 'crm-folders-tree-pane' });
                const listPane = el('div', { id: 'crmFoldersListPane', className: 'crm-folders-list-pane' });
                layout = el('div', { id: 'crmFoldersLayout', className: 'crm-folders-layout' }, [
                    treePane,
                    listPane
                ]);
                root.appendChild(layout);
            }

            this.renderTreePane();
            this.renderListPaneHeader();
        },

        renderTreePane() {
            const treePane = document.getElementById('crmFoldersTreePane');
            if (!treePane) return;
            clear(treePane);

            const state = window.CRMState;
            const isAdmin = state.isAdmin();
            const isVendor = state.isVendor();
            const user = state.currentUser || window.__gemsentryCurrentUser || {};

            // Scope visible companies for non-admin
            let visibleCompanies = state.companies || [];
            if (!isAdmin && user.company_id) {
                visibleCompanies = visibleCompanies.filter((c) => c.id === user.company_id);
            }

            if (visibleCompanies.length > 0 && (!state.folders.selectedCompanyId || !visibleCompanies.some((c) => c.id === state.folders.selectedCompanyId))) {
                state.folders.selectedCompanyId = visibleCompanies[0].id;
                state.folders.selectedSubfolderId = null;
            }

            const header = el('div', { className: 'crm-pane-header' }, [
                el('h3', { text: isVendor ? '🏢 Your Company Folders' : '🗂️ Companies & Folders' }),
                isAdmin ? el('button', {
                    type: 'button',
                    className: 'btn-crm-toolbar',
                    style: { padding: '0.25rem 0.55rem', fontSize: '0.74rem' },
                    text: '+ Company',
                    onClick: () => state.switchMode('admin')
                }) : null
            ]);
            treePane.appendChild(header);

            const treeContainer = el('div', { className: 'crm-company-tree' });
            treePane.appendChild(treeContainer);

            visibleCompanies.forEach((comp) => {
                const isCompActive = state.folders.selectedCompanyId === comp.id;
                const compCount = state.getCompanyCount(comp.id);
                const subfolders = state.getSubfoldersForCompany(comp.id);

                const compBtn = el('button', {
                    type: 'button',
                    className: 'crm-company-btn',
                    onClick: () => {
                        state.folders.selectedCompanyId = comp.id;
                        state.folders.selectedSubfolderId = null;
                        state.folders.selectedSubfolderSlug = null;
                        this.renderTreePane();
                        this.loadDossiers();
                    }
                }, [
                    el('div', { style: { display: 'flex', alignItems: 'center', gap: '0.5rem', minWidth: '0' } }, [
                        el('span', { className: 'crm-company-swatch', style: { backgroundColor: comp.color || '#10B981' } }),
                        el('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, text: comp.name })
                    ]),
                    el('span', { className: 'badge-count', text: String(compCount) })
                ]);

                const groupChildren = [compBtn];

                if (isCompActive) {
                    const sfListNodes = [];
                    if (!user.subfolder_scope) {
                        sfListNodes.push(
                            el('button', {
                                type: 'button',
                                className: `crm-subfolder-btn ${!state.folders.selectedSubfolderId ? 'active' : ''}`,
                                onClick: () => {
                                    state.folders.selectedSubfolderId = null;
                                    state.folders.selectedSubfolderSlug = null;
                                    this.renderTreePane();
                                    this.loadDossiers();
                                }
                            }, [
                                el('span', { text: '📁 All Subfolders' }),
                                el('span', { className: 'badge-count', text: String(compCount) })
                            ])
                        );
                    }

                    subfolders.forEach((sf) => {
                        const isSfActive = String(state.folders.selectedSubfolderId) === String(sf.id);
                        const sfCount = state.getSubfolderCount(sf.id);
                        sfListNodes.push(
                            el('button', {
                                type: 'button',
                                className: `crm-subfolder-btn ${isSfActive ? 'active' : ''}`,
                                onClick: () => {
                                    state.folders.selectedSubfolderId = sf.id;
                                    state.folders.selectedSubfolderSlug = sf.slug;
                                    this.renderTreePane();
                                    this.loadDossiers();
                                }
                            }, [
                                el('span', { text: `📂 ${sf.name}` }),
                                el('span', { className: 'badge-count', text: String(sfCount) })
                            ])
                        );
                    });

                    groupChildren.push(el('div', { className: 'crm-subfolder-list' }, sfListNodes));
                }

                treeContainer.appendChild(
                    el('div', { className: `crm-company-group ${isCompActive ? 'active' : ''}` }, groupChildren)
                );
            });

            // Footer Actions for Selected Company
            const selectedCompany = state.getCompanyById(state.folders.selectedCompanyId);
            if (selectedCompany) {
                const footerBtns = [];
                if (isAdmin) {
                    footerBtns.push(
                        el('button', {
                            type: 'button',
                            className: 'btn btn-secondary',
                            style: { width: '100%', justifyContent: 'center', padding: '0.45rem', fontSize: '0.78rem' },
                            text: '➕ New Subfolder',
                            onClick: () => this.promptNewSubfolder(selectedCompany.id)
                        })
                    );
                }

                footerBtns.push(
                    el('button', {
                        type: 'button',
                        className: 'btn btn-primary',
                        style: { width: '100%', justifyContent: 'center', padding: '0.48rem', fontSize: '0.78rem' },
                        text: '📊 Export Folder Excel (.xlsx)',
                        onClick: async (e) => {
                            const btn = e.currentTarget;
                            btn.disabled = true;
                            btn.textContent = '⏳ Exporting...';
                            try {
                                await window.CRMApi.downloadCompanyExcel(selectedCompany.id, state.folders.selectedSubfolderId);
                                window.crmToast(`Downloaded ${selectedCompany.name} Excel workbook.`, 'success');
                            } catch (err) {
                                window.crmToast(err.message || 'Excel export failed', 'error');
                            } finally {
                                btn.disabled = false;
                                btn.textContent = '📊 Export Folder Excel (.xlsx)';
                            }
                        }
                    })
                );

                if (selectedCompany.google_sheet_url && /^https?:\/\//i.test(selectedCompany.google_sheet_url)) {
                    footerBtns.push(
                        el('a', {
                            href: selectedCompany.google_sheet_url,
                            target: '_blank',
                            rel: 'noopener noreferrer',
                            className: 'btn btn-secondary',
                            style: { width: '100%', justifyContent: 'center', padding: '0.42rem', fontSize: '0.76rem', textDecoration: 'none', color: 'var(--success-color)', borderColor: 'var(--success-border)' },
                            text: '↗ Open Linked Google Sheet'
                        })
                    );
                }

                treePane.appendChild(el('div', { className: 'crm-company-footer-actions' }, footerBtns));
            }
        },

        async promptNewSubfolder(companyId) {
            const name = await window.crmPrompt({
                title: 'Create New Subfolder',
                message: 'Enter a name for the new subfolder:',
                placeholder: 'e.g. enterprise_drone, thermal_optics, sandeep',
                confirmText: 'Create Subfolder'
            });
            if (!name) return;
            try {
                const res = await window.CRMApi.createSubfolder({ company_id: companyId, name: name });
                await window.CRMState.loadBootstrap();
                const created = res.subfolder || res;
                if (created && created.id) {
                    window.CRMState.folders.selectedSubfolderId = created.id;
                }
                window.crmToast(`Created subfolder "${name}".`, 'success');
                this.renderTreePane();
                await this.loadDossiers();
            } catch (err) {
                window.crmToast(err.message || 'Could not create subfolder', 'error');
            }
        },

        renderListPaneHeader() {
            const listPane = document.getElementById('crmFoldersListPane');
            if (!listPane) return;
            clear(listPane);

            const state = window.CRMState;
            const comp = state.getCompanyById(state.folders.selectedCompanyId);
            const sf = (state.subfolders || []).find((s) => String(s.id) === String(state.folders.selectedSubfolderId));
            const titleText = comp ? `${comp.name}${sf ? ' / ' + sf.name : ' · All Dossiers'}` : 'Dossiers';

            const header = el('div', { className: 'crm-pane-header' }, [
                el('div', { style: { display: 'flex', alignItems: 'center', gap: '0.45rem' } }, [
                    el('h3', { text: titleText }),
                    el('span', { id: 'crmDossiersListCount', className: 'badge-count', text: String(state.folders.tenders.length) })
                ]),
                el('button', {
                    type: 'button',
                    className: 'btn-crm-toolbar',
                    style: { padding: '0.25rem 0.55rem', fontSize: '0.74rem' },
                    text: '🔄 Refresh',
                    onClick: () => this.loadDossiers()
                })
            ]);

            const searchInput = el('input', {
                type: 'search',
                value: state.folders.searchQuery || '',
                placeholder: 'Search dossiers by title, bid no, SL #, buyer...',
                style: {
                    width: '100%',
                    padding: '0.45rem 0.7rem',
                    borderRadius: '7px',
                    border: '1px solid var(--border-strong)',
                    background: 'var(--input-bg)',
                    color: 'var(--text-primary)',
                    fontSize: '0.8rem'
                },
                onInput: (e) => {
                    state.folders.searchQuery = e.target.value;
                    clearTimeout(this.searchTimer);
                    this.searchTimer = setTimeout(() => this.loadDossiers(), 200);
                }
            });

            const stageSelect = el('select', {
                className: 'crm-select-sm',
                style: { flex: '1' },
                onChange: (e) => {
                    state.folders.stageFilter = e.target.value;
                    this.loadDossiers();
                }
            }, [
                el('option', { value: '', selected: !state.folders.stageFilter, text: 'All Stages' }),
                el('option', { value: 'assigned', selected: state.folders.stageFilter === 'assigned', text: 'Assigned' }),
                el('option', { value: 'review', selected: state.folders.stageFilter === 'review', text: 'Under Detailed Study / Review' }),
                el('option', { value: 'shortlisted', selected: state.folders.stageFilter === 'shortlisted', text: 'Shortlisted' }),
                el('option', { value: 'participating', selected: state.folders.stageFilter === 'participating', text: 'Participating' }),
                el('option', { value: 'won', selected: state.folders.stageFilter === 'won', text: 'Won' }),
                el('option', { value: 'lost', selected: state.folders.stageFilter === 'lost', text: 'Lost' }),
                el('option', { value: 'rejected', selected: state.folders.stageFilter === 'rejected', text: 'Rejected / Dropped' })
            ]);

            const decisionSelect = el('select', {
                className: 'crm-select-sm',
                style: { flex: '1' },
                onChange: (e) => {
                    state.folders.decisionFilter = e.target.value;
                    this.loadDossiers();
                }
            }, [
                el('option', { value: '', selected: !state.folders.decisionFilter, text: 'All Decisions' }),
                el('option', { value: 'YES', selected: state.folders.decisionFilter === 'YES', text: '✅ YES — Participate' }),
                el('option', { value: 'NO', selected: state.folders.decisionFilter === 'NO', text: '❌ NO — Skip' }),
                el('option', { value: 'CLARIFICATION', selected: state.folders.decisionFilter === 'CLARIFICATION', text: '💬 Need Clarification' }),
                el('option', { value: 'PENDING', selected: state.folders.decisionFilter === 'PENDING', text: '⏳ Pending Decision' })
            ]);

            const filtersBox = el('div', { className: 'crm-dossier-filters' }, [
                searchInput,
                el('div', { style: { display: 'flex', gap: '0.45rem' } }, [stageSelect, decisionSelect])
            ]);

            const listBody = el('div', { id: 'crmDossierListBody', className: 'crm-dossier-list' });

            listPane.appendChild(header);
            listPane.appendChild(filtersBox);
            listPane.appendChild(listBody);
        },

        async loadDossiers() {
            const state = window.CRMState;
            const listBody = document.getElementById('crmDossierListBody');
            if (listBody) {
                clear(listBody);
                listBody.appendChild(el('div', { style: { padding: '1.5rem', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '0.82rem' }, text: 'Loading folder dossiers...' }));
            }

            try {
                const data = await window.CRMApi.listTenders({
                    company_id: state.folders.selectedCompanyId || '',
                    subfolder_id: state.folders.selectedSubfolderId || '',
                    q: state.folders.searchQuery || '',
                    stage: state.folders.stageFilter || '',
                    vendor_decision: state.folders.decisionFilter || ''
                });
                const list = data.tenders || data.items || (Array.isArray(data) ? data : []);
                state.folders.tenders = list;

                const cntBadge = document.getElementById('crmDossiersListCount');
                if (cntBadge) cntBadge.textContent = String(list.length);

                this.renderDossierRows();
            } catch (err) {
                if (listBody) {
                    clear(listBody);
                    listBody.appendChild(el('div', { style: { padding: '1.5rem', textAlign: 'center', color: 'var(--failed-color)', fontSize: '0.82rem' }, text: err.message || 'Could not load dossiers' }));
                }
            }
        },

        renderDossierRows() {
            const listBody = document.getElementById('crmDossierListBody');
            if (!listBody) return;
            clear(listBody);

            const state = window.CRMState;
            const list = state.folders.tenders || [];

            if (list.length === 0) {
                listBody.appendChild(
                    el('div', { className: 'crm-inspector-empty' }, [
                        el('div', { style: { fontSize: '2rem' }, text: '📂' }),
                        el('h4', { text: 'No dossiers in this folder' }),
                        el('p', { style: { fontSize: '0.78rem' }, text: state.isAdmin() ? 'Move tenders here from the Discovery Inbox or click "Scan & Clean Legacy Disk Folders" in the Admin tab.' : 'No tenders have been assigned to this folder yet.' })
                    ])
                );
                return;
            }


            list.forEach((dossier) => {
                const dId = dossier.id || dossier.crm_tender_id;
                const isSelected = String(state.folders.selectedCrmTenderId) === String(dId);
                const filesCount = (dossier.files && dossier.files.length) || dossier.file_count || 0;
                const decision = (dossier.vendor_decision || 'PENDING').toUpperCase();

                let decisionBadgeClass = 'crm-pill-neutral';
                let decisionLabel = '⏳ Pending';
                if (decision === 'YES') {
                    decisionBadgeClass = 'crm-pill-pursue';
                    decisionLabel = '✅ Participate';
                } else if (decision === 'NO') {
                    decisionBadgeClass = 'crm-pill-drop';
                    decisionLabel = '❌ Skip';
                } else if (decision === 'CLARIFICATION') {
                    decisionBadgeClass = 'crm-pill-review';
                    decisionLabel = '💬 Clarification';
                }

                const topPills = [];
                if (dossier.sl_no) {
                    topPills.push(el('span', { className: 'crm-pill crm-pill-synced', text: `SL #${dossier.sl_no}` }));
                }
                if (dossier.bid_no) {
                    topPills.push(el('span', { className: 'crm-row-bid', text: dossier.bid_no }));
                }
                if (dossier.subfolder_name || dossier.subfolder_slug) {
                    topPills.push(el('span', { className: 'crm-pill crm-pill-neutral', text: `📂 ${dossier.subfolder_name || dossier.subfolder_slug}` }));
                }
                topPills.push(el('span', { className: `crm-pill ${decisionBadgeClass}`, text: decisionLabel }));

                // Sync indicators row (SL #, Drive, GSheet, Files)
                const syncPills = [];
                syncPills.push(el('span', { className: 'crm-pill crm-pill-neutral', text: `📎 ${filesCount} file(s)` }));
                if (dossier.drive_link) {
                    syncPills.push(el('span', { className: 'crm-pill crm-pill-synced', text: '📁 Drive ✓' }));
                }
                if (dossier.google_sheet_synced || dossier.sheet_synced || dossier.sl_no) {
                    syncPills.push(el('span', { className: 'crm-pill crm-pill-synced', text: '📊 GSheet ✓' }));
                }

                const row = el('div', {
                    className: `crm-tender-row ${isSelected ? 'selected' : ''}`,
                    dataset: { crmId: String(dId) },
                    onClick: () => {
                        state.folders.selectedCrmTenderId = dId;
                        state.folders.selectedTenderObj = dossier;
                        listBody.querySelectorAll('.crm-tender-row').forEach((n) => {
                            n.classList.toggle('selected', n.dataset.crmId === String(dId));
                        });
                        this.openDossierPage(dossier);
                    }
                }, [
                    el('div', { className: 'crm-row-body' }, [
                        el('div', { className: 'crm-row-top' }, topPills),
                        el('div', { className: 'crm-row-title', text: dossier.title || 'Untitled Tender' }),
                        el('div', { className: 'crm-row-meta' }, [
                            el('span', { className: 'crm-row-dept', text: dossier.organisation || dossier.department || 'Government Buyer' }),
                            el('span', { style: { fontWeight: '600' }, text: dossier.end_date || '' })
                        ]),
                        el('div', { style: { display: 'flex', gap: '0.35rem', flexWrap: 'wrap', marginTop: '0.15rem' } }, syncPills)
                    ])
                ]);

                listBody.appendChild(row);
            });
        },

        openDossierPage(dossier) {
            const list = window.CRMState.folders.tenders || [];
            const idOf = (d) => String(d.id || d.crm_tender_id);
            const index = list.findIndex((d) => idOf(d) === idOf(dossier));
            window.CRMTenderPage.open({
                list: index >= 0 ? list : [dossier],
                index: Math.max(index, 0),
                backLabel: 'Folder Dossiers',
                toInspectOptions: (item) => ({
                    tender: item,
                    crmTender: item,
                    mode: 'folders',
                    onMoved: async () => {
                        this.renderTreePane();
                        await this.loadDossiers();
                    },
                    onUpdated: async (info) => {
                        if (info && info.removed) {
                            window.CRMState.folders.selectedCrmTenderId = null;
                            window.CRMTenderPage.close();
                        }
                        this.renderTreePane();
                        await this.loadDossiers();
                    }
                }),
                onClose: (item) => {
                    if (!item) return;
                    const state = window.CRMState;
                    state.folders.selectedCrmTenderId = item.id || item.crm_tender_id;
                    state.folders.selectedTenderObj = item;
                    const listBody = document.getElementById('crmDossierListBody');
                    if (!listBody) return;
                    listBody.querySelectorAll('.crm-tender-row').forEach((n) => {
                        const match = n.dataset.crmId === idOf(item);
                        n.classList.toggle('selected', match);
                        if (match) n.scrollIntoView({ block: 'nearest' });
                    });
                }
            });
        }
    };

    window.CRMFoldersView = CRMFoldersView;
})();
