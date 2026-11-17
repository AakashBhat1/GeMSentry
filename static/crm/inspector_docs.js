/* ==========================================================================
   GeMSentry CRM Tender Inspector — document column (right side)
   Document tabs (Main RFP, dossier files, embedded GeM links) and the
   full-height viewer (PDF iframe / BOQ CSV table / download fallback).
   Extends window.CRMInspector. Security: crmEl / textContent only.
   ========================================================================== */
(function () {
    'use strict';

    const el = window.crmEl;
    const clear = window.crmClear;

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

    /** Ask the browser PDF viewer to fit the page width to the pane. */
    function withFitWidth(url) {
        if (!url || url.includes('#')) return url;
        return `${url}#view=FitH`;
    }

    function viewerLink(href, text, opts) {
        const o = opts || {};
        const attrs = { href, className: `crm-viewer-link${o.muted ? ' muted' : ''}`, text };
        if (o.download) {
            attrs.download = o.download;
        } else {
            attrs.target = '_blank';
            attrs.rel = 'noopener noreferrer';
        }
        return el('a', attrs);
    }

    const DocsMixin = {
        resolveMainPdfUrl(crmFiles) {
            const ctx = this.context;
            const rfpFile = (crmFiles || []).find((f) => f.file_kind === 'rfp' && f.id);
            if (rfpFile) return `/api/crm/files/${encodeURIComponent(rfpFile.id)}?inline=1`;
            if (ctx.previewData && ctx.previewData.local_pdf_url) return ctx.previewData.local_pdf_url;
            const rawPath = (ctx.crmTender && ctx.crmTender.local_pdf_path) || (ctx.tender && ctx.tender.local_pdf_path) || '';
            if (!rawPath) return null;
            const norm = String(rawPath).replace(/\\/g, '/').replace(/^\/+/, '');
            return norm.startsWith('tenders/') ? '/' + encodeURI(norm) : '/tenders/downloads/' + encodeURI(norm);
        },

        buildDocumentTabsBar(baseTender, crmFiles, embedded, isAdmin) {
            const ctx = this.context;
            const active = ctx.activeTab || {};
            const tabButtons = [];

            const tab = (label, isActive, onClick, title) => el('button', {
                type: 'button',
                className: `crm-doc-tab ${isActive ? 'active' : ''}`,
                title: title || '',
                text: label,
                onClick
            });

            tabButtons.push(tab('📄 Main RFP', active.type === 'rfp', () => this.setActiveTab({ type: 'rfp', id: 'main_rfp' })));

            (crmFiles || []).forEach((file) => {
                if (file.file_kind === 'rfp') return; // covered by the Main RFP tab
                const isActive = active.type === 'crm_file' && String(active.id) === String(file.id);
                tabButtons.push(tab(fileKindLabel(file.file_kind, file.filename), isActive,
                    () => this.setActiveTab({ type: 'crm_file', id: file.id, file }), file.filename));
            });

            const downloadedUrls = new Set((crmFiles || []).map((f) => f.source_url).filter(Boolean));
            (embedded || []).forEach((ann, idx) => {
                const url = ann.url || ann.href || '';
                if (!url || downloadedUrls.has(url)) return;
                const tabId = `emb_${idx}`;
                const kind = ann.file_kind || ann.kind || 'annexure';
                const label = kind === 'boq' ? '📊 GeM BOQ' : (kind === 'tech_spec' ? '📑 GeM Tech Spec' : (kind === 'atc' ? '📎 GeM ATC' : `📎 GeM Doc ${idx + 1}`));
                const isActive = active.type === 'embedded' && active.id === tabId;
                tabButtons.push(tab(label, isActive, () => this.setActiveTab({ type: 'embedded', id: tabId, annexure: ann }), url));
            });

            return el('div', { className: 'crm-doc-tabs-bar' }, [
                el('div', { className: 'crm-doc-tabs-list' }, tabButtons),
                isAdmin ? this.buildDocActions() : null
            ]);
        },

        buildDocActions() {
            const fetchDocsBtn = el('button', {
                type: 'button',
                className: 'btn-crm-toolbar',
                title: 'Extract and download all embedded GeM Tech Specs, BOQs, and ATC documents into this dossier',
                text: '⬇️ Fetch GeM Docs',
                onClick: () => this.handleFetchAllGemDocs(fetchDocsBtn)
            });
            const kindSelect = el('select', { className: 'crm-select-sm', title: 'Document type for upload' }, [
                el('option', { value: 'tech_spec', text: '📑 Tech Spec' }),
                el('option', { value: 'boq', text: '📊 BOQ / CSV' }),
                el('option', { value: 'atc', text: '📎 ATC' }),
                el('option', { value: 'annexure', text: '📄 Annexure' })
            ]);
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
            const uploadBtn = el('button', {
                type: 'button',
                className: 'btn-crm-toolbar is-accent',
                title: 'Upload a Tech Spec, BOQ, ATC or annexure into this dossier',
                text: '➕ Upload',
                onClick: () => fileInput.click()
            });
            return el('div', { className: 'crm-doc-actions' }, [fetchDocsBtn, kindSelect, uploadBtn, fileInput]);
        },

        buildViewerToolbar(label, links, viewerArea) {
            const focusLabel = () => (window.CRMState.inspector.focusMode ? '◨ Show details' : '◧ Hide details');
            const focusBtn = el('button', {
                type: 'button',
                className: 'crm-viewer-btn',
                title: 'Hide the details column so the document uses the full width',
                text: focusLabel(),
                onClick: () => {
                    this.toggleFocus();
                    focusBtn.textContent = focusLabel();
                }
            });
            const fullscreenBtn = el('button', {
                type: 'button',
                className: 'crm-viewer-btn',
                title: 'View this document fullscreen (Esc to exit)',
                text: '⛶ Fullscreen',
                onClick: async () => {
                    try {
                        if (document.fullscreenElement) {
                            await document.exitFullscreen();
                        } else if (viewerArea.requestFullscreen) {
                            await viewerArea.requestFullscreen();
                        } else {
                            throw new Error('Fullscreen is not supported in this browser');
                        }
                    } catch (err) {
                        window.crmToast(err.message || 'Could not toggle fullscreen', 'error');
                    }
                }
            });
            return el('div', { className: 'crm-viewer-toolbar' }, [
                el('span', { className: 'crm-viewer-label', title: label, text: label }),
                el('div', { className: 'crm-viewer-actions' }, [...links, focusBtn, fullscreenBtn])
            ]);
        },

        renderActiveDocument(viewerArea, baseTender, crmFiles, embedded) {
            clear(viewerArea);
            const active = this.context.activeTab || { type: 'rfp' };
            if (active.type === 'crm_file' && active.file) {
                this.renderCrmFile(viewerArea, active.file);
                return;
            }
            if (active.type === 'embedded' && active.annexure) {
                this.renderEmbeddedLink(viewerArea, active.annexure);
                return;
            }
            this.renderMainRfp(viewerArea, baseTender, crmFiles);
        },

        renderCrmFile(viewerArea, f) {
            const fname = String(f.filename || '').toLowerCase();
            const inlineUrl = `/api/crm/files/${encodeURIComponent(f.id)}?inline=1`;
            const downloadUrl = `/api/crm/files/${encodeURIComponent(f.id)}`;
            viewerArea.appendChild(this.buildViewerToolbar(`📎 ${f.filename || 'Document'} (${f.file_kind || 'file'})`, [
                viewerLink(inlineUrl, '↗ Open in Tab'),
                viewerLink(downloadUrl, '⬇️ Download', { download: f.filename || 'document' })
            ], viewerArea));

            if (fname.endsWith('.csv')) {
                const wrap = el('div', { className: 'crm-csv-preview-wrap' }, [
                    el('div', { style: { padding: '1rem', color: 'var(--text-secondary)' }, text: 'Loading BOQ CSV preview...' })
                ]);
                viewerArea.appendChild(wrap);
                this.renderCsvInto(wrap, f.id);
                return;
            }
            if (fname.endsWith('.pdf')) {
                viewerArea.appendChild(el('iframe', { className: 'crm-pdf-frame', src: withFitWidth(inlineUrl), title: f.filename || 'PDF Document' }));
                return;
            }
            viewerArea.appendChild(el('div', { className: 'crm-inspector-empty' }, [
                el('div', { style: { fontSize: '2rem' }, text: '📦' }),
                el('p', { text: `${f.filename} is ready for download.` }),
                el('a', { href: downloadUrl, download: f.filename || 'file', className: 'btn btn-primary', style: { textDecoration: 'none' }, text: '⬇️ Download File' })
            ]));
        },

        async renderCsvInto(wrap, fileId) {
            try {
                const csvData = await window.CRMApi.getCsvPreview(fileId);
                clear(wrap);
                const headers = csvData.headers || [];
                const rows = csvData.rows || [];
                const table = el('table', { className: 'crm-csv-table' });
                if (headers.length > 0) {
                    table.appendChild(el('thead', {}, [el('tr', {}, headers.map((h) => el('th', { text: String(h) })))]));
                }
                table.appendChild(el('tbody', {}, rows.map((row) =>
                    el('tr', {}, (Array.isArray(row) ? row : Object.values(row)).map((cell) => el('td', { text: String(cell ?? '') })))
                )));
                wrap.appendChild(table);
            } catch (err) {
                clear(wrap);
                wrap.appendChild(el('div', { style: { padding: '1rem', color: 'var(--failed-color)' }, text: `Could not preview CSV: ${err.message}` }));
            }
        },

        renderEmbeddedLink(viewerArea, ann) {
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
                    el('a', { href: url, target: '_blank', rel: 'noopener noreferrer', className: 'btn btn-secondary', style: { textDecoration: 'none' }, text: '↗ Open GeM Link' })
                ])
            ]));
        },

        renderMainRfp(viewerArea, baseTender, crmFiles) {
            const ctx = this.context;
            const pdfUrl = this.resolveMainPdfUrl(crmFiles);
            if (pdfUrl) {
                viewerArea.appendChild(this.buildViewerToolbar(`📄 Main RFP · ${ctx.bidNo || 'PDF'}`, [
                    viewerLink(pdfUrl, '↗ Open in Tab'),
                    baseTender.pdf_url ? viewerLink(baseTender.pdf_url, '🌐 Portal', { muted: true }) : null
                ], viewerArea));
                viewerArea.appendChild(el('iframe', { className: 'crm-pdf-frame', src: withFitWidth(pdfUrl), title: `RFP PDF ${ctx.bidNo || ''}` }));
                return;
            }
            viewerArea.appendChild(el('div', { className: 'crm-inspector-empty' }, [
                el('div', { style: { fontSize: '2.2rem' }, text: '📡' }),
                el('h4', { style: { fontSize: '0.96rem', fontWeight: '700' }, text: 'No RFP PDF downloaded for this tender' }),
                el('p', { style: { fontSize: '0.82rem', maxWidth: '380px' }, text: 'Auto-dropped tenders and BHEL / ISRO listings link to the portal page instead of a PDF. Fetch it from GeM or open the portal listing.' }),
                el('div', { style: { display: 'flex', gap: '0.6rem', marginTop: '0.5rem' } }, [
                    ctx.bidNo && window.CRMState.isAdmin() ? el('button', {
                        type: 'button',
                        className: 'btn btn-primary',
                        text: '🔄 Fetch PDF & Re-analyze',
                        onClick: () => {
                            if (typeof window.reanalyzeBid === 'function') window.reanalyzeBid(encodeURIComponent(ctx.bidNo));
                        }
                    }) : null,
                    baseTender.pdf_url ? el('a', { href: baseTender.pdf_url, target: '_blank', rel: 'noopener noreferrer', className: 'btn btn-secondary', style: { textDecoration: 'none' }, text: '🌐 Open Portal Listing' }) : null
                ])
            ]));
        }
    };

    Object.assign(window.CRMInspector, DocsMixin);
})();
