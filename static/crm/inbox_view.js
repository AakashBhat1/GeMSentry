/* ==========================================================================
   GeMSentry CRM Discovery Inbox 2-Pane Split View
   Left Pane: High-density tender list + bulk selection + bulk move to folder
   Right Pane: Shared Inline Inspector (PDF/CSV/Annexures/Move & Sync)
   Security: Strictly uses crmEl / crmClear / textContent.
   ========================================================================== */
(function () {
    'use strict';

    const el = window.crmEl;
    const clear = window.crmClear;
    const INBOX_PAGE_SIZE = 60;

    function formatValueShort(inr) {
        const v = Number(inr);
        if (!v || isNaN(v) || v <= 0) return '';
        if (v >= 10000000) return `₹${(v / 10000000).toFixed(2)}Cr`;
        if (v >= 100000) return `₹${(v / 100000).toFixed(1)}L`;
        return `₹${v.toLocaleString('en-IN')}`;
    }

    const CRMInboxView = {
        rowLimit: INBOX_PAGE_SIZE,
        lastItems: [],
        lastGroups: [],
        lastGroupBy: 'recommendation',
        keyboardBound: false,

        init() {
            if (!this.keyboardBound && typeof document !== 'undefined') {
                this.keyboardBound = true;
                document.addEventListener('keydown', (e) => this.handleKeyDown(e));
            }
            window.CRMState.on('bootstrap:loaded', () => {
                if (window.CRMState.activeMode === 'inbox' && this.lastItems.length > 0) {
                    this.refreshLeftPaneOnly();
                }
            });
        },

        activate() {
            this.init();
            const contentArea = document.getElementById('contentArea');
            if (contentArea) {
                contentArea.classList.add('crm-inbox-mode');
            }
        },

        toggleFilters() {
            const state = window.CRMState;
            state.inbox.filtersCollapsed = !state.inbox.filtersCollapsed;
            const mainContainer = document.getElementById('mainContainer');
            if (mainContainer) {
                mainContainer.classList.toggle('sidebar-collapsed', state.inbox.filtersCollapsed);
            }
            const btn = document.getElementById('toggleSidebarFiltersBtn');
            if (btn) {
                btn.classList.toggle('active', state.inbox.filtersCollapsed);
                btn.textContent = state.inbox.filtersCollapsed ? '◧ Show Filters' : '◧ Hide Filters';
            }
        },

        toggleOverviewBanners() {
            const state = window.CRMState;
            state.inbox.compactHeaders = !state.inbox.compactHeaders;
            const selectors = ['.metrics-grid', '#liveExcelHud', '#todayTriagePanel'];
            selectors.forEach((sel) => {
                const node = document.querySelector(sel);
                if (node) node.classList.toggle('compact-hidden', state.inbox.compactHeaders);
            });
            const btn = document.getElementById('toggleInboxBannersBtn');
            if (btn) {
                btn.classList.toggle('active', !state.inbox.compactHeaders);
                btn.textContent = state.inbox.compactHeaders ? '📊 Show Overview HUD' : '📊 Hide Overview HUD';
            }
        },

        renderInboxSplitPane(items, renderedGroups, currentGroupBy) {
            this.init();
            this.lastItems = Array.isArray(items) ? items : [];
            this.lastGroups = Array.isArray(renderedGroups) ? renderedGroups : [];
            this.lastGroupBy = currentGroupBy || 'none';

            const state = window.CRMState;
            state.inbox.visibleTenders = this.lastItems;

            const listContainer = document.getElementById('tendersList');
            if (!listContainer) return;

            const contentArea = document.getElementById('contentArea');
            if (contentArea) {
                contentArea.classList.add('crm-inbox-mode');
                const selectors = ['.metrics-grid', '#liveExcelHud', '#todayTriagePanel'];
                selectors.forEach((sel) => {
                    const node = document.querySelector(sel);
                    if (node) node.classList.toggle('compact-hidden', state.inbox.compactHeaders);
                });
            }

            // Ensure split wrapper exists so we don't destroy the right-pane iframe when re-filtering if same bid stays selected
            let splitWrap = document.getElementById('crmInboxSplitWrap');
            let leftPane = document.getElementById('crmInboxLeftPane');
            let rightPane = document.getElementById('crmInboxInspectorPane');

            if (!splitWrap || !leftPane || !rightPane || !listContainer.contains(splitWrap)) {
                clear(listContainer);
                leftPane = el('div', { id: 'crmInboxLeftPane', className: 'crm-inbox-left' });
                rightPane = el('div', { id: 'crmInboxInspectorPane', className: 'crm-inspector-pane' });
                splitWrap = el('div', { id: 'crmInboxSplitWrap', className: 'crm-inbox-split' }, [
                    leftPane,
                    rightPane
                ]);
                listContainer.appendChild(splitWrap);
            }

            const prevSelected = state.inbox.selectedBidNo;
            const stillVisible = prevSelected && this.lastItems.some((t) => t.bid_no === prevSelected);
            if (!stillVisible) {
                state.inbox.selectedBidNo = this.lastItems[0] ? this.lastItems[0].bid_no : null;
            }

            this.renderLeftPane(leftPane);

            if (!state.inbox.selectedBidNo) {
                window.CRMInspector.renderEmpty(rightPane, 'No matching tenders for the active filters.');
            } else if (!stillVisible || !window.CRMInspector.context || window.CRMInspector.context.bidNo !== state.inbox.selectedBidNo) {
                const selectedTender = this.lastItems.find((t) => t.bid_no === state.inbox.selectedBidNo);
                if (selectedTender) {
                    this.openInInspector(selectedTender, rightPane);
                }
            }
        },

        refreshLeftPaneOnly() {
            const leftPane = document.getElementById('crmInboxLeftPane');
            if (leftPane) {
                this.renderLeftPane(leftPane);
            }
        },

        renderLeftPane(leftPane) {
            clear(leftPane);
            const state = window.CRMState;
            const items = this.lastItems;

            // 1. Sticky Bulk-Move & Selection Bar
            const bulkBar = this.buildBulkBar(items);
            leftPane.appendChild(bulkBar);

            // 2. Rows container
            const rowsWrap = el('div', { className: 'crm-inbox-rows', id: 'crmInboxRowsContainer' });
            leftPane.appendChild(rowsWrap);

            if (items.length === 0) {
                rowsWrap.appendChild(
                    el('div', { className: 'crm-inspector-empty' }, [
                        el('div', { style: { fontSize: '2rem' }, text: '🔍' }),
                        el('h4', { text: 'No matching tenders found' }),
                        el('p', { style: { fontSize: '0.8rem' }, text: 'Try adjusting your sidebar filters or search query.' })
                    ])
                );
                return;
            }

            const slice = items.slice(0, this.rowLimit);
            let lastBucket = null;

            slice.forEach((tender) => {
                if (this.lastGroupBy && this.lastGroupBy !== 'none' && typeof GROUPINGS !== 'undefined' && GROUPINGS[this.lastGroupBy]) {
                    const bucket = GROUPINGS[this.lastGroupBy].keyOf(tender);
                    if (bucket !== lastBucket) {
                        lastBucket = bucket;
                        const totalInBucket = items.filter((t) => GROUPINGS[this.lastGroupBy].keyOf(t) === bucket).length;
                        rowsWrap.appendChild(
                            el('div', { className: 'crm-group-divider' }, [
                                el('span', { text: bucket }),
                                el('span', { className: 'badge-count', text: String(totalInBucket) })
                            ])
                        );
                    }
                }
                rowsWrap.appendChild(this.buildTenderRow(tender));
            });

            if (items.length > slice.length) {
                const rem = items.length - slice.length;
                rowsWrap.appendChild(
                    el('button', {
                        type: 'button',
                        className: 'group-more',
                        style: { margin: '0.75rem', padding: '0.6rem' },
                        text: `Show ${Math.min(rem, INBOX_PAGE_SIZE)} more (${rem} remaining)`,
                        onClick: () => {
                            this.rowLimit += INBOX_PAGE_SIZE;
                            this.renderLeftPane(leftPane);
                        }
                    })
                );
            }
        },

        buildBulkBar(items) {
            const state = window.CRMState;
            const selectedSet = state.inbox.selectedBidNos;
            const visibleBidNos = items.map((t) => t.bid_no).filter(Boolean);
            const allVisibleSelected = visibleBidNos.length > 0 && visibleBidNos.every((b) => selectedSet.has(b));

            const selectAllCheck = el('input', {
                type: 'checkbox',
                id: 'crmSelectAllVisible',
                checked: allVisibleSelected,
                onChange: (e) => {
                    if (e.target.checked) {
                        visibleBidNos.forEach((b) => selectedSet.add(b));
                    } else {
                        visibleBidNos.forEach((b) => selectedSet.delete(b));
                    }
                    this.refreshLeftPaneOnly();
                }
            });

            const topLine = el('div', { className: 'crm-bulkbar-top' }, [
                el('label', {
                    style: { display: 'inline-flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.78rem', fontWeight: '700', cursor: 'pointer', color: 'var(--text-primary)' }
                }, [
                    selectAllCheck,
                    el('span', { text: `Select All Visible (${items.length})` })
                ]),
                el('div', { style: { display: 'flex', alignItems: 'center', gap: '0.5rem' } }, [
                    el('span', {
                        className: 'crm-pill crm-pill-neutral',
                        text: `${selectedSet.size} selected`
                    }),
                    selectedSet.size > 0 ? el('button', {
                        type: 'button',
                        className: 'btn-crm-toolbar',
                        style: { padding: '0.2rem 0.45rem', fontSize: '0.72rem' },
                        text: 'Clear',
                        onClick: () => {
                            selectedSet.clear();
                            this.refreshLeftPaneOnly();
                        }
                    }) : null
                ])
            ]);

            const companies = state.companies || [];
            if (!state.inbox.targetCompanyId && companies[0]) {
                state.inbox.targetCompanyId = companies[0].id;
            }
            const subfolders = state.getSubfoldersForCompany(state.inbox.targetCompanyId);
            if (!state.inbox.targetSubfolderId && subfolders[0]) {
                state.inbox.targetSubfolderId = String(subfolders[0].id);
            }

            const compSelect = el('select', {
                className: 'crm-select-sm',
                'aria-label': 'Bulk Move Target Company',
                onChange: (e) => {
                    state.inbox.targetCompanyId = e.target.value;
                    const sfs = state.getSubfoldersForCompany(state.inbox.targetCompanyId);
                    state.inbox.targetSubfolderId = sfs[0] ? String(sfs[0].id) : '';
                    this.refreshLeftPaneOnly();
                }
            }, companies.map((c) => el('option', {
                value: c.id,
                selected: c.id === state.inbox.targetCompanyId,
                text: `🏢 ${c.name}`
            })));

            const sfSelect = el('select', {
                className: 'crm-select-sm',
                'aria-label': 'Bulk Move Target Subfolder',
                onChange: (e) => {
                    state.inbox.targetSubfolderId = e.target.value;
                }
            }, subfolders.map((sf) => el('option', {
                value: String(sf.id),
                selected: String(sf.id) === String(state.inbox.targetSubfolderId),
                text: `📂 ${sf.name}`
            })));

            const syncCheck = el('input', {
                type: 'checkbox',
                checked: state.inbox.autoFetchAndSync !== false,
                onChange: (e) => {
                    state.inbox.autoFetchAndSync = e.target.checked;
                }
            });

            const bulkMoveBtn = el('button', {
                type: 'button',
                className: 'btn btn-primary',
                style: { padding: '0.34rem 0.7rem', fontSize: '0.75rem' },
                disabled: selectedSet.size === 0,
                text: `📁 Move ${selectedSet.size || ''} Selected to Folder & Sync`.replace(/\s+/g, ' '),
                onClick: (e) => this.handleBulkMove(e.currentTarget)
            });

            const controlsLine = el('div', { className: 'crm-bulkbar-controls' }, [
                compSelect,
                sfSelect,
                el('label', {
                    style: { display: 'inline-flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.73rem', color: 'var(--text-secondary)', cursor: 'pointer' },
                    title: 'Auto-fetch GeM annexures & sync to Google Sheet / Master Excel'
                }, [syncCheck, el('span', { text: 'Auto-Sync' })]),
                bulkMoveBtn
            ]);

            return el('div', { className: 'crm-inbox-bulkbar' }, [topLine, controlsLine]);
        },

        buildTenderRow(tender) {
            const state = window.CRMState;
            const bidNo = tender.bid_no || '';
            const isSelected = state.inbox.selectedBidNo === bidNo;
            const isChecked = state.inbox.selectedBidNos.has(bidNo);
            const analysis = tender.analysis || {};
            const rec = analysis.recommendation || '';

            const pills = [];
            if (rec) {
                const recClass = rec === 'Pursue' ? 'crm-pill-pursue' : (rec === 'Review' ? 'crm-pill-review' : 'crm-pill-drop');
                pills.push(el('span', { className: `crm-pill ${recClass}`, text: rec }));
            }
            if (analysis.high_emd_hold) {
                pills.push(el('span', { className: 'crm-pill crm-pill-review', title: 'High EMD — check MSE/Startup exemption', text: 'High EMD' }));
            }
            pills.push(el('span', { className: 'crm-row-bid', text: bidNo }));

            if (analysis.business_line && analysis.business_line.label) {
                pills.push(el('span', { className: 'crm-pill crm-pill-neutral', text: analysis.business_line.label }));
            }

            // CRM Sync / Finalized status badge
            const crmAssignments = state.getAssignmentsForBid(bidNo);
            const finalizedRec = (typeof finalizedBidsMap !== 'undefined' && finalizedBidsMap) ? finalizedBidsMap[bidNo] : null;
            if (crmAssignments.length > 0) {
                const first = crmAssignments[0];
                const label = `📁 ${first.company_name || first.company_id}${first.subfolder_name ? '/' + first.subfolder_name : ''}`;
                pills.push(el('span', { className: 'crm-pill crm-pill-synced', text: label }));
            } else if (finalizedRec) {
                pills.push(el('span', { className: 'crm-pill crm-pill-synced', text: `⭐ SL #${finalizedRec.sl_no}` }));
            }

            if (tender.status === 'Shortlisted') {
                pills.push(el('span', { className: 'crm-pill crm-pill-pursue', text: '🟢 Shortlisted' }));
            } else if (tender.status === 'Rejected') {
                pills.push(el('span', { className: 'crm-pill crm-pill-drop', text: '🔴 Rejected' }));
            }

            // Deadline & Days Left
            const days = typeof window.gemDaysToDeadline === 'function' ? window.gemDaysToDeadline(tender.end_date) : null;
            let dlText = tender.end_date ? String(tender.end_date).split(' ')[0] : 'N/A';
            let dlColor = 'var(--text-secondary)';
            if (days !== null) {
                if (days < 0) {
                    dlText = `⌛ Expired (${dlText})`;
                    dlColor = 'var(--failed-color)';
                } else if (days <= 3) {
                    dlText = `🔥 ${days}d left · ${dlText}`;
                    dlColor = 'var(--failed-color)';
                } else if (days <= 7) {
                    dlText = `⏳ ${days}d left · ${dlText}`;
                    dlColor = 'var(--warning-color)';
                } else {
                    dlText = `📅 ${days}d left · ${dlText}`;
                }
            }

            const valStr = formatValueShort(analysis.est_value_inr);
            const checkbox = el('input', {
                type: 'checkbox',
                className: 'crm-row-checkbox',
                checked: isChecked,
                onClick: (e) => e.stopPropagation(),
                onChange: (e) => {
                    if (e.target.checked) state.inbox.selectedBidNos.add(bidNo);
                    else state.inbox.selectedBidNos.delete(bidNo);
                    this.refreshLeftPaneOnly();
                }
            });

            const row = el('div', {
                className: `crm-tender-row ${isSelected ? 'selected' : ''}`,
                dataset: { bidNo: bidNo },
                onClick: () => this.selectTenderRow(tender)
            }, [
                checkbox,
                el('div', { className: 'crm-row-body' }, [
                    el('div', { className: 'crm-row-top' }, pills),
                    el('div', { className: 'crm-row-title', text: tender.title || 'Untitled Tender' }),
                    el('div', { className: 'crm-row-meta' }, [
                        el('span', { className: 'crm-row-dept', title: tender.department || '', text: tender.department || 'Government Buyer' }),
                        el('div', { style: { display: 'inline-flex', alignItems: 'center', gap: '0.5rem' } }, [
                            valStr ? el('span', { style: { fontWeight: '700', color: 'var(--secondary-accent)' }, text: valStr }) : null,
                            el('span', { style: { fontWeight: '600', color: dlColor }, text: dlText }),
                            analysis.priority_score !== undefined && analysis.priority_score !== null
                                ? el('span', { className: 'crm-pill crm-pill-neutral', text: `⭐ ${analysis.priority_score}` })
                                : null
                        ])
                    ])
                ])
            ]);

            return row;
        },

        selectTenderRow(tender) {
            if (!tender || !tender.bid_no) return;
            const state = window.CRMState;
            state.inbox.selectedBidNo = tender.bid_no;

            const container = document.getElementById('crmInboxRowsContainer');
            if (container) {
                container.querySelectorAll('.crm-tender-row').forEach((node) => {
                    const match = node.dataset.bidNo === tender.bid_no;
                    node.classList.toggle('selected', match);
                });
            }

            const rightPane = document.getElementById('crmInboxInspectorPane');
            if (rightPane) {
                this.openInInspector(tender, rightPane);
            }
        },

        openInInspector(tender, rightPane) {
            window.CRMInspector.inspect(rightPane, {
                tender: tender,
                mode: 'inbox',
                onMoved: () => {
                    this.refreshLeftPaneOnly();
                },
                onUpdated: () => {
                    this.refreshLeftPaneOnly();
                }
            });
        },

        async handleBulkMove(btnEl) {
            const state = window.CRMState;
            const bidNos = Array.from(state.inbox.selectedBidNos);
            if (bidNos.length === 0) return;

            const origText = btnEl ? btnEl.textContent : '';
            if (btnEl) {
                btnEl.disabled = true;
                btnEl.textContent = `⏳ Moving ${bidNos.length}...`;
            }

            try {
                const res = await window.CRMApi.moveTenders({
                    bid_nos: bidNos,
                    company_id: state.inbox.targetCompanyId || 'etspl',
                    subfolder_id: state.inbox.targetSubfolderId ? Number(state.inbox.targetSubfolderId) || state.inbox.targetSubfolderId : null,
                    fetch_gem_annexures: state.inbox.autoFetchAndSync !== false,
                    sync_master_and_sheets: state.inbox.autoFetchAndSync !== false
                });
                state.inbox.selectedBidNos.clear();
                await state.loadBootstrap();
                if (typeof window.loadFinalizedData === 'function') {
                    window.loadFinalizedData();
                }
                window.crmToast(`Successfully moved ${bidNos.length} tender(s) to folder and synced!`, 'success');
                this.refreshLeftPaneOnly();
            } catch (err) {
                window.crmToast(err.message || 'Bulk move failed', 'error');
            } finally {
                if (btnEl) {
                    btnEl.disabled = false;
                    btnEl.textContent = origText;
                }
            }
        },

        handleKeyDown(e) {
            if (window.CRMState.activeMode !== 'inbox') return;
            const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : '';
            if (tag === 'input' || tag === 'textarea' || tag === 'select' || (e.target && e.target.isContentEditable)) {
                return;
            }
            if (document.querySelector('.modal[style*="flex"]') || document.querySelector('.crm-dialog-backdrop')) {
                return;
            }
            if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;

            const items = this.lastItems.slice(0, this.rowLimit);
            if (items.length === 0) return;
            e.preventDefault();

            const currentBid = window.CRMState.inbox.selectedBidNo;
            let idx = items.findIndex((t) => t.bid_no === currentBid);
            if (e.key === 'ArrowDown') {
                idx = idx < items.length - 1 ? idx + 1 : 0;
            } else {
                idx = idx > 0 ? idx - 1 : items.length - 1;
            }
            const nextTender = items[idx];
            if (nextTender) {
                this.selectTenderRow(nextTender);
                const container = document.getElementById('crmInboxRowsContainer');
                if (container) {
                    const activeRow = container.querySelector('.crm-tender-row.selected');
                    if (activeRow && typeof activeRow.scrollIntoView === 'function') {
                        activeRow.scrollIntoView({ block: 'nearest' });
                    }
                }
            }
        }
    };

    window.CRMInboxView = CRMInboxView;
    window.toggleInboxFilters = function () {
        CRMInboxView.toggleFilters();
    };
    window.toggleInboxBanners = function () {
        CRMInboxView.toggleOverviewBanners();
    };
})();
