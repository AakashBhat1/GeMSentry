/* ==========================================================================
   GeMSentry CRM Shared Inline Inspector (PDF / CSV / Annexures / Triage)
   Used in both Discovery Inbox (right pane) & Companies/Folders (right pane).
   Security: Strictly uses crmEl / crmClear / textContent.
   ========================================================================== */
(function () {
    'use strict';

    const el = window.crmEl;
    const clear = window.crmClear;

    function formatInr(val) {
        const num = Number(val);
        if (!num || isNaN(num) || num <= 0) return 'N/A';
        if (num >= 10000000) return `₹${(num / 10000000).toFixed(2)} Cr`;
        if (num >= 100000) return `₹${(num / 100000).toFixed(2)} Lakh`;
        return `₹${num.toLocaleString('en-IN')}`;
    }

    function fileKindLabel(kind, filename) {
        const k = String(kind || '').toLowerCase();
        const name = String(filename || '');
        const shortName = name.length > 26 ? name.slice(0, 24) + '…' : name;
        if (k === 'rfp') return '📄 Main RFP PDF';
        if (k === 'tech_spec') return `📑 Tech Spec${shortName ? ' · ' + shortName : ''}`;
        if (k === 'boq' || name.toLowerCase().endsWith('.csv')) return `📊 BOQ CSV${shortName ? ' · ' + shortName : ''}`;
        if (k === 'atc') return `📎 ATC${shortName ? ' · ' + shortName : ''}`;
        return `📎 ${shortName || 'Annexure'}`;
    }

    function resolveMainPdfUrl(tender, crmTender, crmFiles, previewData) {
        const rfpFile = (crmFiles || []).find((f) => f.file_kind === 'rfp' && f.id);
        if (rfpFile) {
            return `/api/crm/files/${encodeURIComponent(rfpFile.id)}?inline=1`;
        }
        if (previewData && previewData.local_pdf_url) {
            return previewData.local_pdf_url;
        }
        const rawPath = (crmTender && crmTender.local_pdf_path) || (tender && tender.local_pdf_path) || '';
        if (rawPath) {
            const norm = String(rawPath).replace(/\\/g, '/').replace(/^\/+/, '');
            if (norm.startsWith('tenders/')) {
                return '/' + encodeURI(norm);
            }
            return '/tenders/downloads/' + encodeURI(norm);
        }
        return null;
    }

    const CRMInspector = {
        activeContainer: null,
        context: null, // { tender, crmTender, mode, previewData, activeTab, onMoved, onUpdated }
        requestCounter: 0,

        renderEmpty(container, message) {
            if (!container) return;
            clear(container);
            container.appendChild(
                el('div', { className: 'crm-inspector-empty' }, [
                    el('div', { style: { fontSize: '2.2rem' }, text: '📄' }),
                    el('h3', { style: { fontSize: '1rem', fontWeight: '700', color: 'var(--text-primary)' }, text: 'Inline Tender & Document Inspector' }),
                    el('p', { style: { fontSize: '0.84rem', maxWidth: '340px' }, text: message || 'Select any tender row on the left to inspect its RFP PDF, Tech Specs, BOQ CSVs, and score breakdown right on this page.' })
                ])
            );
        },

        async inspect(container, options) {
            if (!container) return;
            const opts = options || {};
            const tender = opts.tender || {};
            const crmTender = opts.crmTender || null;
            const bidNo = (crmTender && crmTender.bid_no) || tender.bid_no || '';

            if (!bidNo && !crmTender) {
                this.renderEmpty(container);
                return;
            }

            this.activeContainer = container;
            const reqId = ++this.requestCounter;

            // Default company & subfolder selection
            const state = window.CRMState;
            let defaultCompId = (crmTender && crmTender.company_id) || state.inspector.targetCompanyId || 'etspl';
            if (!state.getCompanyById(defaultCompId) && state.companies.length > 0) {
                defaultCompId = state.companies[0].id;
            }
            const sfs = state.getSubfoldersForCompany(defaultCompId);
            let defaultSfId = (crmTender && crmTender.subfolder_id) || state.inspector.targetSubfolderId || (sfs[0] ? String(sfs[0].id) : '');

            this.context = {
                tender: tender,
                crmTender: crmTender,
                bidNo: bidNo,
                mode: opts.mode || 'inbox',
                previewData: null,
                activeTab: { type: 'rfp', id: 'main_rfp' },
                targetCompanyId: defaultCompId,
                targetSubfolderId: defaultSfId ? String(defaultSfId) : '',
                autoFetchAndSync: state.inspector.autoFetchAndSync !== false,
                onMoved: opts.onMoved || null,
                onUpdated: opts.onUpdated || null
            };

            this.renderView();

            if (bidNo) {
                try {
                    const preview = await window.CRMApi.getTenderPreview(bidNo);
                    if (reqId !== this.requestCounter) return;
                    this.context.previewData = preview.preview || preview.data || preview;
                    const assignments = this.getAssignments();
                    if (assignments.length > 0) {
                        state.setAssignmentsForBid(bidNo, assignments);
                        if (!this.context.crmTender) {
                            this.context.crmTender = assignments[0];
                        }
                    }
                    // Also hydrate full analysis from /api/tenders/<bidNo> if needed for admin
                    if (state.isAdmin() && tender.analysis && tender.analysis._truncated && typeof window.fetchTenderDetail === 'function') {
                        const full = await window.fetchTenderDetail(bidNo);
                        if (reqId === this.requestCounter && full) {
                            this.context.tender = full;
                        }
                    }
                    this.renderView();
                } catch (_err) {
                    // Preview endpoint may 404 if tender is only in memory; keep local render
                }
            }
        },

        getAssignments() {
            const ctx = this.context;
            if (!ctx) return [];
            const p = ctx.previewData || {};
            const list = p.assignments || p.crm_tenders || p.crm_assignments || [];
            if (list.length > 0) return list;
            if (ctx.crmTender) return [ctx.crmTender];
            return window.CRMState.getAssignmentsForBid(ctx.bidNo);
        },

        getCrmFiles() {
            const ctx = this.context;
            if (!ctx) return [];
            const p = ctx.previewData || {};
            const fromPreview = p.files || p.crm_files || (p.crm_tender && p.crm_tender.files) || [];
            if (Array.isArray(fromPreview) && fromPreview.length > 0) return fromPreview;
            if (ctx.crmTender && Array.isArray(ctx.crmTender.files)) return ctx.crmTender.files;
            const assignments = this.getAssignments();
            if (assignments[0] && Array.isArray(assignments[0].files)) return assignments[0].files;
            return [];
        },

        getEmbeddedAnnexures() {
            const ctx = this.context;
            if (!ctx) return [];
            const p = ctx.previewData || {};
            return p.embedded_annexures || p.annexure_links || p.annexures || [];
        },

        getActiveCrmTenderId() {
            const ctx = this.context;
            if (!ctx) return null;
            if (ctx.crmTender && (ctx.crmTender.id || ctx.crmTender.crm_tender_id)) {
                return ctx.crmTender.id || ctx.crmTender.crm_tender_id;
            }
            const assignments = this.getAssignments();
            if (assignments.length > 0) {
                return assignments[0].id || assignments[0].crm_tender_id || null;
            }
            return null;
        },

        async ensureInCrmFolder() {
            const existingId = this.getActiveCrmTenderId();
            if (existingId) return existingId;
            const ctx = this.context;
            if (!ctx || !ctx.bidNo) throw new Error('No tender selected');
            const res = await window.CRMApi.moveTenders({
                bid_nos: [ctx.bidNo],
                company_id: ctx.targetCompanyId || 'etspl',
                subfolder_id: ctx.targetSubfolderId ? Number(ctx.targetSubfolderId) || ctx.targetSubfolderId : null,
                fetch_gem_annexures: true,
                sync_master_and_sheets: Boolean(ctx.autoFetchAndSync)
            });
            const movedList = res.moved || res.tenders || (res.crm_tender ? [res.crm_tender] : []);
            if (movedList.length > 0) {
                ctx.crmTender = movedList[0];
                window.CRMState.setAssignmentsForBid(ctx.bidNo, movedList);
            }
            await window.CRMState.loadBootstrap();
            const preview = await window.CRMApi.getTenderPreview(ctx.bidNo).catch(() => null);
            if (preview) {
                ctx.previewData = preview.preview || preview.data || preview;
            }
            return this.getActiveCrmTenderId();
        },

        renderView() {
            const container = this.activeContainer;
            const ctx = this.context;
            if (!container || !ctx) return;
            clear(container);

            const state = window.CRMState;
            const isAdmin = state.isAdmin();
            const isVendor = state.isVendor();
            const p = ctx.previewData || {};
            const baseTender = Object.assign({}, p.tender || {}, ctx.tender || {}, ctx.crmTender || {});
            const crmFiles = this.getCrmFiles();
            const embedded = this.getEmbeddedAnnexures();
            const assignments = this.getAssignments();

            const layout = window.CRMInspectorLayout;
            layout.applyFocus(container);

            // 1. Compact header (bid, pills, title, triage)
            container.appendChild(this.buildHeaderAndActionBar(baseTender, assignments, isAdmin, isVendor));

            // 2. Collapsible panels — collapsed by default so the document keeps the space
            const panels = [];
            const folderBar = this.buildFolderActionBar(isAdmin);
            if (folderBar) {
                panels.push({ id: 'folder', label: '📁 Folder & Sync', node: folderBar });
            }
            // Participation response: folders mode, any non-admin user, or when assigned to a CRM folder
            if (ctx.mode === 'folders' || !isAdmin || ctx.crmTender) {
                const responseBox = this.buildParticipationBox(baseTender, isAdmin, isVendor);
                if (responseBox) {
                    const decision = String(baseTender.vendor_decision || 'PENDING').toUpperCase();
                    panels.push({ id: 'response', label: '📋 Response', badge: decision, node: responseBox });
                }
            }
            const meta = this.buildMetadataStrip(baseTender, isAdmin, isVendor);
            panels.push({ id: 'details', label: '📊 Details & Score', badge: meta.badge, node: meta.node });

            const deck = layout.buildPanelDeck(panels);
            container.appendChild(deck.bar);
            container.appendChild(deck.body);

            // 3. Document Tabs Bar
            container.appendChild(this.buildDocumentTabsBar(baseTender, crmFiles, embedded, isAdmin));

            // 4. Inline Viewer Container (iframe PDF or CSV table) — fills the remaining height
            const viewerArea = el('div', { className: 'crm-viewer-container', id: 'crmInlineViewerArea' });
            container.appendChild(viewerArea);
            this.renderActiveDocument(viewerArea, baseTender, crmFiles, embedded);
        },

        buildHeaderAndActionBar(baseTender, assignments, isAdmin, isVendor) {
            const ctx = this.context;
            const bidNo = ctx.bidNo || baseTender.bid_no || 'Confidential Bid';
            const statusText = baseTender.status || 'Pending Review';

            const badgeNodes = [];
            if (baseTender.sl_no) {
                badgeNodes.push(el('span', { className: 'crm-pill crm-pill-synced', text: `SL #${baseTender.sl_no}` }));
            }
            if (!isVendor || baseTender.bid_no) {
                badgeNodes.push(el('span', { className: 'crm-row-bid', text: bidNo }));
            }
            if (baseTender.work_category) {
                badgeNodes.push(el('span', { className: 'crm-pill crm-pill-neutral', text: baseTender.work_category }));
            }
            assignments.forEach((asgn) => {
                const cName = asgn.company_name || asgn.company_id || '';
                const sfName = asgn.subfolder_name || asgn.subfolder_slug || '';
                if (cName) {
                    badgeNodes.push(el('span', {
                        className: 'crm-pill crm-pill-synced',
                        title: asgn.dossier_path || '',
                        text: `📁 ${cName}${sfName ? ' / ' + sfName : ''}`
                    }));
                }
            });

            // Status triage buttons (Admin only)
            const triageGroup = isAdmin ? el('div', { style: { display: 'inline-flex', gap: '0.3rem', alignItems: 'center' } }, [
                el('button', {
                    type: 'button',
                    className: `btn-override ${statusText === 'Shortlisted' ? 'active-short' : ''}`,
                    style: { padding: '0.28rem 0.55rem', fontSize: '0.73rem' },
                    text: '🟢 Shortlist',
                    onClick: () => this.handleStatusOverride('Shortlisted')
                }),
                el('button', {
                    type: 'button',
                    className: `btn-override ${statusText === 'Pending Review' ? 'active-pending' : ''}`,
                    style: { padding: '0.28rem 0.55rem', fontSize: '0.73rem' },
                    text: '🟡 Pending',
                    onClick: () => this.handleStatusOverride('Pending Review')
                }),
                el('button', {
                    type: 'button',
                    className: `btn-override ${statusText === 'Rejected' ? 'active-rejected' : ''}`,
                    style: { padding: '0.28rem 0.55rem', fontSize: '0.73rem' },
                    text: '🔴 Reject',
                    onClick: () => this.handleStatusOverride('Rejected')
                })
            ]) : null;

            const topRow = el('div', { className: 'crm-inspector-title-row' }, [
                el('div', { style: { display: 'flex', flexDirection: 'column', gap: '0.3rem', flex: '1' } }, [
                    el('div', { style: { display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' } }, badgeNodes),
                    el('div', { className: 'crm-inspector-title', title: baseTender.title || '', text: baseTender.title || 'Untitled Tender' })
                ]),
                triageGroup
            ]);

            return el('div', { className: 'crm-inspector-header' }, [topRow]);
        },

        buildFolderActionBar(isAdmin) {
            const ctx = this.context;
            const state = window.CRMState;
            // Folder Move & Sync Bar (Admin only)
            if (isAdmin && state.companies.length > 0) {
                const companySelect = el('select', {
                    className: 'crm-select-sm',
                    'aria-label': 'Select Target Company',
                    onChange: (e) => {
                        ctx.targetCompanyId = e.target.value;
                        state.inspector.targetCompanyId = ctx.targetCompanyId;
                        const sfs = state.getSubfoldersForCompany(ctx.targetCompanyId);
                        ctx.targetSubfolderId = sfs[0] ? String(sfs[0].id) : '';
                        state.inspector.targetSubfolderId = ctx.targetSubfolderId;
                        this.renderView();
                    }
                }, state.companies.map((c) => el('option', {
                    value: c.id,
                    selected: c.id === ctx.targetCompanyId,
                    text: `🏢 ${c.name}`
                })));

                const subfolders = state.getSubfoldersForCompany(ctx.targetCompanyId);
                if (!ctx.targetSubfolderId && subfolders.length > 0) {
                    ctx.targetSubfolderId = String(subfolders[0].id);
                }

                const subfolderSelect = el('select', {
                    className: 'crm-select-sm',
                    'aria-label': 'Select Target Subfolder',
                    onChange: (e) => {
                        ctx.targetSubfolderId = e.target.value;
                        state.inspector.targetSubfolderId = ctx.targetSubfolderId;
                    }
                }, subfolders.map((sf) => el('option', {
                    value: String(sf.id),
                    selected: String(sf.id) === String(ctx.targetSubfolderId),
                    text: `📂 ${sf.name}`
                })));

                const newSfBtn = el('button', {
                    type: 'button',
                    className: 'btn-crm-toolbar',
                    style: { padding: '0.32rem 0.55rem', fontSize: '0.75rem' },
                    title: 'Create a new subfolder in this company',
                    text: '+ New',
                    onClick: async () => {
                        const name = await window.crmPrompt({
                            title: 'New Subfolder',
                            message: 'Enter new subfolder name:',
                            placeholder: 'e.g. lidar_drones, solar_pumps, sandeep',
                            confirmText: 'Create Subfolder'
                        });
                        if (!name) return;
                        try {
                            const res = await window.CRMApi.createSubfolder({
                                company_id: ctx.targetCompanyId,
                                name: name
                            });
                            await state.loadBootstrap();
                            const created = res.subfolder || res;
                            if (created && created.id) {
                                ctx.targetSubfolderId = String(created.id);
                            }
                            window.crmToast(`Subfolder "${name}" created.`, 'success');
                            this.renderView();
                        } catch (err) {
                            window.crmToast(err.message || 'Failed to create subfolder', 'error');
                        }
                    }
                });

                const autoSyncCheck = el('input', {
                    type: 'checkbox',
                    checked: ctx.autoFetchAndSync,
                    onChange: (e) => {
                        ctx.autoFetchAndSync = e.target.checked;
                        state.inspector.autoFetchAndSync = ctx.autoFetchAndSync;
                    }
                });

                const autoSyncLabel = el('label', {
                    style: { display: 'inline-flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.75rem', color: 'var(--text-secondary)', cursor: 'pointer' },
                    title: 'Automatically download GeM embedded Tech Spec / BOQ / ATC docs, assign Serial Number, and sync to Google Sheet / Drive / Excel'
                }, [
                    autoSyncCheck,
                    el('span', { text: 'Auto-fetch GeM Specs/BOQ & Sync Google Sheet/Drive/Excel' })
                ]);

                const moveBtn = el('button', {
                    type: 'button',
                    className: 'btn btn-primary',
                    style: { padding: '0.38rem 0.85rem', fontSize: '0.78rem' },
                    text: '📁 Move to Folder & Sync',
                    onClick: () => this.handleMoveAndSync(moveBtn)
                });

                const removeBtn = (ctx.mode === 'folders' && this.getActiveCrmTenderId() && isAdmin)
                    ? el('button', {
                        type: 'button',
                        className: 'btn btn-secondary',
                        style: { padding: '0.35rem 0.65rem', fontSize: '0.75rem', color: 'var(--failed-color)', borderColor: 'var(--failed-border)' },
                        text: '🗑️ Remove',
                        onClick: () => this.handleRemoveFromFolder()
                    })
                    : null;

                return el('div', { className: 'crm-inspector-actionbar' }, [
                    companySelect,
                    subfolderSelect,
                    newSfBtn,
                    autoSyncLabel,
                    moveBtn,
                    removeBtn
                ]);
            }
            return null;
        },

        buildParticipationBox(baseTender, isAdmin, isVendor) {
            const crmId = this.getActiveCrmTenderId();
            if (!crmId && !isAdmin) return null;

            let currentDecision = (baseTender.vendor_decision || 'PENDING').toUpperCase();
            const remarksInput = el('input', {
                type: 'text',
                value: baseTender.vendor_remarks || '',
                placeholder: 'Remarks, technical deviations, readiness, or clarification needed...',
                style: {
                    flex: '1',
                    minWidth: '200px',
                    padding: '0.4rem 0.65rem',
                    borderRadius: '6px',
                    border: '1px solid var(--border-strong)',
                    background: 'var(--input-bg)',
                    color: 'var(--text-primary)',
                    fontSize: '0.8rem'
                }
            });

            const stageOptions = [
                { value: 'assigned', label: 'Assigned' },
                { value: 'review', label: 'Under Detailed Study / Review' },
                { value: 'shortlisted', label: 'Shortlisted' },
                { value: 'participating', label: 'Participating' },
                { value: 'won', label: 'Won' },
                { value: 'lost', label: 'Lost' },
                { value: 'rejected', label: 'Rejected / Dropped' }
            ];
            const normalizedStage = String(baseTender.stage || 'review').toLowerCase().trim();
            const stageSelect = isAdmin ? el('select', {
                className: 'crm-select-sm',
                title: 'Tender CRM Stage'
            }, stageOptions.map((st) => el('option', {
                value: st.value,
                selected: normalizedStage === st.value,
                text: `Stage: ${st.label}`
            }))) : null;

            const btnPending = el('button', {
                type: 'button',
                className: `btn-decision-choice ${currentDecision === 'PENDING' ? 'active-clarify' : ''}`,
                text: '⏳ Pending',
                onClick: () => {
                    currentDecision = 'PENDING';
                    updateChoiceStyles();
                }
            });
            const btnYes = el('button', {
                type: 'button',
                className: `btn-decision-choice ${currentDecision === 'YES' ? 'active-yes' : ''}`,
                text: '✅ YES — Participate',
                onClick: () => {
                    currentDecision = 'YES';
                    updateChoiceStyles();
                }
            });
            const btnNo = el('button', {
                type: 'button',
                className: `btn-decision-choice ${currentDecision === 'NO' ? 'active-no' : ''}`,
                text: '❌ NO — Skip',
                onClick: () => {
                    currentDecision = 'NO';
                    updateChoiceStyles();
                }
            });
            const btnClarify = el('button', {
                type: 'button',
                className: `btn-decision-choice ${currentDecision === 'CLARIFICATION' ? 'active-clarify' : ''}`,
                text: '💬 Need Clarification',
                onClick: () => {
                    currentDecision = 'CLARIFICATION';
                    updateChoiceStyles();
                }
            });

            function updateChoiceStyles() {
                btnPending.className = `btn-decision-choice ${currentDecision === 'PENDING' ? 'active-clarify' : ''}`;
                btnYes.className = `btn-decision-choice ${currentDecision === 'YES' ? 'active-yes' : ''}`;
                btnNo.className = `btn-decision-choice ${currentDecision === 'NO' ? 'active-no' : ''}`;
                btnClarify.className = `btn-decision-choice ${currentDecision === 'CLARIFICATION' ? 'active-clarify' : ''}`;
            }

            const saveBtn = el('button', {
                type: 'button',
                className: 'btn btn-primary',
                style: { padding: '0.4rem 0.85rem', fontSize: '0.78rem' },
                text: '💾 Save Response',
                onClick: async () => {
                    try {
                        const targetId = isAdmin ? await this.ensureInCrmFolder() : this.getActiveCrmTenderId();
                        if (!targetId) return;
                        saveBtn.disabled = true;
                        saveBtn.textContent = 'Saving...';
                        const payload = {
                            vendor_decision: currentDecision || 'PENDING',
                            vendor_remarks: remarksInput.value.trim()
                        };
                        if (isAdmin && stageSelect) {
                            payload.stage = stageSelect.value;
                        }
                        const res = await window.CRMApi.updateDecision(targetId, payload);
                        const updated = res.tender || res.crm_tender || payload;
                        if (this.context.crmTender) {
                            Object.assign(this.context.crmTender, updated);
                        }
                        window.crmToast('Participation response saved.', 'success');
                        if (typeof this.context.onUpdated === 'function') {
                            this.context.onUpdated(updated);
                        }
                    } catch (err) {
                        window.crmToast(err.message || 'Failed to save decision', 'error');
                    } finally {
                        saveBtn.disabled = false;
                        saveBtn.textContent = '💾 Save Response';
                    }
                }
            });

            return el('div', { className: 'crm-vendor-response-box' }, [
                el('div', { className: 'crm-decision-btns' }, [
                    btnPending,
                    btnYes,
                    btnNo,
                    btnClarify,
                    stageSelect
                ]),
                el('div', { className: 'crm-decision-btns' }, [
                    remarksInput,
                    saveBtn
                ])
            ]);
        },

        buildMetadataStrip(baseTender, isAdmin, isVendor) {
            const analysis = baseTender.analysis || {};
            const cells = [];

            const addCell = (label, val, color) => {
                if (val === undefined || val === null || val === '') return;
                cells.push(el('div', { className: 'crm-meta-cell' }, [
                    el('span', { className: 'crm-meta-label', text: label }),
                    el('span', { className: 'crm-meta-val', style: color ? { color } : {}, text: String(val) })
                ]));
            };

            addCell('Organisation / Buyer', baseTender.department || baseTender.organisation || analysis.buyer_org || 'N/A');
            addCell('Deadline', baseTender.end_date || 'N/A');
            addCell('Quantity', baseTender.quantity || 'N/A');

            // Hide commercial & internal score fields when redacted for vendor
            const isRedactedVendor = isVendor && (baseTender.est_value_inr === null || baseTender.est_value_inr === undefined) && !analysis.score;
            if (!isRedactedVendor) {
                const estVal = baseTender.est_value_inr || analysis.est_value_inr;
                addCell('Estimated Value', formatInr(estVal), 'var(--secondary-accent)');
                addCell('EMD Requirement', baseTender.emd_amount || analysis.emd_status || 'N/A');
                addCell('MSE / Startup Exemption', `${analysis.mse_exemption || 'N/A'} / ${analysis.startup_exemption || 'N/A'}`);
                if (analysis.eligibility && analysis.eligibility.verdict) {
                    addCell('Eligibility Verdict', `${analysis.eligibility.verdict.toUpperCase()}${analysis.eligibility.detail ? ' — ' + analysis.eligibility.detail : ''}`);
                }
                if (analysis.fit_score !== undefined && analysis.fit_score !== null) {
                    addCell('Scores', `Fit: ${analysis.fit_score}/100 · Terms: ${analysis.score !== undefined && analysis.score !== null ? analysis.score + '/100' : 'Card Only'}`);
                }
                if (analysis.consignee_state) {
                    addCell('Consignee State', analysis.consignee_state);
                }
            }

            if (baseTender.tech_spec_url) {
                cells.push(el('div', { className: 'crm-meta-cell' }, [
                    el('span', { className: 'crm-meta-label', text: 'Cloud Spec Link' }),
                    el('a', {
                        href: baseTender.tech_spec_url,
                        target: '_blank',
                        rel: 'noopener noreferrer',
                        style: { color: 'var(--primary-accent)', fontWeight: '700', fontSize: '0.76rem' },
                        text: '↗ Open Cloud Spec Doc'
                    })
                ]));
            }

            return {
                badge: (!isRedactedVendor && analysis.recommendation) ? String(analysis.recommendation) : '',
                node: el('div', { className: 'crm-meta-grid' }, cells)
            };
        },

        buildDocumentTabsBar(baseTender, crmFiles, embedded, isAdmin) {
            const ctx = this.context;
            const tabButtons = [];

            // Main RFP PDF tab
            const isMainActive = ctx.activeTab && ctx.activeTab.type === 'rfp';
            tabButtons.push(el('button', {
                type: 'button',
                className: `crm-doc-tab ${isMainActive ? 'active' : ''}`,
                text: '📄 Main RFP PDF',
                onClick: () => {
                    ctx.activeTab = { type: 'rfp', id: 'main_rfp' };
                    this.renderView();
                }
            }));

            // Downloaded CRM dossier files (excluding duplicate main rfp if already shown, or show all non-rfp + any extra files)
            (crmFiles || []).forEach((file) => {
                if (file.file_kind === 'rfp' && tabButtons.length === 1) {
                    return; // Main RFP tab already covers primary RFP
                }
                const isActive = ctx.activeTab && ctx.activeTab.type === 'crm_file' && String(ctx.activeTab.id) === String(file.id);
                tabButtons.push(el('button', {
                    type: 'button',
                    className: `crm-doc-tab ${isActive ? 'active' : ''}`,
                    title: file.filename || '',
                    text: fileKindLabel(file.file_kind, file.filename),
                    onClick: () => {
                        ctx.activeTab = { type: 'crm_file', id: file.id, file: file };
                        this.renderView();
                    }
                }));
            });

            // Extracted embedded annexure links not yet downloaded
            const downloadedUrls = new Set((crmFiles || []).map((f) => f.source_url).filter(Boolean));
            (embedded || []).forEach((ann, idx) => {
                const url = ann.url || ann.href || '';
                if (!url || downloadedUrls.has(url)) return;
                const tabId = `emb_${idx}`;
                const isActive = ctx.activeTab && ctx.activeTab.type === 'embedded' && ctx.activeTab.id === tabId;
                const kind = ann.file_kind || ann.kind || 'annexure';
                const label = kind === 'boq' ? '📊 GeM BOQ Link' : (kind === 'tech_spec' ? '📑 GeM Tech Spec Link' : (kind === 'atc' ? '📎 GeM ATC Link' : `📎 GeM Doc #${idx + 1}`));
                tabButtons.push(el('button', {
                    type: 'button',
                    className: `crm-doc-tab ${isActive ? 'active' : ''}`,
                    title: url,
                    text: label,
                    onClick: () => {
                        ctx.activeTab = { type: 'embedded', id: tabId, annexure: ann };
                        this.renderView();
                    }
                }));
            });

            // Right-side document actions (Admin only): Fetch All GeM Docs + Upload Spec/File
            let docActionsGroup = null;
            if (isAdmin) {
                const fetchDocsBtn = el('button', {
                    type: 'button',
                    className: 'btn-crm-toolbar',
                    title: 'Extract and download all embedded GeM Tech Specs, BOQs, and ATC documents into this dossier',
                    text: '⬇️ Fetch GeM Docs',
                    onClick: () => this.handleFetchAllGemDocs(fetchDocsBtn)
                });

                const fileInput = el('input', {
                    type: 'file',
                    accept: '.pdf,.csv,.xlsx,.xls,.doc,.docx,.txt,.png,.jpg,.jpeg,.zip',
                    style: { display: 'none' },
                    onChange: (e) => {
                        if (e.target.files && e.target.files[0]) {
                            this.handleUploadFile(e.target.files[0], kindSelect.value);
                            e.target.value = '';
                        }
                    }
                });

                const kindSelect = el('select', {
                    className: 'crm-select-sm',
                    title: 'Select document type for upload'
                }, [
                    el('option', { value: 'tech_spec', text: '📑 Tech Spec' }),
                    el('option', { value: 'boq', text: '📊 BOQ / CSV' }),
                    el('option', { value: 'atc', text: '📎 ATC' }),
                    el('option', { value: 'annexure', text: '📄 Annexure' })
                ]);

                const uploadBtn = el('button', {
                    type: 'button',
                    className: 'btn-crm-toolbar is-accent',
                    title: 'Upload a Tech Spec, BOQ, ATC or annexure into this dossier',
                    text: '➕ Upload',
                    onClick: () => fileInput.click()
                });

                docActionsGroup = el('div', { className: 'crm-doc-actions' }, [
                    fetchDocsBtn,
                    kindSelect,
                    uploadBtn,
                    fileInput
                ]);
            }

            return el('div', { className: 'crm-doc-tabs-bar' }, [
                el('div', { className: 'crm-doc-tabs-list' }, tabButtons),
                docActionsGroup
            ]);
        },

        async renderActiveDocument(viewerArea, baseTender, crmFiles, embedded) {
            clear(viewerArea);
            const ctx = this.context;
            const layout = window.CRMInspectorLayout;
            const active = (ctx && ctx.activeTab) || { type: 'rfp', id: 'main_rfp' };

            if (active.type === 'crm_file' && active.file) {
                const f = active.file;
                const fname = String(f.filename || '').toLowerCase();
                const inlineUrl = `/api/crm/files/${encodeURIComponent(f.id)}?inline=1`;
                const downloadUrl = `/api/crm/files/${encodeURIComponent(f.id)}`;

                viewerArea.appendChild(layout.buildViewerToolbar(`📎 ${f.filename || 'Document'} (${f.file_kind || 'file'})`, [
                    layout.viewerLink(inlineUrl, '↗ Pop Out'),
                    layout.viewerLink(downloadUrl, '⬇️ Download', { download: f.filename || 'document' }),
                    ...layout.buildViewerControls(this.activeContainer, viewerArea)
                ]));

                if (fname.endsWith('.csv')) {
                    const wrap = el('div', { className: 'crm-csv-preview-wrap' }, [
                        el('div', { style: { padding: '1rem', color: 'var(--text-secondary)' }, text: 'Loading BOQ CSV preview...' })
                    ]);
                    viewerArea.appendChild(wrap);
                    try {
                        const csvData = await window.CRMApi.getCsvPreview(f.id);
                        clear(wrap);
                        const headers = csvData.headers || [];
                        const rows = csvData.rows || [];
                        const table = el('table', { className: 'crm-csv-table' });
                        if (headers.length > 0) {
                            table.appendChild(el('thead', {}, [
                                el('tr', {}, headers.map((h) => el('th', { text: String(h) })))
                            ]));
                        }
                        const tbody = el('tbody', {}, rows.map((row) =>
                            el('tr', {}, (Array.isArray(row) ? row : Object.values(row)).map((cell) => el('td', { text: String(cell ?? '') })))
                        ));
                        table.appendChild(tbody);
                        wrap.appendChild(table);
                    } catch (err) {
                        clear(wrap);
                        wrap.appendChild(el('div', { style: { padding: '1rem', color: 'var(--failed-color)' }, text: `Could not preview CSV: ${err.message}` }));
                    }
                    return;
                }

                if (fname.endsWith('.pdf')) {
                    viewerArea.appendChild(el('iframe', {
                        className: 'crm-pdf-frame',
                        src: layout.withFitWidth(inlineUrl),
                        title: f.filename || 'PDF Document'
                    }));
                    return;
                }

                viewerArea.appendChild(el('div', { className: 'crm-inspector-empty' }, [
                    el('div', { style: { fontSize: '2rem' }, text: '📦' }),
                    el('p', { text: `${f.filename} is ready for download.` }),
                    el('a', { href: downloadUrl, download: f.filename || 'file', className: 'btn btn-primary', style: { textDecoration: 'none' }, text: '⬇️ Download File' })
                ]));
                return;
            }

            if (active.type === 'embedded' && active.annexure) {
                const ann = active.annexure;
                const url = ann.url || ann.href || '';
                viewerArea.appendChild(el('div', { className: 'crm-inspector-empty' }, [
                    el('div', { style: { fontSize: '2rem' }, text: '🔗' }),
                    el('h4', { style: { fontSize: '0.95rem', fontWeight: '700' }, text: ann.label || ann.filename || 'Embedded GeM Document Link' }),
                    el('p', { style: { fontSize: '0.8rem', color: 'var(--text-secondary)', wordBreak: 'break-all', maxWidth: '460px' }, text: url }),
                    el('div', { style: { display: 'flex', gap: '0.6rem', marginTop: '0.5rem' } }, [
                        window.CRMState.isAdmin() ? el('button', {
                            type: 'button',
                            className: 'btn btn-primary',
                            text: '⬇️ Download into Dossier & View Inline',
                            onClick: (e) => this.handleFetchAllGemDocs(e.currentTarget)
                        }) : null,
                        el('a', {
                            href: url,
                            target: '_blank',
                            rel: 'noopener noreferrer',
                            className: 'btn btn-secondary',
                            style: { textDecoration: 'none' },
                            text: '↗ Open GeM Link'
                        })
                    ])
                ]));
                return;
            }

            // Default: Main RFP PDF
            const pdfUrl = resolveMainPdfUrl(ctx.tender, ctx.crmTender, crmFiles, ctx.previewData);
            if (pdfUrl) {
                viewerArea.appendChild(layout.buildViewerToolbar(`📄 Main RFP · ${ctx.bidNo || 'PDF'}`, [
                    layout.viewerLink(pdfUrl, '↗ Open in Tab'),
                    baseTender.pdf_url ? layout.viewerLink(baseTender.pdf_url, '🌐 Portal', { muted: true }) : null,
                    ...layout.buildViewerControls(this.activeContainer, viewerArea)
                ]));
                viewerArea.appendChild(el('iframe', {
                    className: 'crm-pdf-frame',
                    src: layout.withFitWidth(pdfUrl),
                    title: `RFP PDF ${ctx.bidNo || ''}`
                }));
            } else {
                viewerArea.appendChild(el('div', { className: 'crm-inspector-empty' }, [
                    el('div', { style: { fontSize: '2.2rem' }, text: '📡' }),
                    el('h4', { style: { fontSize: '0.96rem', fontWeight: '700' }, text: 'Local RFP PDF Not Yet Downloaded' }),
                    el('p', { style: { fontSize: '0.82rem', maxWidth: '380px' }, text: 'Click below to acquire and analyze the official RFP PDF from GeM, or open the portal listing.' }),
                    el('div', { style: { display: 'flex', gap: '0.6rem', marginTop: '0.5rem' } }, [
                        ctx.bidNo && window.CRMState.isAdmin() ? el('button', {
                            type: 'button',
                            className: 'btn btn-primary',
                            text: '🔄 Fetch PDF & Re-analyze',
                            onClick: () => {
                                if (typeof window.reanalyzeBid === 'function') {
                                    window.reanalyzeBid(encodeURIComponent(ctx.bidNo));
                                }
                            }
                        }) : null,
                        baseTender.pdf_url ? el('a', {
                            href: baseTender.pdf_url,
                            target: '_blank',
                            rel: 'noopener noreferrer',
                            className: 'btn btn-secondary',
                            style: { textDecoration: 'none' },
                            text: '🌐 Open GeM Portal Link'
                        }) : null
                    ])
                ]));
            }
        },

        async handleStatusOverride(newStatus) {
            const ctx = this.context;
            if (!ctx || !ctx.bidNo) return;
            if (typeof window.overrideStatus === 'function') {
                await window.overrideStatus(encodeURIComponent(ctx.bidNo), newStatus);
                if (ctx.tender) ctx.tender.status = newStatus;
                this.renderView();
            }
        },

        async handleMoveAndSync(btnEl) {
            const ctx = this.context;
            if (!ctx || !ctx.bidNo) return;
            const origText = btnEl ? btnEl.textContent : '';
            if (btnEl) {
                btnEl.disabled = true;
                btnEl.textContent = '⏳ Moving & Syncing...';
            }
            try {
                const res = await window.CRMApi.moveTenders({
                    bid_nos: [ctx.bidNo],
                    company_id: ctx.targetCompanyId,
                    subfolder_id: ctx.targetSubfolderId ? Number(ctx.targetSubfolderId) || ctx.targetSubfolderId : null,
                    fetch_gem_annexures: Boolean(ctx.autoFetchAndSync),
                    sync_master_and_sheets: Boolean(ctx.autoFetchAndSync)
                });
                const moved = res.moved || res.tenders || (res.crm_tender ? [res.crm_tender] : []);
                if (moved.length > 0) {
                    ctx.crmTender = moved[0];
                    window.CRMState.setAssignmentsForBid(ctx.bidNo, moved);
                }
                if (ctx.tender) {
                    ctx.tender.status = 'Shortlisted';
                }
                await window.CRMState.loadBootstrap();
                const preview = await window.CRMApi.getTenderPreview(ctx.bidNo).catch(() => null);
                if (preview) {
                    ctx.previewData = preview.preview || preview.data || preview;
                }
                if (typeof window.loadFinalizedData === 'function') {
                    window.loadFinalizedData();
                }
                window.crmToast(`Moved ${ctx.bidNo} to folder and synced!`, 'success');
                this.renderView();
                if (typeof ctx.onMoved === 'function') {
                    ctx.onMoved(res);
                }
            } catch (err) {
                window.crmToast(err.message || 'Move to folder failed', 'error');
            } finally {
                if (btnEl) {
                    btnEl.disabled = false;
                    btnEl.textContent = origText || '📁 Move to Folder & Sync';
                }
            }
        },

        async handleFetchAllGemDocs(btnEl) {
            const origText = btnEl ? btnEl.textContent : '';
            if (btnEl) {
                btnEl.disabled = true;
                btnEl.textContent = '⏳ Fetching Docs...';
            }
            try {
                const crmId = await this.ensureInCrmFolder();
                if (!crmId) throw new Error('Could not resolve CRM dossier ID');
                const res = await window.CRMApi.fetchAnnexures(crmId);
                const preview = await window.CRMApi.getTenderPreview(this.context.bidNo).catch(() => null);
                if (preview) {
                    this.context.previewData = preview.preview || preview.data || preview;
                }
                const savedCount = (res.saved_files && res.saved_files.length) || (res.files && res.files.length) || 0;
                window.crmToast(`Fetched GeM documents (${savedCount} file(s) available in dossier).`, 'success');
                this.renderView();
            } catch (err) {
                window.crmToast(err.message || 'Failed to fetch GeM annexures', 'error');
            } finally {
                if (btnEl) {
                    btnEl.disabled = false;
                    btnEl.textContent = origText || '⬇️ Fetch All GeM Docs';
                }
            }
        },

        async handleUploadFile(file, fileKind) {
            try {
                window.crmToast(`Uploading ${file.name}...`, 'info', 2500);
                const crmId = await this.ensureInCrmFolder();
                if (!crmId) throw new Error('Could not resolve CRM dossier ID');
                const res = await window.CRMApi.uploadTenderFile(crmId, file, fileKind);
                const uploaded = res.file || (res.files && res.files[res.files.length - 1]);
                const preview = await window.CRMApi.getTenderPreview(this.context.bidNo).catch(() => null);
                if (preview) {
                    this.context.previewData = preview.preview || preview.data || preview;
                }
                if (uploaded && uploaded.id) {
                    this.context.activeTab = { type: 'crm_file', id: uploaded.id, file: uploaded };
                }
                window.crmToast(`Uploaded ${file.name} to dossier.`, 'success');
                this.renderView();
                if (this.context && typeof this.context.onUpdated === 'function') {
                    this.context.onUpdated(res);
                }
            } catch (err) {
                window.crmToast(err.message || 'File upload failed', 'error');
            }
        },

        async handleRemoveFromFolder() {
            const crmId = this.getActiveCrmTenderId();
            if (!crmId) return;
            const sure = await window.crmConfirm({
                title: 'Remove Tender from Folder',
                message: 'Remove this tender assignment from the current CRM folder?',
                confirmText: 'Remove',
                danger: true
            });
            if (!sure) return;
            try {
                await window.CRMApi.removeTender(crmId);
                await window.CRMState.loadBootstrap();
                window.crmToast('Removed tender from folder.', 'info');
                if (this.context && typeof this.context.onUpdated === 'function') {
                    this.context.onUpdated({ removed: true, id: crmId });
                }
            } catch (err) {
                window.crmToast(err.message || 'Failed to remove tender', 'error');
            }
        }
    };

    window.CRMInspector = CRMInspector;
})();
