/* ==========================================================================
   GeMSentry CRM Tender Inspector — details column (left side)
   Header, conclusion (recommendation, scores, eligibility, reasons), key
   extracted facts, score breakdown, participation response, folder & sync.
   Extends window.CRMInspector. Security: crmEl / textContent only.
   ========================================================================== */
(function () {
    'use strict';

    const el = window.crmEl;

    const REC_CLASS = { Pursue: 'is-pursue', Review: 'is-review', Drop: 'is-drop' };
    const EMPTY_VALUES = new Set(['', 'na', 'n/a', 'none', 'null', 'undefined']);

    function formatInr(val) {
        const num = Number(val);
        if (!num || isNaN(num) || num <= 0) return '';
        if (num >= 10000000) return `₹${(num / 10000000).toFixed(2)} Cr`;
        if (num >= 100000) return `₹${(num / 100000).toFixed(2)} Lakh`;
        return `₹${num.toLocaleString('en-IN')}`;
    }

    function hasValue(v) {
        return v !== undefined && v !== null && !EMPTY_VALUES.has(String(v).trim().toLowerCase());
    }

    function humanize(key) {
        return String(key || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    }

    function card(title, children, extraClass) {
        return el('section', { className: `crm-tp-card${extraClass ? ' ' + extraClass : ''}` }, [
            title ? el('h3', { className: 'crm-tp-card-title', text: title }) : null,
            ...children
        ]);
    }

    function deadlineText(endDate) {
        if (!endDate) return '';
        const datePart = String(endDate);
        const days = typeof window.gemDaysToDeadline === 'function' ? window.gemDaysToDeadline(endDate) : null;
        if (days === null || days === undefined) return datePart;
        if (days < 0) return `${datePart} · expired`;
        return `${datePart} · ${days} day${days === 1 ? '' : 's'} left`;
    }

    const SideMixin = {
        buildSideSections() {
            const ctx = this.context;
            const state = window.CRMState;
            const isAdmin = state.isAdmin();
            const isVendor = state.isVendor();
            const base = this.getBaseTender();
            const analysis = base.analysis || {};
            const redacted = isVendor && !hasValue(base.est_value_inr) && !hasValue(analysis.score);
            const showResponse = ctx.mode === 'folders' || !isAdmin || Boolean(ctx.crmTender);

            return [
                this.buildSideHeader(base, isAdmin),
                redacted ? null : this.buildConclusion(analysis),
                this.buildFacts(base, analysis, redacted),
                redacted ? null : this.buildBreakdown(analysis),
                showResponse ? this.buildParticipationCard(base, isAdmin) : null,
                isAdmin ? this.buildFolderCard() : null
            ];
        },

        buildSideHeader(base, isAdmin) {
            const ctx = this.context;
            const analysis = base.analysis || {};
            const rec = analysis.recommendation || '';
            const pills = [];
            if (rec) pills.push(el('span', { className: `crm-tp-rec-pill ${REC_CLASS[rec] || ''}`, text: rec }));
            pills.push(el('span', { className: 'crm-row-bid', text: ctx.bidNo || base.bid_no || 'Confidential Bid' }));
            if (base.source_name) pills.push(el('span', { className: 'crm-pill crm-pill-neutral', text: base.source_name }));
            if (base.sl_no) pills.push(el('span', { className: 'crm-pill crm-pill-synced', text: `SL #${base.sl_no}` }));
            this.getAssignments().forEach((asgn) => {
                const cName = asgn.company_name || asgn.company_id || '';
                const sfName = asgn.subfolder_name || asgn.subfolder_slug || '';
                if (cName) {
                    pills.push(el('span', { className: 'crm-pill crm-pill-synced', title: asgn.dossier_path || '', text: `📁 ${cName}${sfName ? ' / ' + sfName : ''}` }));
                }
            });

            const buyer = [base.department, analysis.buyer_org || base.organisation].filter(hasValue);
            const uniqueBuyer = buyer.filter((v, i) => buyer.indexOf(v) === i).join(' · ');

            // A plain div: the dashboard styles bare <header> elements as the sticky app bar.
            return el('div', { className: 'crm-tp-head' }, [
                el('div', { className: 'crm-tp-pills' }, pills),
                el('h2', { className: 'crm-tp-title', title: base.title || '', text: base.title || 'Untitled Tender' }),
                uniqueBuyer ? el('div', { className: 'crm-tp-buyer', text: uniqueBuyer }) : null,
                isAdmin ? this.buildTriageButtons(base.status || 'Pending Review') : null
            ]);
        },

        buildTriageButtons(statusText) {
            const btn = (label, value, activeClass) => el('button', {
                type: 'button',
                className: `crm-tp-seg-btn ${statusText === value ? activeClass : ''}`,
                'aria-pressed': String(statusText === value),
                text: label,
                onClick: () => this.handleStatusOverride(value)
            });
            return el('div', { className: 'crm-tp-seg', role: 'group', 'aria-label': 'Tender status' }, [
                btn('🟢 Shortlist', 'Shortlisted', 'active-short'),
                btn('🟡 Pending', 'Pending Review', 'active-pending'),
                btn('🔴 Reject', 'Rejected', 'active-rejected')
            ]);
        },

        buildConclusion(analysis) {
            if (!analysis || Object.keys(analysis).length === 0) return null;
            const rec = analysis.recommendation || 'Not scored';
            const stat = (label, value) => el('div', { className: 'crm-tp-stat' }, [
                el('span', { className: 'crm-tp-stat-val', text: hasValue(value) ? String(value) : '—' }),
                el('span', { className: 'crm-tp-stat-label', text: label })
            ]);

            const flags = [];
            if (analysis.auto_reject) flags.push('Auto-rejected by keyword / category rules');
            if (analysis.is_expired) flags.push('Bid deadline has passed');
            if (analysis.high_emd_hold) flags.push('High EMD — check MSE / Startup exemption');
            if (analysis.pages_truncated) flags.push(`Only ${analysis.pages_read} of ${analysis.pages_total} pages were read`);

            const elig = analysis.eligibility || {};
            const reasons = Array.isArray(analysis.reasons) ? analysis.reasons : [];
            const reasonsLoading = !reasons.length && analysis._truncated;

            return card('Conclusion', [
                el('div', { className: `crm-tp-verdict ${REC_CLASS[rec] || ''}` }, [
                    el('span', { className: 'crm-tp-verdict-label', text: 'Recommendation' }),
                    el('span', { className: 'crm-tp-verdict-val', text: rec })
                ]),
                el('div', { className: 'crm-tp-stats' }, [
                    stat('Priority', analysis.priority_score),
                    stat('Fit', analysis.fit_score),
                    stat('Terms', analysis.terms_score !== undefined ? analysis.terms_score : analysis.score)
                ]),
                elig.verdict ? el('div', { className: 'crm-tp-elig' }, [
                    el('span', { className: `crm-pill ${elig.verdict === 'eligible' ? 'crm-pill-pursue' : (elig.verdict === 'ineligible' ? 'crm-pill-drop' : 'crm-pill-review')}`, text: `Eligibility: ${humanize(elig.verdict)}` }),
                    elig.detail ? el('span', { className: 'crm-tp-elig-detail', text: elig.detail }) : null
                ]) : null,
                flags.length ? el('ul', { className: 'crm-tp-flags' }, flags.map((f) => el('li', { text: `⚠️ ${f}` }))) : null,
                reasons.length ? el('ul', { className: 'crm-tp-reasons' }, reasons.map((r) => el('li', { text: String(r) }))) : null,
                reasonsLoading ? el('p', { className: 'crm-tp-muted', text: 'Loading full analysis…' }) : null
            ], 'crm-tp-conclusion');
        },

        buildFacts(base, analysis, redacted) {
            const rows = [];
            const add = (label, value, wide) => {
                if (hasValue(value)) rows.push({ label, value: String(value), wide });
            };

            add('Deadline', deadlineText(base.end_date));
            add('Published', base.start_date);
            add('Quantity', base.quantity);
            add('Consignee State', analysis.consignee_state);
            if (!redacted) {
                const est = formatInr(base.est_value_inr || analysis.est_value_inr);
                add('Estimated Value', est ? `${est}${analysis.est_value_estimated ? ' (estimated)' : ''}` : '');
                add('EMD', analysis.emd_status || formatInr(analysis.emd_amount || base.emd_amount));
                add('ePBG', hasValue(analysis.epbg_required) ? `${analysis.epbg_required}${hasValue(analysis.epbg_percentage) ? ' · ' + analysis.epbg_percentage + '%' : ''}` : '');
                add('MSE Exemption', analysis.mse_exemption);
                add('Startup Exemption', analysis.startup_exemption);
                add('Pre-bid Meeting', hasValue(analysis.pre_bid_required) ? `${analysis.pre_bid_required}${hasValue(analysis.pre_bid_date) ? ' · ' + analysis.pre_bid_date : ''}` : '');
                add('Make in India', analysis.mii_required);
                add('MSE Preference', analysis.mse_pref);
            }
            const line = analysis.business_line || {};
            add('Business Line', line.label ? `${line.label}${(line.matched_keywords || []).length ? ' — ' + line.matched_keywords.join(', ') : ''}` : base.work_category, true);
            add('Item / Category', analysis.item_category || base.item_category || base.primary_item, true);
            if (hasValue(analysis.pages_total)) {
                const conf = hasValue(analysis.confidence) ? ` · ${Math.round(Number(analysis.confidence) * 100)}% fields parsed` : '';
                add('Document Read', `${analysis.pages_read}/${analysis.pages_total} pages${conf}`);
            }
            if (base.tech_spec_url) rows.push({ label: 'Cloud Spec', value: '↗ Open cloud spec document', href: base.tech_spec_url, wide: true });

            if (rows.length === 0) return null;
            return card('Key Details', [
                el('dl', { className: 'crm-tp-facts' }, rows.map((r) => el('div', { className: `crm-tp-fact${r.wide ? ' is-wide' : ''}` }, [
                    el('dt', { text: r.label }),
                    el('dd', {}, [r.href
                        ? el('a', { href: r.href, target: '_blank', rel: 'noopener noreferrer', text: r.value })
                        : r.value])
                ])))
            ]);
        },

        buildBreakdown(analysis) {
            const items = Array.isArray(analysis.breakdown) ? analysis.breakdown : [];
            if (items.length === 0) return null;
            return el('details', { className: 'crm-tp-card crm-tp-breakdown' }, [
                el('summary', { className: 'crm-tp-card-title', text: `Score Breakdown (${items.length} criteria)` }),
                el('ul', {}, items.map((b) => el('li', {}, [
                    el('div', { className: 'crm-tp-bd-head' }, [
                        el('span', { text: humanize(b.criterion) }),
                        el('span', { className: 'crm-tp-bd-pts', text: hasValue(b.points) ? `${b.points} pts` : '' })
                    ]),
                    b.detail ? el('div', { className: 'crm-tp-bd-detail', text: b.detail }) : null
                ])))
            ]);
        },

        buildParticipationCard(base, isAdmin) {
            if (!this.getActiveCrmTenderId() && !isAdmin) return null;
            let currentDecision = String(base.vendor_decision || 'PENDING').toUpperCase();
            const choices = [
                { value: 'PENDING', label: '⏳ Pending', cls: 'active-clarify' },
                { value: 'YES', label: '✅ Participate', cls: 'active-yes' },
                { value: 'NO', label: '❌ Skip', cls: 'active-no' },
                { value: 'CLARIFICATION', label: '💬 Clarify', cls: 'active-clarify' }
            ];
            const buttons = choices.map((c) => el('button', {
                type: 'button',
                className: `btn-decision-choice ${currentDecision === c.value ? c.cls : ''}`,
                text: c.label,
                onClick: () => {
                    currentDecision = c.value;
                    buttons.forEach((b, i) => {
                        b.className = `btn-decision-choice ${currentDecision === choices[i].value ? choices[i].cls : ''}`;
                    });
                }
            }));

            const remarksInput = el('textarea', {
                className: 'crm-tp-remarks',
                rows: '2',
                placeholder: 'Remarks, technical deviations, readiness, or clarification needed…'
            });
            remarksInput.value = base.vendor_remarks || '';

            const stageOptions = [
                ['assigned', 'Assigned'], ['review', 'Under Review'], ['shortlisted', 'Shortlisted'],
                ['participating', 'Participating'], ['won', 'Won'], ['lost', 'Lost'], ['rejected', 'Rejected / Dropped']
            ];
            const normalizedStage = String(base.stage || 'review').toLowerCase().trim();
            const stageSelect = isAdmin ? el('select', { className: 'crm-select-sm', title: 'Tender CRM Stage' }, stageOptions.map(([value, label]) => el('option', {
                value, selected: normalizedStage === value, text: `Stage: ${label}`
            }))) : null;

            const saveBtn = el('button', {
                type: 'button',
                className: 'btn btn-primary crm-tp-save',
                text: '💾 Save Response',
                onClick: () => {
                    const payload = { vendor_decision: currentDecision || 'PENDING', vendor_remarks: remarksInput.value.trim() };
                    if (stageSelect) payload.stage = stageSelect.value;
                    this.handleSaveResponse(saveBtn, payload);
                }
            });

            return card('Your Response', [
                el('div', { className: 'crm-decision-btns' }, buttons),
                remarksInput,
                el('div', { className: 'crm-tp-row' }, [stageSelect, saveBtn])
            ]);
        },

        buildFolderCard() {
            const ctx = this.context;
            const state = window.CRMState;
            if (state.companies.length === 0) return null;

            const companySelect = el('select', {
                className: 'crm-select-sm',
                'aria-label': 'Target company',
                onChange: (e) => {
                    ctx.targetCompanyId = e.target.value;
                    state.inspector.targetCompanyId = ctx.targetCompanyId;
                    const sfs = state.getSubfoldersForCompany(ctx.targetCompanyId);
                    ctx.targetSubfolderId = sfs[0] ? String(sfs[0].id) : '';
                    state.inspector.targetSubfolderId = ctx.targetSubfolderId;
                    this.renderSide();
                }
            }, state.companies.map((c) => el('option', { value: c.id, selected: c.id === ctx.targetCompanyId, text: `🏢 ${c.name}` })));

            const subfolders = state.getSubfoldersForCompany(ctx.targetCompanyId);
            if (!ctx.targetSubfolderId && subfolders.length > 0) ctx.targetSubfolderId = String(subfolders[0].id);
            const subfolderSelect = el('select', {
                className: 'crm-select-sm',
                'aria-label': 'Target subfolder',
                onChange: (e) => {
                    ctx.targetSubfolderId = e.target.value;
                    state.inspector.targetSubfolderId = ctx.targetSubfolderId;
                }
            }, subfolders.map((sf) => el('option', { value: String(sf.id), selected: String(sf.id) === String(ctx.targetSubfolderId), text: `📂 ${sf.name}` })));

            const newSfBtn = el('button', {
                type: 'button',
                className: 'btn-crm-toolbar',
                title: 'Create a new subfolder in this company',
                text: '+ New',
                onClick: () => this.handleNewSubfolder()
            });

            const autoSyncCheck = el('input', {
                type: 'checkbox',
                checked: ctx.autoFetchAndSync,
                onChange: (e) => {
                    ctx.autoFetchAndSync = e.target.checked;
                    state.inspector.autoFetchAndSync = ctx.autoFetchAndSync;
                }
            });

            const moveBtn = el('button', {
                type: 'button',
                className: 'btn btn-primary',
                text: '📁 Move to Folder & Sync',
                onClick: () => this.handleMoveAndSync(moveBtn)
            });
            const removeBtn = (ctx.mode === 'folders' && this.getActiveCrmTenderId())
                ? el('button', { type: 'button', className: 'btn btn-secondary crm-tp-danger', text: '🗑️ Remove', onClick: () => this.handleRemoveFromFolder() })
                : null;

            return card('Folder & Sync', [
                el('div', { className: 'crm-tp-row' }, [companySelect, subfolderSelect, newSfBtn]),
                el('label', {
                    className: 'crm-tp-check',
                    title: 'Automatically download GeM Tech Spec / BOQ / ATC docs, assign Serial Number, and sync to Google Sheet / Drive / Excel'
                }, [autoSyncCheck, el('span', { text: 'Auto-fetch GeM specs/BOQ & sync Sheet/Drive/Excel' })]),
                el('div', { className: 'crm-tp-row' }, [moveBtn, removeBtn])
            ]);
        },

        async handleNewSubfolder() {
            const ctx = this.context;
            const name = await window.crmPrompt({
                title: 'New Subfolder',
                message: 'Enter new subfolder name:',
                placeholder: 'e.g. lidar_drones, solar_pumps, sandeep',
                confirmText: 'Create Subfolder'
            });
            if (!name) return;
            try {
                const res = await window.CRMApi.createSubfolder({ company_id: ctx.targetCompanyId, name });
                await window.CRMState.loadBootstrap();
                const created = res.subfolder || res;
                if (created && created.id) ctx.targetSubfolderId = String(created.id);
                window.crmToast(`Subfolder "${name}" created.`, 'success');
                this.renderSide();
            } catch (err) {
                window.crmToast(err.message || 'Failed to create subfolder', 'error');
            }
        }
    };

    Object.assign(window.CRMInspector, SideMixin);
})();
