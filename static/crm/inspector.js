/* ==========================================================================
   GeMSentry CRM Tender Inspector — core (data loading + actions)
   Renders one tender as a two-column page body:
     left  (.crm-tp-side) — extracted data, conclusion, response, folder sync
     right (.crm-tp-doc)  — document tabs + full-height PDF / CSV viewer
   Builders for each column live in inspector_side.js and inspector_docs.js,
   which extend this object. The page shell lives in tender_page.js.
   Security: Strictly uses crmEl / crmClear / textContent.
   ========================================================================== */
(function () {
    'use strict';

    const el = window.crmEl;
    const clear = window.crmClear;

    const CRMInspector = {
        activeContainer: null,
        sideEl: null,
        docEl: null,
        context: null, // { tender, crmTender, bidNo, mode, previewData, activeTab, docReady, ... }
        requestCounter: 0,

        async inspect(container, options) {
            if (!container) return;
            const opts = options || {};
            const tender = opts.tender || {};
            const crmTender = opts.crmTender || null;
            const bidNo = (crmTender && crmTender.bid_no) || tender.bid_no || '';

            this.activeContainer = container;
            const reqId = ++this.requestCounter;

            const state = window.CRMState;
            let defaultCompId = (crmTender && crmTender.company_id) || state.inspector.targetCompanyId || 'etspl';
            if (!state.getCompanyById(defaultCompId) && state.companies.length > 0) {
                defaultCompId = state.companies[0].id;
            }
            const sfs = state.getSubfoldersForCompany(defaultCompId);
            const defaultSfId = (crmTender && crmTender.subfolder_id) || state.inspector.targetSubfolderId || (sfs[0] ? String(sfs[0].id) : '');

            this.context = {
                tender: tender,
                crmTender: crmTender,
                bidNo: bidNo,
                mode: opts.mode || 'inbox',
                previewData: null,
                activeTab: { type: 'rfp', id: 'main_rfp' },
                docReady: !bidNo,
                docSignature: null,
                targetCompanyId: defaultCompId,
                targetSubfolderId: defaultSfId ? String(defaultSfId) : '',
                autoFetchAndSync: state.inspector.autoFetchAndSync !== false,
                onMoved: opts.onMoved || null,
                onUpdated: opts.onUpdated || null
            };

            this.renderView();
            if (!bidNo) return;

            await this.loadPreview(reqId);
            if (reqId !== this.requestCounter) return;
            this.context.docReady = true;
            this.renderSide();
            this.renderDoc();

            await this.hydrateFullAnalysis(reqId);
        },

        async loadPreview(reqId) {
            const ctx = this.context;
            try {
                const preview = await window.CRMApi.getTenderPreview(ctx.bidNo);
                if (reqId !== this.requestCounter) return;
                ctx.previewData = preview.preview || preview.data || preview;
                const assignments = this.getAssignments();
                if (assignments.length > 0) {
                    window.CRMState.setAssignmentsForBid(ctx.bidNo, assignments);
                    if (!ctx.crmTender) ctx.crmTender = assignments[0];
                }
            } catch (_err) {
                // Expected for tenders that only exist in the scrape store (404);
                // the page still renders from the list record and its local PDF path.
            }
        },

        async hydrateFullAnalysis(reqId) {
            const ctx = this.context;
            const analysis = ctx.tender && ctx.tender.analysis;
            if (!analysis || !analysis._truncated) return;
            if (!window.CRMState.isAdmin() || typeof window.fetchTenderDetail !== 'function') return;
            const full = await window.fetchTenderDetail(ctx.bidNo);
            if (reqId !== this.requestCounter || !full) return;
            ctx.tender = full;
            this.renderSide();
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

        /** Merged view of the tender: preview record < list record < CRM dossier. */
        getBaseTender() {
            const ctx = this.context;
            const p = ctx.previewData || {};
            return Object.assign({}, p.tender || {}, ctx.tender || {}, ctx.crmTender || {});
        },

        async refreshPreview() {
            const ctx = this.context;
            const preview = await window.CRMApi.getTenderPreview(ctx.bidNo).catch(() => null);
            if (preview) {
                ctx.previewData = preview.preview || preview.data || preview;
            }
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
            await this.refreshPreview();
            return this.getActiveCrmTenderId();
        },

        /* ---------------- Rendering orchestration ---------------- */

        renderView() {
            const container = this.activeContainer;
            if (!container || !this.context) return;
            clear(container);
            container.classList.add('crm-tp-layout');
            container.classList.toggle('is-focus', Boolean(window.CRMState.inspector.focusMode));

            this.sideEl = el('aside', { className: 'crm-tp-side', 'aria-label': 'Tender details' });
            this.docEl = el('section', { className: 'crm-tp-doc', 'aria-label': 'Tender documents' });
            container.appendChild(this.sideEl);
            container.appendChild(this.docEl);
            this.context.docSignature = null;

            this.renderSide();
            this.renderDoc();
        },

        /** Rebuild the left column only, keeping its scroll position. */
        renderSide() {
            const side = this.sideEl;
            if (!side || !this.context) return;
            const scrollTop = side.scrollTop;
            clear(side);
            this.buildSideSections().forEach((node) => {
                if (node) side.appendChild(node);
            });
            side.scrollTop = scrollTop;
        },

        /**
         * Rebuild the document column. Skipped when nothing visible changed so the
         * PDF iframe is not reloaded by unrelated data refreshes.
         */
        renderDoc(force) {
            const doc = this.docEl;
            const ctx = this.context;
            if (!doc || !ctx) return;
            const crmFiles = this.getCrmFiles();
            const embedded = this.getEmbeddedAnnexures();
            const signature = this.docSignatureFor(crmFiles, embedded);
            if (!force && signature === ctx.docSignature) return;
            ctx.docSignature = signature;

            clear(doc);
            const baseTender = this.getBaseTender();
            doc.appendChild(this.buildDocumentTabsBar(baseTender, crmFiles, embedded, window.CRMState.isAdmin()));
            const viewerArea = el('div', { className: 'crm-viewer-container' });
            doc.appendChild(viewerArea);
            if (!ctx.docReady) {
                viewerArea.appendChild(el('div', { className: 'crm-inspector-empty', text: 'Loading document…' }));
                return;
            }
            this.renderActiveDocument(viewerArea, baseTender, crmFiles, embedded);
        },

        docSignatureFor(crmFiles, embedded) {
            const ctx = this.context;
            return JSON.stringify([
                ctx.docReady,
                ctx.activeTab && ctx.activeTab.type,
                ctx.activeTab && String(ctx.activeTab.id),
                this.resolveMainPdfUrl(crmFiles),
                (crmFiles || []).map((f) => f.id),
                (embedded || []).map((a) => a.url || a.href || '')
            ]);
        },

        setActiveTab(tab) {
            this.context.activeTab = tab;
            this.renderDoc(true);
        },

        /** Hide/show the details column so the document uses the whole width. */
        toggleFocus() {
            const state = window.CRMState;
            state.inspector.focusMode = !state.inspector.focusMode;
            if (this.activeContainer) {
                this.activeContainer.classList.toggle('is-focus', state.inspector.focusMode);
            }
            return state.inspector.focusMode;
        },

        /* ---------------- Actions ---------------- */

        async handleStatusOverride(newStatus) {
            const ctx = this.context;
            if (!ctx || !ctx.bidNo || typeof window.overrideStatus !== 'function') return;
            await window.overrideStatus(encodeURIComponent(ctx.bidNo), newStatus);
            if (ctx.tender) ctx.tender = Object.assign({}, ctx.tender, { status: newStatus });
            this.renderSide();
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
                if (ctx.tender) ctx.tender = Object.assign({}, ctx.tender, { status: 'Shortlisted' });
                await window.CRMState.loadBootstrap();
                await this.refreshPreview();
                if (typeof window.loadFinalizedData === 'function') {
                    window.loadFinalizedData();
                }
                window.crmToast(`Moved ${ctx.bidNo} to folder and synced!`, 'success');
                this.renderSide();
                this.renderDoc();
                if (typeof ctx.onMoved === 'function') ctx.onMoved(res);
            } catch (err) {
                window.crmToast(err.message || 'Move to folder failed', 'error');
            } finally {
                if (btnEl && btnEl.isConnected) {
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
                await this.refreshPreview();
                const savedCount = (res.saved_files && res.saved_files.length) || (res.files && res.files.length) || 0;
                window.crmToast(`Fetched GeM documents (${savedCount} file(s) available in dossier).`, 'success');
                this.renderSide();
                this.renderDoc(true);
            } catch (err) {
                window.crmToast(err.message || 'Failed to fetch GeM annexures', 'error');
            } finally {
                if (btnEl && btnEl.isConnected) {
                    btnEl.disabled = false;
                    btnEl.textContent = origText || '⬇️ Fetch GeM Docs';
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
                await this.refreshPreview();
                if (uploaded && uploaded.id) {
                    this.context.activeTab = { type: 'crm_file', id: uploaded.id, file: uploaded };
                }
                window.crmToast(`Uploaded ${file.name} to dossier.`, 'success');
                this.renderSide();
                this.renderDoc(true);
                if (typeof this.context.onUpdated === 'function') this.context.onUpdated(res);
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
        },

        async handleSaveResponse(saveBtn, payload) {
            const isAdmin = window.CRMState.isAdmin();
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving...';
            try {
                const targetId = isAdmin ? await this.ensureInCrmFolder() : this.getActiveCrmTenderId();
                if (!targetId) throw new Error('This tender is not in a CRM folder yet');
                const res = await window.CRMApi.updateDecision(targetId, payload);
                const updated = res.tender || res.crm_tender || payload;
                if (this.context.crmTender) {
                    this.context.crmTender = Object.assign({}, this.context.crmTender, updated);
                }
                window.crmToast('Participation response saved.', 'success');
                if (typeof this.context.onUpdated === 'function') this.context.onUpdated(updated);
            } catch (err) {
                window.crmToast(err.message || 'Failed to save decision', 'error');
            } finally {
                if (saveBtn.isConnected) {
                    saveBtn.disabled = false;
                    saveBtn.textContent = '💾 Save Response';
                }
            }
        }
    };

    window.CRMInspector = CRMInspector;
})();
