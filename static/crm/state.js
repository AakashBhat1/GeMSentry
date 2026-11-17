/* ==========================================================================
   GeMSentry CRM Centralized State Store & Mode Router
   Security: Strictly uses createElement / textContent / replaceChildren.
   ========================================================================== */
(function () {
    'use strict';

    const listeners = {};

    const CRMState = {
        initialized: false,
        currentUser: null,
        activeMode: 'inbox', // 'inbox' | 'folders' | 'master' | 'admin'
        companies: [],
        subfolders: [],
        counts: { by_company: {}, by_subfolder: {} },
        assignmentsByBid: {},
        inbox: {
            selectedBidNo: null,
            selectedBidNos: new Set(),
            filtersCollapsed: false,
            compactHeaders: true,
            visibleTenders: [],
            targetCompanyId: 'etspl',
            targetSubfolderId: '',
            autoFetchAndSync: true
        },
        folders: {
            selectedCompanyId: null,
            selectedSubfolderId: null,
            selectedSubfolderSlug: null,
            searchQuery: '',
            stageFilter: '',
            decisionFilter: '',
            tenders: [],
            selectedCrmTenderId: null,
            selectedTenderObj: null,
            loading: false
        },
        inspector: {
            currentBidNo: null,
            currentCrmTender: null,
            activeDocTab: 'rfp',
            previewData: null,
            targetCompanyId: 'etspl',
            targetSubfolderId: '',
            autoFetchAndSync: true,
            openPanel: null, // 'folder' | 'response' | 'details' | null — collapsed by default so the PDF keeps the space
            focusMode: false
        },
        admin: {
            users: [],
            editingCompany: null,
            editingUser: null,
            migrationReport: null
        },

        on(event, fn) {
            if (!listeners[event]) listeners[event] = [];
            listeners[event].push(fn);
        },

        emit(event, payload) {
            const list = listeners[event] || [];
            list.forEach((fn) => {
                try {
                    fn(payload);
                } catch (err) {
                    console.error(`CRMState listener error (${event}):`, err);
                }
            });
        },

        isAdmin() {
            const u = this.currentUser || window.__gemsentryCurrentUser;
            return !u || u.role === 'admin';
        },

        isScopedUser() {
            return !this.isAdmin();
        },

        isVendor() {
            const u = this.currentUser || window.__gemsentryCurrentUser;
            return Boolean(u && u.role === 'vendor');
        },

        getCompanyById(companyId) {
            return (this.companies || []).find((c) => c.id === companyId) || null;
        },

        getSubfoldersForCompany(companyId) {
            if (!companyId) return [];
            const list = (this.subfolders || []).filter((sf) => sf.company_id === companyId);
            const u = this.currentUser || window.__gemsentryCurrentUser;
            if (u && u.role !== 'admin' && u.subfolder_scope) {
                const scope = String(u.subfolder_scope).toLowerCase().trim();
                return list.filter((sf) => {
                    const slug = String(sf.slug || '').toLowerCase();
                    const name = String(sf.name || '').toLowerCase();
                    return slug === scope || name === scope || String(sf.id) === scope;
                });
            }
            return list;
        },

        getCompanyCount(companyId) {
            const byComp = (this.counts && this.counts.by_company) || {};
            if (byComp[companyId] !== undefined) return Number(byComp[companyId]) || 0;
            const comp = this.getCompanyById(companyId);
            if (comp && comp.tender_count !== undefined) return Number(comp.tender_count) || 0;
            return 0;
        },

        getSubfolderCount(subfolderId) {
            const bySf = (this.counts && this.counts.by_subfolder) || {};
            if (bySf[subfolderId] !== undefined) return Number(bySf[subfolderId]) || 0;
            if (bySf[String(subfolderId)] !== undefined) return Number(bySf[String(subfolderId)]) || 0;
            const sf = (this.subfolders || []).find((s) => String(s.id) === String(subfolderId));
            if (sf && sf.tender_count !== undefined) return Number(sf.tender_count) || 0;
            return 0;
        },

        getAssignmentsForBid(bidNo) {
            if (!bidNo) return [];
            return this.assignmentsByBid[bidNo] || [];
        },

        setAssignmentsForBid(bidNo, list) {
            if (!bidNo) return;
            this.assignmentsByBid[bidNo] = Array.isArray(list) ? list : [];
        },

        applyBootstrapData(data) {
            if (!data) return;
            if (data.user) {
                this.currentUser = data.user;
                window.__gemsentryCurrentUser = data.user;
                if (typeof window.updateUserSessionUI === 'function') {
                    window.updateUserSessionUI(data.user);
                }
            } else if (window.__gemsentryCurrentUser) {
                this.currentUser = window.__gemsentryCurrentUser;
            }

            const rawCompanies = data.companies || [];
            const rawSubfolders = data.subfolders ? data.subfolders.slice() : [];
            const seenSfIds = new Set(rawSubfolders.map((s) => String(s.id)));

            rawCompanies.forEach((comp) => {
                if (Array.isArray(comp.subfolders)) {
                    comp.subfolders.forEach((sf) => {
                        const sfId = String(sf.id);
                        if (!seenSfIds.has(sfId)) {
                            seenSfIds.add(sfId);
                            rawSubfolders.push(Object.assign({ company_id: comp.id }, sf));
                        }
                    });
                }
            });

            this.companies = rawCompanies;
            this.subfolders = rawSubfolders;
            if (data.counts) {
                this.counts = {
                    by_company: data.counts.by_company || {},
                    by_subfolder: data.counts.by_subfolder || {}
                };
            }
            if (data.assignments_by_bid && typeof data.assignments_by_bid === 'object') {
                this.assignmentsByBid = Object.assign({}, this.assignmentsByBid, data.assignments_by_bid);
            }

            if (this.companies.length > 0) {
                const defaultComp = this.currentUser && this.currentUser.company_id
                    ? (this.getCompanyById(this.currentUser.company_id) || this.companies[0])
                    : this.companies[0];

                if (!this.folders.selectedCompanyId || !this.getCompanyById(this.folders.selectedCompanyId)) {
                    this.folders.selectedCompanyId = defaultComp.id;
                }
                if (!this.inbox.targetCompanyId || !this.getCompanyById(this.inbox.targetCompanyId)) {
                    this.inbox.targetCompanyId = this.companies[0].id;
                }
                if (!this.inspector.targetCompanyId || !this.getCompanyById(this.inspector.targetCompanyId)) {
                    this.inspector.targetCompanyId = this.companies[0].id;
                }
            }

            this.updateModeBarPermissions();
            this.emit('bootstrap:loaded', data);
        },

        async loadBootstrap() {
            try {
                const data = await window.CRMApi.getBootstrap();
                this.applyBootstrapData(data);
                this.initialized = true;
                return data;
            } catch (err) {
                console.warn('CRM bootstrap load warning:', err.message || err);
                return null;
            }
        },

        updateModeBarPermissions() {
            const adminOnly = this.isAdmin();
            const tabInbox = document.getElementById('crmModeTabInbox');
            const tabFolders = document.getElementById('crmModeTabFolders');
            const tabMaster = document.getElementById('crmModeTabMaster');
            const tabAdmin = document.getElementById('crmModeTabAdmin');
            const inboxActions = document.getElementById('crmInboxToolbarActions');

            if (tabInbox) tabInbox.style.display = adminOnly ? 'inline-flex' : 'none';
            if (tabFolders) tabFolders.style.display = 'inline-flex';
            if (tabMaster) tabMaster.style.display = adminOnly ? 'inline-flex' : 'none';
            if (tabAdmin) tabAdmin.style.display = adminOnly ? 'inline-flex' : 'none';
            if (inboxActions) inboxActions.style.display = (adminOnly && this.activeMode === 'inbox') ? 'flex' : 'none';

            // Update folder total count badge
            const foldersBadge = document.getElementById('crmFoldersCountBadge');
            if (foldersBadge) {
                const totalFoldersCount = Object.values((this.counts && this.counts.by_company) || {})
                    .reduce((acc, v) => acc + (Number(v) || 0), 0);
                foldersBadge.textContent = String(totalFoldersCount);
            }
        },

        switchMode(mode) {
            const isAdmin = this.isAdmin();
            let targetMode = mode || (isAdmin ? 'inbox' : 'folders');
            if (!isAdmin && targetMode !== 'folders') {
                targetMode = 'folders';
            }

            if (targetMode === 'master') {
                if (typeof window.openFinalizedModal === 'function') {
                    window.openFinalizedModal('study');
                }
                return;
            }

            this.activeMode = targetMode;

            // Update active tab styling
            const tabMap = {
                inbox: document.getElementById('crmModeTabInbox'),
                folders: document.getElementById('crmModeTabFolders'),
                admin: document.getElementById('crmModeTabAdmin')
            };
            Object.entries(tabMap).forEach(([k, btn]) => {
                if (btn) btn.classList.toggle('active', k === targetMode);
            });

            const mainContainer = document.getElementById('mainContainer');
            const foldersSection = document.getElementById('crmFoldersWorkspace');
            const adminSection = document.getElementById('crmAdminWorkspace');
            const inboxActions = document.getElementById('crmInboxToolbarActions');

            if (mainContainer) mainContainer.style.display = targetMode === 'inbox' ? 'flex' : 'none';
            if (foldersSection) foldersSection.style.display = targetMode === 'folders' ? 'block' : 'none';
            if (adminSection) adminSection.style.display = targetMode === 'admin' ? 'block' : 'none';
            if (inboxActions) inboxActions.style.display = (isAdmin && targetMode === 'inbox') ? 'flex' : 'none';

            if (targetMode === 'folders' && window.CRMFoldersView) {
                window.CRMFoldersView.activate();
            } else if (targetMode === 'admin' && window.CRMAdminView) {
                window.CRMAdminView.activate();
            } else if (targetMode === 'inbox' && window.CRMInboxView) {
                window.CRMInboxView.activate();
            }

            this.emit('mode:changed', targetMode);
        },

        reset() {
            this.initialized = false;
            this.currentUser = null;
            this.inbox.selectedBidNos.clear();
        }
    };

    window.CRMState = CRMState;
    window.switchCrmMode = function (mode) {
        CRMState.switchMode(mode);
    };
})();
