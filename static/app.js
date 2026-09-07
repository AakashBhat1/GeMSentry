        // Global Fetch Interceptor for Token Authentication
        const originalFetch = window.fetch;
        window.fetch = async function(url, options = {}) {
            options = options || {};
            options.headers = options.headers || {};
            const token = localStorage.getItem('gemsentry_auth_token');
            if (token) {
                if (options.headers instanceof Headers) {
                    options.headers.set('Authorization', `Bearer ${token}`);
                } else {
                    options.headers['Authorization'] = `Bearer ${token}`;
                }
            }
            const res = await originalFetch(url, options);
            if (res.status === 401 && typeof url === 'string' && !url.includes('/api/auth/')) {
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

        // Default filters state
        let currentKeyword = 'all';
        let currentStatus = 'all';
        let currentRec = 'all';
        let currentBusinessLine = 'all';
        let currentValueBand = 'all';
        let currentDeadlineBand = 'actionable';
        let currentDate = 'all';   // 'all' | 'YYYY-MM-DD' | 'unknown'
        let currentSource = 'all'; // 'all' | source_id
        let searchStr = '';
        let tenders = [];
        let pollingInterval = null;
        let previousStatus = null;
        let activeSessionLogPath = null;
        let knownKeywords = [];
        let scoringConfig = null;
        let companyProfile = null;

        // --- Live Excel & Today's Triage state -------------------------------
        let currentTodayTriage = 'all'; // 'all' | 'proceed' | 'preview' | 'reject'
        let liveExcelState = {
            isActive: false,
            status: 'idle',
            secondsRemaining: 0,
            timeoutSeconds: 600,
            updateCount: 0,
            tenderBids: new Set(),
            lastSavedFilename: null,
            todaySavedFiles: [],
            lastMessage: ''
        };

        // id -> {name, category} for every configured portal, so the filter can
        // label a source even when no tender from it has landed yet.
        let sourceCatalog = {};

        // --- Grouped rendering state -----------------------------------------
        // The list is sectioned because a flat 2,500-card render is both
        // unreadable and slow: only open sections build DOM.
        const GROUP_PAGE_SIZE = 25;
        let currentGroupBy = 'recommendation';
        let renderedGroups = [];             // last computed [{key,label,...,items}]
        const openGroupKeys = new Set();     // section keys the user has expanded
        const groupLimits = new Map();       // section key -> cards currently shown

        /* Scraped titles and department names are arbitrary text that gets
           interpolated into HTML. Escape everything that comes from a tender. */
        function escapeHtml(value) {
            if (value === null || value === undefined) return '';
            return String(value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        const DEFAULT_SCORING_CONFIG = {
            version: 1,
            weights: {
                emd: 2.0,
                startup_exemption: 1.5,
                mse_exemption: 1.5,
                prebid: 0.5,
                date_window: 1.0,
                epbg: 0.5
            },
            emd: {
                free_threshold_inr: 200000,
                max_penalty_threshold_inr: 2000000
            },
            date_window: {
                min_days: 7,
                full_credit_days: 14
            },
            epbg: {
                free_threshold_pct: 3.0,
                max_penalty_pct: 10.0
            },
            unknown_subscore: 0.5,
            status_thresholds: {
                shortlist_min: 70,
                reject_max: 40
            },
            fit: {
                weights: {
                    relevance: 3.0,
                    serviceability: 1.0,
                    value_fit: 1.0,
                    buyer_affinity: 1.0,
                    eligibility_factor: 2.0
                },
                fit_min: 60,
                unknown_buyer_subscore: 0.4,
                turnover_gap_subscore: 0.3,
                weak_relevance_subscore: 0.5
            }
        };

        const DEFAULT_COMPANY_PROFILE = {
            version: 1,
            company: {
                legal_name: "Earnest Tactical Solutions Pvt. Ltd.",
                short_name: "ETSPL",
                incorporation_ym: "2020-03",
                hq_state: "Haryana",
                hq_city: "Gurgaon"
            },
            eligibility: {
                annual_turnover_inr: 1800000,
                years_experience: 6,
                registrations: {
                    mse_udyam: true,
                    startup_dpiit: true
                },
                certifications: ["ISO 9001:2015"],
                can_meet_make_in_india: true,
                max_order_value_inr: null,
                turnover_waivable_by_exemption: true
            },
            serviceability: {
                all_india: true,
                soft_avoid_states: ["Tamil Nadu", "Kerala", "Karnataka", "Andhra Pradesh", "Telangana", "Puducherry"],
                soft_avoid_reason: "Local monopoly on these product categories in South India",
                soft_avoid_penalty: 0.5
            },
            business_lines: [
                { id: "drone", label: "Drone / UAV", priority: 1.0, keywords: ["drone", "drones", "uav", "unmanned aerial", "multirotor", "quadcopter", "aerostat", "gis", "mapping", "surveillance", "reconnaissance"] },
                { id: "power_supply", label: "Power Supply / Electrical", priority: 1.0, keywords: ["power supply", "ac-dc", "ac dc", "rectifier", "alternator", "amplifier", "ups", "voltage regulator", "lvpsu", "hvpsu", "power unit", "static convertor", "power conversion", "battery charger", "solid state power amplifier", "power system", "psu"] },
                { id: "ai_it", label: "AI / IT / Electronics", priority: 1.0, keywords: ["artificial intelligence", "ai based", "ai-based", "software", "server", "radar", "cctv", "camera", "connectors", "harness", "rugged laptop", "military grade", "repairing", "electronics", "data acquisition", "network switch", "router", "display", "laptop", "notebook"] },
                { id: "gis_dgps_survey", label: "DGPS & GIS Survey / Geospatial", priority: 1.0, keywords: ["dgps", "dgps survey", "gis survey", "gis mapping", "topographic survey", "cadastral survey", "drone survey", "lidar survey", "total station survey", "geospatial survey", "land survey", "contour survey", "rtk survey", "gnss survey"] }
            ],
            buyer_affinity: {
                "INDIAN AIR FORCE": 1.0,
                "INDIAN ARMY": 0.85,
                "INDIAN NAVY": 0.75,
                "HAL": 0.75,
                "DRDO": 0.65,
                "BHARAT PETROLEUM": 0.5,
                "DEFENCE": 0.6
            },
            value_preference: {
                sweet_min_inr: 500000,
                sweet_max_inr: 30000000
            },
            avoid_rules: {
                gem_q2_category: true,
                prefer_custom_bids: true
            }
        };

        async function loadScoringConfig() {
            try {
                const response = await fetch('/api/scoring-config');
                const data = await response.json();
                if (data && !data.error) {
                    scoringConfig = data;
                    if (tenders && tenders.length > 0) {
                        updateBadges();
                        filterData();
                    }
                }
            } catch (err) {
                console.error("Failed to load scoring config from API:", err);
            }
        }

        async function loadCompanyProfile() {
            try {
                const response = await fetch('/api/company-profile');
                const data = await response.json();
                if (data && !data.error) {
                    companyProfile = data;
                    if (tenders && tenders.length > 0) {
                        updateBadges();
                        filterData();
                    }
                }
            } catch (err) {
                console.error("Failed to load company profile from API:", err);
            }
        }

        function openSettingsModal() {
            document.getElementById('settingsModal').style.display = 'flex';
            populateSettingsForm();
        }

        function closeSettingsModal() {
            document.getElementById('settingsModal').style.display = 'none';
            const feedback = document.getElementById('settingsFeedback');
            feedback.style.display = 'none';
        }

        function openProfileModal() {
            document.getElementById('profileModal').style.display = 'flex';
            populateProfileForm();
        }

        function closeProfileModal() {
            document.getElementById('profileModal').style.display = 'none';
            document.getElementById('profileFeedback').style.display = 'none';
        }

        function populateSettingsForm() {
            const config = scoringConfig || DEFAULT_SCORING_CONFIG;
            
            const w = config.weights || {};
            document.getElementById('weight_emd').value = w.emd !== undefined ? w.emd : 2.0;
            document.getElementById('weight_startup_exemption').value = w.startup_exemption !== undefined ? w.startup_exemption : 1.5;
            document.getElementById('weight_mse_exemption').value = w.mse_exemption !== undefined ? w.mse_exemption : 1.5;
            document.getElementById('weight_prebid').value = w.prebid !== undefined ? w.prebid : 0.5;
            document.getElementById('weight_date_window').value = w.date_window !== undefined ? w.date_window : 1.0;
            document.getElementById('weight_epbg').value = w.epbg !== undefined ? w.epbg : 0.5;
            
            document.getElementById('unknown_subscore').value = config.unknown_subscore !== undefined ? config.unknown_subscore : 0.5;
            
            const thr = config.status_thresholds || {};
            document.getElementById('shortlist_min').value = thr.shortlist_min !== undefined ? thr.shortlist_min : 70;
            document.getElementById('reject_max').value = thr.reject_max !== undefined ? thr.reject_max : 40;

            const fit = config.fit || {};
            const fw = fit.weights || {};
            document.getElementById('weight_fit_relevance').value = fw.relevance !== undefined ? fw.relevance : 3.0;
            document.getElementById('weight_fit_serviceability').value = fw.serviceability !== undefined ? fw.serviceability : 1.0;
            document.getElementById('weight_fit_value').value = fw.value_fit !== undefined ? fw.value_fit : 1.0;
            document.getElementById('weight_fit_buyer').value = fw.buyer_affinity !== undefined ? fw.buyer_affinity : 1.0;
            document.getElementById('weight_fit_eligibility').value = fw.eligibility_factor !== undefined ? fw.eligibility_factor : 2.0;
            document.getElementById('fit_min_threshold').value = fit.fit_min !== undefined ? fit.fit_min : 60;
        }

        function populateProfileForm() {
            const config = companyProfile || DEFAULT_COMPANY_PROFILE;
            
            const elig = config.eligibility || {};
            document.getElementById('profile_turnover').value = elig.annual_turnover_inr !== undefined ? elig.annual_turnover_inr : 1800000;
            document.getElementById('profile_experience').value = elig.years_experience !== undefined ? elig.years_experience : 6;
            
            const reg = elig.registrations || {};
            document.getElementById('profile_mse_udyam').checked = !!reg.mse_udyam;
            document.getElementById('profile_startup_dpiit').checked = !!reg.startup_dpiit;
            
            document.getElementById('profile_certifications').value = (elig.certifications || []).join(', ');
            document.getElementById('profile_mii').checked = !!elig.can_meet_make_in_india;
            document.getElementById('profile_turnover_exempt').checked = !!elig.turnover_waivable_by_exemption;

            const svc = config.serviceability || {};
            document.getElementById('profile_soft_avoid_states').value = (svc.soft_avoid_states || []).join(', ');
            document.getElementById('profile_soft_avoid_penalty').value = svc.soft_avoid_penalty !== undefined ? svc.soft_avoid_penalty : 0.5;
            document.getElementById('profile_soft_avoid_reason').value = svc.soft_avoid_reason || '';

            const vp = config.value_preference || {};
            document.getElementById('profile_sweet_min').value = vp.sweet_min_inr !== undefined ? vp.sweet_min_inr : 500000;
            document.getElementById('profile_sweet_max').value = vp.sweet_max_inr !== undefined ? vp.sweet_max_inr : 30000000;

            const lines = config.business_lines || [];
            const blDrone = lines.find(l => l.id === 'drone') || {};
            const blPower = lines.find(l => l.id === 'power_supply') || {};
            const blAi = lines.find(l => l.id === 'ai_it') || {};
            
            document.getElementById('profile_kws_drone').value = (blDrone.keywords || []).join(', ');
            document.getElementById('profile_kws_power_supply').value = (blPower.keywords || []).join(', ');
            document.getElementById('profile_kws_ai_it').value = (blAi.keywords || []).join(', ');

            let affinityText = '';
            for (let [k, v] of Object.entries(config.buyer_affinity || {})) {
                affinityText += `${k}: ${v}\n`;
            }
            document.getElementById('profile_buyer_affinity').value = affinityText;
        }

        function showFeedback(message, isSuccess) {
            const feedback = document.getElementById('settingsFeedback');
            feedback.innerText = message;
            feedback.style.display = 'block';
            if (isSuccess) {
                feedback.style.backgroundColor = 'var(--success-bg)';
                feedback.style.color = 'var(--success-color)';
                feedback.style.border = '1px solid rgba(16, 185, 129, 0.2)';
            } else {
                feedback.style.backgroundColor = 'var(--failed-bg)';
                feedback.style.color = 'var(--failed-color)';
                feedback.style.border = '1px solid rgba(239, 68, 68, 0.2)';
            }
        }

        function showProfileFeedback(message, isSuccess) {
            const feedback = document.getElementById('profileFeedback');
            feedback.innerText = message;
            feedback.style.display = 'block';
            if (isSuccess) {
                feedback.style.backgroundColor = 'var(--success-bg)';
                feedback.style.color = 'var(--success-color)';
                feedback.style.border = '1px solid rgba(16, 185, 129, 0.2)';
            } else {
                feedback.style.backgroundColor = 'var(--failed-bg)';
                feedback.style.color = 'var(--failed-color)';
                feedback.style.border = '1px solid rgba(239, 68, 68, 0.2)';
            }
        }

        async function saveScoringConfigForm(event) {
            if (event) event.preventDefault();
            
            const feedback = document.getElementById('settingsFeedback');
            feedback.style.display = 'none';
            
            const emd = parseFloat(document.getElementById('weight_emd').value);
            const startup = parseFloat(document.getElementById('weight_startup_exemption').value);
            const mse = parseFloat(document.getElementById('weight_mse_exemption').value);
            const prebid = parseFloat(document.getElementById('weight_prebid').value);
            const dateWindow = parseFloat(document.getElementById('weight_date_window').value);
            const epbg = parseFloat(document.getElementById('weight_epbg').value);
            
            const unknown = parseFloat(document.getElementById('unknown_subscore').value);
            const shortlistMin = parseFloat(document.getElementById('shortlist_min').value);
            const rejectMax = parseFloat(document.getElementById('reject_max').value);
            
            const fitRelevance = parseFloat(document.getElementById('weight_fit_relevance').value);
            const fitServiceability = parseFloat(document.getElementById('weight_fit_serviceability').value);
            const fitValue = parseFloat(document.getElementById('weight_fit_value').value);
            const fitBuyer = parseFloat(document.getElementById('weight_fit_buyer').value);
            const fitEligibility = parseFloat(document.getElementById('weight_fit_eligibility').value);
            const fitMin = parseFloat(document.getElementById('fit_min_threshold').value);

            if (isNaN(emd) || emd < 0 ||
                isNaN(startup) || startup < 0 ||
                isNaN(mse) || mse < 0 ||
                isNaN(prebid) || prebid < 0 ||
                isNaN(dateWindow) || dateWindow < 0 ||
                isNaN(epbg) || epbg < 0) {
                showFeedback("All weights must be numeric and >= 0.", false);
                return;
            }
            
            if (emd + startup + mse + prebid + dateWindow + epbg <= 0) {
                showFeedback("At least one weight must be > 0.", false);
                return;
            }
            
            if (isNaN(unknown) || unknown < 0 || unknown > 1) {
                showFeedback("unknown_subscore must be in [0, 1].", false);
                return;
            }
            
            if (isNaN(shortlistMin) || isNaN(rejectMax) || rejectMax < 0 || shortlistMin > 100 || rejectMax >= shortlistMin) {
                showFeedback("Require 0 <= Reject Max < Shortlist Min <= 100.", false);
                return;
            }
            
            if (isNaN(fitRelevance) || fitRelevance < 0 ||
                isNaN(fitServiceability) || fitServiceability < 0 ||
                isNaN(fitValue) || fitValue < 0 ||
                isNaN(fitBuyer) || fitBuyer < 0 ||
                isNaN(fitEligibility) || fitEligibility < 0) {
                showFeedback("All fit weights must be numeric and >= 0.", false);
                return;
            }
            if (fitRelevance + fitServiceability + fitValue + fitBuyer + fitEligibility <= 0) {
                showFeedback("At least one fit weight must be > 0.", false);
                return;
            }
            if (isNaN(fitMin) || fitMin < 0 || fitMin > 100) {
                showFeedback("Fit Minimum threshold must be between 0 and 100.", false);
                return;
            }

            const baseConfig = scoringConfig || DEFAULT_SCORING_CONFIG;
            const payload = {
                version: baseConfig.version || 1,
                weights: {
                    emd: emd,
                    startup_exemption: startup,
                    mse_exemption: mse,
                    prebid: prebid,
                    date_window: dateWindow,
                    epbg: epbg
                },
                emd: baseConfig.emd || DEFAULT_SCORING_CONFIG.emd,
                date_window: baseConfig.date_window || DEFAULT_SCORING_CONFIG.date_window,
                epbg: baseConfig.epbg || DEFAULT_SCORING_CONFIG.epbg,
                unknown_subscore: unknown,
                status_thresholds: {
                    shortlist_min: shortlistMin,
                    reject_max: rejectMax
                },
                fit: {
                    weights: {
                        relevance: fitRelevance,
                        serviceability: fitServiceability,
                        value_fit: fitValue,
                        buyer_affinity: fitBuyer,
                        eligibility_factor: fitEligibility
                    },
                    fit_min: fitMin,
                    unknown_buyer_subscore: baseConfig.fit ? (baseConfig.fit.unknown_buyer_subscore || 0.4) : 0.4,
                    turnover_gap_subscore: baseConfig.fit ? (baseConfig.fit.turnover_gap_subscore || 0.3) : 0.3,
                    weak_relevance_subscore: baseConfig.fit ? (baseConfig.fit.weak_relevance_subscore || 0.5) : 0.5
                }
            };
            
            try {
                const response = await fetch('/api/scoring-config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                
                const data = await response.json();
                if (response.ok) {
                    showFeedback("Configuration saved successfully. Applies on next scrape.", true);
                    scoringConfig = payload;
                    if (tenders && tenders.length > 0) {
                        updateBadges();
                        filterData();
                    }
                    setTimeout(closeSettingsModal, 1500);
                } else {
                    showFeedback(data.error || "Failed to save configuration.", false);
                }
            } catch (err) {
                console.error("Failed to save scoring config:", err);
                showFeedback("Server communication failed.", false);
            }
        }

        async function saveCompanyProfileForm(event) {
            if (event) event.preventDefault();
            
            const feedback = document.getElementById('profileFeedback');
            feedback.style.display = 'none';

            const turnover = parseFloat(document.getElementById('profile_turnover').value);
            const experience = parseInt(document.getElementById('profile_experience').value);
            const mse = document.getElementById('profile_mse_udyam').checked;
            const startup = document.getElementById('profile_startup_dpiit').checked;
            const certifications = document.getElementById('profile_certifications').value.split(',').map(s => s.trim()).filter(Boolean);
            const mii = document.getElementById('profile_mii').checked;
            const turnoverExempt = document.getElementById('profile_turnover_exempt').checked;

            const softAvoidStates = document.getElementById('profile_soft_avoid_states').value.split(',').map(s => s.trim()).filter(Boolean);
            const softAvoidPenalty = parseFloat(document.getElementById('profile_soft_avoid_penalty').value);
            const softAvoidReason = document.getElementById('profile_soft_avoid_reason').value.trim();

            const sweetMin = parseFloat(document.getElementById('profile_sweet_min').value);
            const sweetMax = parseFloat(document.getElementById('profile_sweet_max').value);

            const kwsDrone = document.getElementById('profile_kws_drone').value.split(',').map(s => s.trim()).filter(Boolean);
            const kwsPower = document.getElementById('profile_kws_power_supply').value.split(',').map(s => s.trim()).filter(Boolean);
            const kwsAi = document.getElementById('profile_kws_ai_it').value.split(',').map(s => s.trim()).filter(Boolean);

            if (isNaN(turnover) || turnover < 0) {
                showProfileFeedback("Annual turnover must be a non-negative number.", false);
                return;
            }
            if (isNaN(experience) || experience < 0) {
                showProfileFeedback("Years of experience must be a non-negative integer.", false);
                return;
            }
            if (isNaN(softAvoidPenalty) || softAvoidPenalty < 0 || softAvoidPenalty > 1) {
                showProfileFeedback("Soft avoid penalty must be between 0.0 and 1.0.", false);
                return;
            }
            if (isNaN(sweetMin) || sweetMin < 0 || isNaN(sweetMax) || sweetMax < 0 || sweetMin > sweetMax) {
                showProfileFeedback("Value band sweet spot must satisfy: 0 <= sweet min <= sweet max.", false);
                return;
            }
            if (kwsDrone.length === 0 || kwsPower.length === 0 || kwsAi.length === 0) {
                showProfileFeedback("Keywords for each business line must be non-empty.", false);
                return;
            }

            const buyerAffinity = {};
            const lines = document.getElementById('profile_buyer_affinity').value.split('\n');
            for (let line of lines) {
                if (!line.trim()) continue;
                const parts = line.split(':');
                if (parts.length < 2) {
                    showProfileFeedback(`Invalid buyer affinity format: '${line}'. Must be 'BUYER: VALUE'.`, false);
                    return;
                }
                const k = parts[0].trim();
                const v = parseFloat(parts.slice(1).join(':').trim());
                if (!k) {
                    showProfileFeedback(`Buyer name cannot be empty.`, false);
                    return;
                }
                if (isNaN(v) || v < 0 || v > 1) {
                    showProfileFeedback(`Buyer affinity value for '${k}' must be between 0.0 and 1.0.`, false);
                    return;
                }
                buyerAffinity[k] = v;
            }

            // This form only edits three of the business lines. Everything else on
            // a line (strong_keywords, exclude_keywords, priority) and every line
            // the form does not render (components, smart metering, batteries,
            // solar, biometrics) must survive the save untouched — rebuilding the
            // array from the three form fields silently deleted them.
            const mergeBusinessLines = (edits) => {
                const existing = (companyProfile && companyProfile.business_lines) || [];
                const edited = new Map(edits.map(e => [e.id, e]));
                const merged = existing.map(line => {
                    const patch = edited.get(line && line.id);
                    if (!patch) return line;               // untouched line, kept verbatim
                    edited.delete(line.id);
                    return { ...line, ...patch };          // keep strong/exclude keywords
                });
                // A line the form knows about but the stored profile lacks (first run).
                edited.forEach(patch => merged.push(patch));
                return merged;
            };

            const payload = {
                version: companyProfile ? (companyProfile.version || 1) : 1,
                company: companyProfile ? companyProfile.company : {
                    legal_name: "Earnest Tactical Solutions Pvt. Ltd.",
                    short_name: "ETSPL",
                    incorporation_ym: "2020-03",
                    hq_state: "Haryana",
                    hq_city: "Gurgaon"
                },
                eligibility: {
                    annual_turnover_inr: turnover,
                    years_experience: experience,
                    registrations: {
                        mse_udyam: mse,
                        startup_dpiit: startup
                    },
                    certifications: certifications,
                    can_meet_make_in_india: mii,
                    max_order_value_inr: companyProfile ? companyProfile.eligibility.max_order_value_inr : null,
                    turnover_waivable_by_exemption: turnoverExempt
                },
                serviceability: {
                    all_india: companyProfile ? companyProfile.serviceability.all_india : true,
                    soft_avoid_states: softAvoidStates,
                    soft_avoid_penalty: softAvoidPenalty,
                    soft_avoid_reason: softAvoidReason
                },
                business_lines: mergeBusinessLines([
                    { id: "drone", label: "Drone / UAV", priority: 1.0, keywords: kwsDrone },
                    { id: "power_supply", label: "Power Supply / Electrical", priority: 1.0, keywords: kwsPower },
                    { id: "ai_it", label: "AI / IT / Electronics", priority: 1.0, keywords: kwsAi }
                ]),
                buyer_affinity: buyerAffinity,
                value_preference: {
                    sweet_min_inr: sweetMin,
                    sweet_max_inr: sweetMax
                },
                avoid_rules: companyProfile ? companyProfile.avoid_rules : {
                    gem_q2_category: true,
                    prefer_custom_bids: true
                }
            };

            try {
                const response = await fetch('/api/company-profile', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                if (response.ok) {
                    showProfileFeedback("Company profile updated successfully. Applies on next scrape.", true);
                    companyProfile = payload;
                    if (tenders && tenders.length > 0) {
                        updateBadges();
                        filterData();
                    }
                    setTimeout(closeProfileModal, 1500);
                } else {
                    showProfileFeedback(data.error || "Failed to update profile.", false);
                }
            } catch (err) {
                console.error("Failed to save company profile:", err);
                showProfileFeedback("Server communication failed.", false);
            }
        }

        async function resetConfigToDefaults() {
            if (!confirm("Are you sure you want to reset all weights and thresholds to default values?")) {
                return;
            }
            
            const feedback = document.getElementById('settingsFeedback');
            feedback.style.display = 'none';
            
            try {
                const response = await fetch('/api/scoring-config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(DEFAULT_SCORING_CONFIG)
                });
                
                const data = await response.json();
                if (response.ok) {
                    showFeedback("Configuration reset to defaults successfully.", true);
                    scoringConfig = DEFAULT_SCORING_CONFIG;
                    populateSettingsForm();
                    if (tenders && tenders.length > 0) {
                        updateBadges();
                        filterData();
                    }
                    setTimeout(closeSettingsModal, 1500);
                } else {
                    showFeedback(data.error || "Failed to reset configuration.", false);
                }
            } catch (err) {
                console.error("Failed to reset scoring config:", err);
                showFeedback("Server communication failed.", false);
            }
        }

        async function reanalyzeBid(encodedBidId) {
            const bidId = decodeURIComponent(encodedBidId);
            try {
                const response = await fetch('/api/scrape/id', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ bid_id: bidId })
                });
                const resData = await response.json();
                if (resData.error) {
                    alert(resData.error);
                } else {
                    openScraperModal();
                    document.getElementById('consoleLogs').innerText = `Re-analyzing Bid: ${bidId}...`;
                }
            } catch (err) {
                console.error("Failed to re-analyze bid:", err);
                alert("Failed to trigger re-analysis.");
            }
        }

        // Light is the working surface; dark is opt-in and remembered.
        const SUN_ICON = `<path stroke-linecap="round" stroke-linejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364-6.364l-.707.707M6.343 17.657l-.707.707m12.728 0l-.707-.707M6.343 6.343l-.707-.707M14 12a2 2 0 11-4 0 2 2 0 014 0z" />`;
        const MOON_ICON = `<path stroke-linecap="round" stroke-linejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />`;

        if (localStorage.getItem('theme') === 'dark') {
            document.body.classList.add('dark-mode');
        }

        function toggleTheme() {
            const isDark = document.body.classList.toggle('dark-mode');
            localStorage.setItem('theme', isDark ? 'dark' : 'light');
            updateThemeButton(isDark);
        }

        // The button advertises what a click will do, not what is on screen.
        function updateThemeButton(isDark) {
            const themeText = document.getElementById('themeText');
            const themeIcon = document.getElementById('themeIcon');
            if (!themeText || !themeIcon) return;
            themeText.innerText = isDark ? 'Light Mode' : 'Dark Mode';
            themeIcon.innerHTML = isDark ? SUN_ICON : MOON_ICON;
        }

        // Fetch data from local Flask API on load
        window.addEventListener('DOMContentLoaded', () => {
            updateThemeButton(document.body.classList.contains('dark-mode'));
            initSearchInput();
            runSplash();
            checkAuthRequirement();
            loadScoringConfig();
            loadCompanyProfile();
            refreshData();
            loadSourceCatalog();
            loadKeywordsForModal();
            loadPresets();
            fetchLiveExcelStatus();
            startLiveExcelTicker();
            loadFinalizedData();
        });

        // Every keystroke used to re-filter and re-render the whole list.
        function initSearchInput() {
            const input = document.getElementById('searchInput');
            if (!input) return;
            let debounce = null;
            input.addEventListener('input', () => {
                clearTimeout(debounce);
                debounce = setTimeout(filterData, 180);
            });
        }

        // ------------------------------------------------------------------
        // Lazy analysis detail.
        // /api/tenders omits the heavy analysis members (breakdown,
        // fit_breakdown, reasons, field_status) -- they are ~3.4 MB across the
        // corpus and only ever appear inside the score accordion. We fetch the
        // full record for one bid the first time its accordion is opened.
        // ------------------------------------------------------------------
        async function fetchTenderDetail(bidNo) {
            try {
                const response = await fetch(`/api/tenders/${encodeURIComponent(bidNo)}`);
                if (!response.ok) return null;
                const data = await response.json();
                const full = data.tender;
                if (!full) return null;
                const idx = tenders.findIndex(t => t.bid_no === bidNo);
                if (idx !== -1) tenders[idx] = full;
                return full;
            } catch (err) {
                console.error(`Failed to load analysis detail for ${bidNo}:`, err);
                return null;
            }
        }

        async function hydrateAnalysis(detailsEl, encodedBid) {
            if (!detailsEl.open || detailsEl.dataset.hydrated) return;
            detailsEl.dataset.hydrated = 'pending';
            const full = await fetchTenderDetail(decodeURIComponent(encodedBid));
            if (!full) {
                delete detailsEl.dataset.hydrated;
                return;
            }
            // Rebuild the card off the now-complete record and lift out the
            // freshly rendered accordion body.
            const rebuilt = new DOMParser()
                .parseFromString(buildTenderCard(full), 'text/html')
                .querySelector('details.tender-analysis-details');
            if (rebuilt) detailsEl.innerHTML = rebuilt.innerHTML;
            detailsEl.dataset.hydrated = 'done';
        }

        async function refreshData() {
            try {
                const response = await fetch('/api/tenders');
                const data = await response.json();
                tenders = data.tenders || [];
                clearDeadlineCache();
                initDashboard();
            } catch (err) {
                console.error("Failed to load tenders from API:", err);
                showSetupState();
            }
        }

        async function loadKeywordsForModal() {
            try {
                const response = await fetch('/api/keywords');
                const data = await response.json();
                knownKeywords = data.keywords || [];
                
                const grid = document.getElementById('modalKeywordsGrid');
                grid.innerHTML = knownKeywords.map(kw => `
                    <label class="keyword-item" data-kw="${kw.toLowerCase()}">
                        <input type="checkbox" name="keywordCheckbox" value="${kw}">
                        <span>${kw}</span>
                    </label>
                `).join('') + '<div class="kw-empty" id="kwEmpty" style="display:none;">No keyword matches that search.</div>';

                // One delegated listener beats 300 inline handlers.
                grid.addEventListener('change', updateKeywordCount);

                const search = document.getElementById('keywordSearch');
                if (search) {
                    search.placeholder = `Search ${knownKeywords.length} keywords… (e.g. lead acid, vrla, tubular)`;
                }
                updatePresetCoverage();
                applyKeywordSearch();
            } catch (err) {
                console.error("Failed to load keywords for modal:", err);
            }
        }

        // --- Keyword picker: search, counter, and visible-scoped bulk actions ---
        // The grid holds 300+ terms. Without a filter, finding "vrla battery"
        // meant scrolling the entire list; and "Select All" over 300 keywords
        // is never what you want mid-search.

        function visibleKeywordBoxes() {
            return Array.from(document.getElementsByName('keywordCheckbox'))
                .filter(b => !b.closest('.keyword-item').classList.contains('kw-hidden'));
        }

        function applyKeywordSearch() {
            const searchEl = document.getElementById('keywordSearch');
            const selectedOnlyEl = document.getElementById('keywordShowSelected');
            const q = (searchEl ? searchEl.value : '').trim().toLowerCase();
            const selectedOnly = selectedOnlyEl ? selectedOnlyEl.checked : false;

            // Space-separated words all have to appear, in any order, so
            // "acid lead" and "lead acid" both find LEAD ACID BATTERY.
            const terms = q ? q.split(/\s+/) : [];

            let shown = 0;
            document.querySelectorAll('#modalKeywordsGrid .keyword-item').forEach(item => {
                const hay = item.dataset.kw || '';
                const box = item.querySelector('input');
                const matchesQuery = terms.every(t => hay.includes(t));
                const matchesSelected = !selectedOnly || (box && box.checked);
                const visible = matchesQuery && matchesSelected;
                item.classList.toggle('kw-hidden', !visible);
                if (visible) shown++;
            });

            const empty = document.getElementById('kwEmpty');
            if (empty) empty.style.display = shown === 0 ? 'block' : 'none';
            updateKeywordCount();
        }

        function updateKeywordCount() {
            const el = document.getElementById('keywordCount');
            if (!el) return;
            const all = Array.from(document.getElementsByName('keywordCheckbox'));
            const selected = all.filter(b => b.checked).length;
            const visible = visibleKeywordBoxes().length;
            const scope = visible === all.length
                ? `${all.length} keywords`
                : `${visible} shown of ${all.length}`;
            el.innerHTML = `<strong>${selected}</strong> selected · ${scope}`;
        }

        let loadedPresets = {};

        async function loadPresets() {
            try {
                const response = await fetch('/api/presets');
                const data = await response.json();
                loadedPresets = data.value_presets || {};
                const sel = document.getElementById('presetSelect');
                if (!sel) return;
                sel.innerHTML = Object.entries(loadedPresets).map(([id, p]) =>
                    `<option value="${id}">${p.label || id}</option>`
                ).join('');
                if (data.active_preset && loadedPresets[data.active_preset]) {
                    sel.value = data.active_preset;
                }
                updatePresetBandLabel();
            } catch (err) {
                console.error("Failed to load presets:", err);
            }
        }

        function updatePresetBandLabel() {
            const sel = document.getElementById('presetSelect');
            const lbl = document.getElementById('presetBandLabel');
            if (!sel || !lbl) return;
            const p = loadedPresets[sel.value];
            if (p && p.sweet_min_inr != null && p.sweet_max_inr != null) {
                const fmt = v => v >= 10000000 ? `₹${(v/10000000).toFixed(2)}Cr`
                    : v >= 100000 ? `₹${(v/100000).toFixed(1)}L` : `₹${v.toLocaleString('en-IN')}`;
                lbl.innerText = `band ${fmt(p.sweet_min_inr)} – ${fmt(p.sweet_max_inr)}`;
            } else { lbl.innerText = ''; }
        }

        async function onPresetChange() {
            const sel = document.getElementById('presetSelect');
            const presetId = sel.value;
            updatePresetBandLabel();
            try {
                const response = await fetch('/api/preset', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: presetId })
                });
                const data = await response.json();
                if (data.error) { alert('Preset switch failed: ' + data.error); return; }
                // Auto-select this preset's suggested keywords in the grid (case-insensitive).
                const wanted = (data.keywords || []).map(k => k.toLowerCase());
                if (wanted.length) {
                    const boxes = document.getElementsByName('keywordCheckbox');
                    boxes.forEach(b => { b.checked = wanted.includes(b.value.toLowerCase()); });
                    if (typeof applyKeywordSearch === 'function') { applyKeywordSearch(); }
                }
                // Each preset has its own isolated inventory — reload the list.
                if (typeof refreshData === 'function') { refreshData(); }
            } catch (err) {
                console.error("Failed to switch preset:", err);
            }
        }

        // Quick-preset taxonomy for the keyword picker.
        //
        // Every keyword in config/keywords.csv must be reachable from at least
        // one chip; tests/test_keyword_presets.py fails if one is not, and the
        // "❓ Uncategorised" chip is the runtime safety net for keywords added
        // to the CSV after that check last ran.
        const TECHNICAL_CATEGORIES = {
            solar_broad: [
                "solar", "solar panels", "panels", "panel", "solar power plant", "solar epc",
                "solar project", "solar pv", "solar photovoltaic", "renewable energy",
                "green energy", "rooftop solar", "on grid solar", "off grid solar",
                "hybrid solar", "pm surya ghar", "residential rooftop solar",
                "government building solar", "sitc solar", "epc solar", "turnkey solar",
                "solar installation", "solar commissioning", "solar o&m", "annual maintenance solar",
                "solar panel", "solar module", "mono perc", "topcon module", "bifacial module",
                "solar inverter", "string inverter", "central inverter", "hybrid inverter",
                "battery energy storage system", "bess", "lithium battery", "solar battery",
                "solar cable", "solar mounting structure", "module mounting structure",
                "solar junction box", "ground mounted solar", "solar street light",
                "solar high mast", "solar led street light", "solar pump",
                "solar water pump", "pm kusum", "kusum component b", "kusum component c",
                "design supply installation testing commissioning"
            ],
            power_electrical: [
                "power", "power supply", "psu", "lvpsu", "hvpsu", "transformer",
                "substation", "switchgear", "gis substation", "discom", "power distribution",
                "electrical testing", "electrical commissioning", "solid state power amplifier",
                "amplifier", "resistors", "cables", "cable", "rectifiers", "harness",
                "relay", "connectors", "repairing (electronics)", "supply",
                "ht equipment", "lt equipment", "ht/lt equipment",
                // Bare "inverter" sits outside the solar-prefixed variants above.
                "inverter", "current transformer"
            ],
            smart_metering: [
                "meter", "metering", "smart meter", "smart metering", "smart energy meter",
                "electricity smart meter", "energy meter", "electronic energy meter",
                "digital energy meter", "multifunction meter", "net meter", "net metering",
                "prepaid meter", "smart prepaid meter", "ami",
                "advanced metering infrastructure", "amisp", "ami service provider",
                "meter data management", "mdm", "hes", "head end system",
                "rf mesh", "rf communication", "nb-iot meter", "lorawan meter",
                "gprs meter", "cellular smart meter", "iot energy meter",
                "remote meter reading", "automatic meter reading", "amr",
                "distribution metering", "feeder metering", "dt metering",
                "consumer metering", "lt meter", "ht meter", "three phase meter",
                "single phase meter", "mdas", "scada integration", "energy accounting",
                "smart grid", "rdss smart meter", "dbfoot smart meter",
                "meter installation", "meter replacement", "smart meter o&m",
                "ct operated meter", "meter box", "meter enclosure",
                "meter communication module", "dcu", "data concentrator unit",
                "gateway", "meter testing equipment"
            ],
            ai_software: [
                "ai", "artificial intelligence", "machine learning", "deep learning", "computer vision",
                "object detection", "video analytics", "generative ai", "large language model",
                "llm", "chatbot", "natural language processing", "nlp", "neural network",
                "predictive analytics", "data analytics", "data science", "big data",
                "image recognition", "speech recognition", "software", "development",
                "cloud computing", "cloud migration", "cybersecurity", "firewall",
                "endpoint security", "penetration testing", "vulnerability assessment",
                "erp", "crm", "enterprise software", "database", "data warehouse",
                "business intelligence", "iot", "internet of things", "smart city",
                "edge computing", "embedded system", "embedded software", "scada",
                "automation", "robotic process automation", "rpa", "blockchain",
                "digital twin", "augmented reality",
                "virtual reality", "it infrastructure", "system integration",
                "data center", "server", "digital transformation", "e-governance",
                "website development", "web portal", "custom software",
                "application development", "api integration", "mobile application"
            ],
            security_surveillance: [
                "security", "cybersecurity", "firewall", "endpoint security",
                "penetration testing", "vulnerability assessment", "surveillance",
                "cctv", "video analytics", "facial recognition", "face recognition",
                "biometric", "biometrics", "fingerprint", "access control"
            ],
            defence_avionics: [
                "defence", "military", "military grade", "radar", "gis",
                "mapping", "cctv", "data aquisition", "indigenous",
                "indigenous & development", "design",
                "mechanical", "repair", "repairing", "avionics",
                "military engineering services"
            ],
            drones_uav: [
                "drone", "drones", "uav", "uavs", "unmanned aerial vehicle",
                "unmanned aerial vehicles (uavs)", "unmanned aircraft",
                "remotely piloted aircraft", "multirotor", "quadcopter",
                "fixed wing drone", "drone survey", "uav survey", "aerial survey",
                "airborne lidar", "lidar survey", "orthomosaic", "photogrammetry"
            ],
            dgps_gis_survey: [
                "dgps", "dgps survey", "differential gps", "gis", "gis survey", "gis mapping",
                "geographical information system", "geospatial", "geospatial survey",
                "geospatial mapping", "topographic survey", "topographical survey",
                "cadastral survey", "drone survey", "uav survey", "aerial survey",
                "lidar survey", "airborne lidar", "bathymetric survey", "hydrographic survey",
                "total station survey", "total station", "contour survey", "land survey",
                "boundary survey", "georeferencing", "orthomosaic", "photogrammetry",
                "digital elevation model", "dem", "dtm", "dsm", "ground control points",
                "gcp", "rtk survey", "gnss survey", "subsurface utility engineering",
                "sue survey", "utility mapping", "thematic mapping", "remote sensing",
                "satellite imagery", "as-built survey", "geotechnical survey", "geophysical survey"
            ],
            battery_storage: [
                "battery", "bess", "battery energy storage system", "energy storage",
                "energy storage system", "lithium battery", "lithium ion battery",
                "lithium iron phosphate", "lifepo4 battery", "lfp battery",
                "solar battery", "battery pack", "battery bank", "battery set",
                "battery cell", "battery charger", "battery charging system",
                "battery management system", "battery container", "battery rack",
                "battery stand", "battery cable", "battery terminal",
                "battery tester", "battery load tester", "battery replacement",
                "battery maintenance", "battery amc", "battery disposal",
                "battery buyback", "scrap battery"
            ],
            lead_acid: [
                "lead acid battery", "lead-acid battery", "sealed lead acid battery",
                "smf battery", "sealed maintenance free battery", "vrla battery",
                "valve regulated lead acid battery", "tubular battery",
                "tubular lead acid battery", "flooded lead acid battery",
                "flat plate battery", "plante battery", "stationary battery",
                "stationary lead acid battery", "traction battery",
                "motive power battery", "inverter battery", "ups battery",
                "solar tubular battery", "deep cycle battery", "gel battery",
                "agm battery", "absorbent glass mat battery",
                "nickel cadmium battery", "ni-cd battery", "telecom battery",
                "dg set battery", "automotive battery", "sli battery",
                "lead acid cell", "2v lead acid cell", "2v battery cell", "12v battery",
                "12v 100ah battery", "100ah battery", "150ah battery",
                "200ah battery", "c10 rating battery", "battery electrolyte",
                "lead scrap"
            ],
            ev_infrastructure: [
                "ev", "ev charging", "electric vehicle charger", "charging station"
            ],
            lighting_mast: [
                "light", "lights", "lighting", "solar street light", "solar high mast",
                "solar led street light", "cctv"
            ],
            pumps_kusum: [
                "pump", "pumps", "solar pump", "solar water pump", "pm kusum",
                "kusum component b", "kusum component c"
            ]
        };

        // Buyer and scheme names rather than technologies. Kept out of
        // TECHNICAL_CATEGORIES so "🚀 All Technical" keeps meaning "everything we
        // can build", not "every keyword in the file".
        const BUYER_CATEGORIES = {
            buyers_agencies: [
                "discom", "state electricity board", "electricity distribution company",
                "rec pdcl", "energy department", "power department",
                "smart grid mission", "seci", "ntpc", "nhpc", "sjvn", "nlc india",
                "mnre", "cpwd", "railways", "airports authority of india",
                "military engineering services",
                "state renewable energy development agencies", "municipal corporation"
            ]
        };

        // What a preset chip resolves against. Presets may overlap on purpose --
        // "drone survey" belongs to both 🚁 Drones and 🛰️ Survey.
        const KEYWORD_CATEGORIES = { ...TECHNICAL_CATEGORIES, ...BUYER_CATEGORIES };

        // Mirrors gemsentry/textmatch.py:keyword_hit. Raw substring matching made
        // a preset tick half the list: "ai" sits inside "maintenance", "ev" inside
        // "development", so 🪫 Lead-Acid also selected SOLAR, POWER, AI and CABLE.
        // Implemented with indexOf rather than lookbehind so no term needs escaping.
        function isKwWordChar(ch) {
            return /[a-z0-9_]/i.test(ch);
        }

        function termMatchesKeyword(term, keyword) {
            const hay = String(keyword || '').toLowerCase().trim();
            const needle = String(term || '').toLowerCase().trim();
            if (!hay || !needle) return false;
            if (hay === needle) return true;

            // Tolerate a trailing plural s ("connector" hits "connectors"), but not
            // on a term that already ends in s — that would let "ups" match "up".
            const allowPlural = isKwWordChar(needle[needle.length - 1]) && !needle.endsWith('s');

            let from = 0;
            for (;;) {
                const i = hay.indexOf(needle, from);
                if (i === -1) return false;
                from = i + 1;
                const before = i > 0 ? hay[i - 1] : '';
                if (before && isKwWordChar(before) && isKwWordChar(needle[0])) continue;
                let end = i + needle.length;
                if (allowPlural && hay[end] === 's') end++;
                const after = end < hay.length ? hay[end] : '';
                if (after && isKwWordChar(after)) continue;
                return true;
            }
        }

        function selectCategoryKeywords(categoryKey) {
            // A preset ticks boxes across the whole list, so drop any active
            // search — otherwise the ticks land on rows the user cannot see.
            const search = document.getElementById('keywordSearch');
            if (search) search.value = '';
            const selectedOnly = document.getElementById('keywordShowSelected');
            if (selectedOnly) selectedOnly.checked = false;

            const boxes = Array.from(document.getElementsByName('keywordCheckbox'));

            // "All Technical" replaces the selection; the other chips add to it,
            // so several presets can be stacked into one search.
            if (categoryKey === 'all_technical') {
                const allTech = Object.values(TECHNICAL_CATEGORIES).flat();
                boxes.forEach(b => {
                    b.checked = allTech.some(t => termMatchesKeyword(t, b.value.toLowerCase().trim()));
                });
                applyKeywordSearch();
                return;
            }

            // The safety net: whatever the curated chips do not reach. Empty in
            // a healthy build, non-empty the moment keywords.csv grows a term
            // nobody has filed yet — so no keyword is ever unreachable.
            if (categoryKey === 'uncategorised') {
                uncategorisedKeywordBoxes().forEach(b => { b.checked = true; });
                applyKeywordSearch();
                return;
            }

            const targetTerms = KEYWORD_CATEGORIES[categoryKey] || [];
            boxes.forEach(b => {
                const val = b.value.toLowerCase().trim();
                if (targetTerms.some(t => termMatchesKeyword(t, val))) {
                    b.checked = true;
                }
            });
            applyKeywordSearch();
        }

        function keywordIsCategorised(keyword) {
            const val = String(keyword || '').toLowerCase().trim();
            return Object.values(KEYWORD_CATEGORIES)
                .some(terms => terms.some(t => termMatchesKeyword(t, val)));
        }

        function uncategorisedKeywordBoxes() {
            return Array.from(document.getElementsByName('keywordCheckbox'))
                .filter(b => !keywordIsCategorised(b.value));
        }

        // Shows the ❓ chip only when it has something to offer, and puts the
        // count on it so an unfiled keyword is visible rather than merely
        // reachable.
        function updatePresetCoverage() {
            const chip = document.getElementById('uncategorisedChip');
            if (!chip) return;
            const orphans = uncategorisedKeywordBoxes();
            chip.style.display = orphans.length ? '' : 'none';
            chip.textContent = `❓ Uncategorised (${orphans.length})`;
            chip.title = orphans.length
                ? `Not covered by any preset: ${orphans.map(b => b.value).join(', ')}`
                : '';
        }

        // Scoped to what is on screen: with a search active, "Select All" means
        // "select these matches", not "select all 300 keywords".
        function selectAllKeywords() {
            visibleKeywordBoxes().forEach(b => b.checked = true);
            updateKeywordCount();
        }

        function clearAllKeywords() {
            visibleKeywordBoxes().forEach(b => b.checked = false);
            applyKeywordSearch();  // "Selected only" view must drop what was cleared
        }

        function openScraperModal() {
            document.getElementById('scraperModal').style.display = 'flex';
            checkScraperStatus();
            if (!pollingInterval) {
                pollingInterval = setInterval(checkScraperStatus, 1000);
            }
        }

        function closeScraperModal() {
            document.getElementById('scraperModal').style.display = 'none';
            if (pollingInterval) {
                clearInterval(pollingInterval);
                pollingInterval = null;
            }
        }

        // Label, colour and CSS class for an eligibility verdict. Only an
        // explicit 'eligible' is presented as a pass — 'unknown' means the
        // document did not tell us, which is a review, not a green light.
        function eligibilityPresentation(verdict) {
            if (verdict === 'eligible') {
                return {
                    label: 'Eligible',
                    className: 'text-success',
                    color: 'var(--success-color)',
                };
            }
            if (verdict === 'turnover_gap') {
                return {
                    label: 'Turnover Gap',
                    className: 'text-failed',
                    color: 'var(--failed-color)',
                };
            }
            return {
                label: 'Needs Review — Eligibility Unconfirmed',
                className: 'text-warning',
                color: 'var(--warning-color)',
            };
        }

        // Renders the terminating line for a finished job. The backend sets
        // `outcome` for every job it runs (scrape, single-bid, rescore); a
        // missing one means no job has finished in this server's lifetime.
        function jobOutcomeMessage(data) {
            const outcome = data && data.outcome;
            const warnings = (data && data.warnings) || [];
            const warningText = warnings.length
                ? '\n' + warnings.map(w => '[WARNING] ' + w).join('\n')
                : '';

            if (outcome === 'failed') {
                const detail = (data && data.error) ? ' ' + data.error : '';
                return '[FAILED] Job did not complete.' + detail
                    + ' Dashboard data may be unchanged.' + warningText;
            }
            if (outcome === 'partial') {
                return '[PARTIAL] Job completed, but some results are missing.'
                    + warningText;
            }
            if (outcome === 'succeeded') {
                if (data.new_count === 0) {
                    return '[SUCCESS] Job completed. No new tenders matched this search.'
                        + warningText;
                }
                return '[SUCCESS] Job completed execution. Dashboard data updated.'
                    + warningText;
            }
            return '[DONE] Job is no longer running.';
        }

        async function checkScraperStatus() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                
                const consoleLogs = document.getElementById('consoleLogs');
                const startBtn = document.getElementById('startScrapeBtn');
                
                // Update log session hint if present, else fallback
                if (data && data.hasOwnProperty('log_session_path') && data.log_session_path) {
                    activeSessionLogPath = data.log_session_path;
                    document.getElementById('logSessionHint').innerText = 'Session log: ' + activeSessionLogPath;
                } else {
                    activeSessionLogPath = null;
                    document.getElementById('logSessionHint').innerText = 'App log: logs/gemsentry.log';
                }

                if (data.status === 'running') {
                    startBtn.disabled = true;
                    startBtn.innerText = 'Scraping...';
                    startBtn.style.opacity = '0.6';
                    
                    if (data.logs && data.logs.length > 0) {
                        consoleLogs.innerText = data.logs.join('\n');
                        consoleLogs.scrollTop = consoleLogs.scrollHeight;
                    } else {
                        consoleLogs.innerText = 'Scraper starting up...';
                    }
                } else {
                    startBtn.disabled = false;
                    startBtn.innerText = 'Start Scraper';
                    startBtn.style.opacity = '1';
                    
                    if (data.logs && data.logs.length > 0) {
                        // The mere presence of log lines used to be read as
                        // success, so a crashed scrape still reported
                        // [SUCCESS]. Trust the backend's outcome instead.
                        consoleLogs.innerText = data.logs.join('\n') + '\n\n' + jobOutcomeMessage(data);
                        consoleLogs.scrollTop = consoleLogs.scrollHeight;
                        
                        // Stop polling and update main dashboard
                        clearInterval(pollingInterval);
                        pollingInterval = null;
                        refreshData();
                    }
                }

                // Check for transition from running -> idle
                const currentStatus = (data && data.status) || 'idle';
                if (previousStatus === 'running' && currentStatus === 'idle') {
                    refreshLogTail(true);
                    setTimeout(() => {
                        setTodayTriage('all');
                        fetchLiveExcelStatus();
                    }, 500);
                }
                previousStatus = currentStatus;
            } catch (err) {
                console.error("Error checking status:", err);
            }
        }

        async function refreshLogTail(isTransition = false) {
            try {
                const response = await fetch('/api/logs');
                if (!response.ok) throw new Error("Failed to fetch logs");
                const data = await response.json();
                
                // Guard missing keys
                const appLog = (data && data.app_log) || 'logs/gemsentry.log';
                const tail = (data && data.tail) || [];
                
                // Update log hint only if we don't have a specific active session path from /api/status
                if (!activeSessionLogPath) {
                    document.getElementById('logSessionHint').innerText = 'App log: ' + appLog;
                }
                
                const secondaryTailLogs = document.getElementById('secondaryTailLogs');
                const secondaryTailContainer = document.getElementById('secondaryTailContainer');
                
                if (secondaryTailLogs) {
                    secondaryTailLogs.innerText = tail.length > 0 ? tail.join('\n') : '(No log entries found)';
                    secondaryTailLogs.scrollTop = secondaryTailLogs.scrollHeight;
                }
                
                if (secondaryTailContainer) {
                    secondaryTailContainer.style.display = 'flex';
                }
            } catch (err) {
                console.error("Error refreshing log tail:", err);
            }
        }

        async function triggerScrape() {
            const checkboxes = document.getElementsByName('keywordCheckbox');
            const selected = [];
            checkboxes.forEach(b => {
                if (b.checked) selected.push(b.value);
            });
            
            if (selected.length === 0) {
                alert("Please select at least one keyword to scrape.");
                return;
            }
            
            const pageLimit = document.getElementById('modalPageLimit').value;
            const maxPages = pageLimit === 'auto' ? null : parseInt(pageLimit);
            const sortOrder = document.getElementById('modalSortOrder').value;
            const minDaysLeftInput = document.getElementById('modalMinDaysLeft');
            const minDaysLeftVal = minDaysLeftInput ? parseInt(minDaysLeftInput.value) : 5;
            const enableTarget = document.getElementById('enableTargetGoal') ? document.getElementById('enableTargetGoal').checked : false;
            const targetCount = enableTarget ? (parseInt(document.getElementById('targetCountInput').value) || 20) : null;
            
            const payload = {
                keywords: selected,
                max_pages: maxPages,
                sort_order: sortOrder,
                min_days_left: isNaN(minDaysLeftVal) ? 5 : minDaysLeftVal
            };
            if (enableTarget && targetCount) {
                payload.target_count = targetCount;
                payload.min_days_left = 15;
                payload.max_days_left = 20;
            }
            
            try {
                const response = await fetch('/api/scrape', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const resData = await response.json();
                if (resData.error) {
                    alert(resData.error);
                } else {
                    document.getElementById('consoleLogs').innerText = 'Triggering background scraper...';
                    
                    // Restart polling if stopped
                    if (!pollingInterval) {
                        pollingInterval = setInterval(checkScraperStatus, 1000);
                    }
                }
            } catch (err) {
                console.error("Failed to start scrape:", err);
                alert("Failed to start scrape. Please try again.");
            }
        }

        async function triggerRescore() {
            try {
                const response = await fetch('/api/rescore', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                });
                const resData = await response.json();
                if (resData.error) {
                    alert(resData.error);
                } else {
                    document.getElementById('consoleLogs').innerText = 'Rescoring all tenders locally (no network)...';
                    if (!pollingInterval) {
                        pollingInterval = setInterval(checkScraperStatus, 1000);
                    }
                }
            } catch (err) {
                console.error("Failed to start rescore:", err);
                alert("Failed to start rescore. Please try again.");
            }
        }

        async function triggerClearWorkspace() {
            const sure = confirm(
                "Clear the ACTIVE profile's workspace?\n\n" +
                "This deletes ALL its tenders, downloaded PDFs and the Excel report.\n" +
                "Metadata is backed up to the workspace's backups/ folder first.\n" +
                "Other profiles are NOT affected."
            );
            if (!sure) return;
            try {
                const response = await fetch('/api/clear-workspace', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ confirm: true })
                });
                const resData = await response.json();
                if (resData.error) {
                    alert(resData.error);
                } else {
                    alert(resData.message);
                    location.reload();
                }
            } catch (err) {
                console.error("Failed to clear workspace:", err);
                alert("Failed to clear workspace. Please try again.");
            }
        }

        async function triggerManualAcquisition() {
            const bidId = document.getElementById('manualBidId').value.trim();
            if (!bidId) {
                alert("Please enter a Bid ID or Bid Number.");
                return;
            }
            
            try {
                const response = await fetch('/api/scrape/id', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ bid_id: bidId })
                });
                const resData = await response.json();
                if (resData.error) {
                    alert(resData.error);
                } else {
                    document.getElementById('consoleLogs').innerText = `Triggering manual acquisition for Bid: ${bidId}...`;
                    
                    // Restart polling if stopped
                    if (!pollingInterval) {
                        pollingInterval = setInterval(checkScraperStatus, 1000);
                    }
                }
            } catch (err) {
                console.error("Failed to start manual acquisition:", err);
                alert("Failed to start manual acquisition. Please try again.");
            }
        }

        async function overrideStatus(encodedBidNo, newStatus) {
            // Bid numbers reach the handler URI-encoded so quotes in the value
            // cannot break out of the onclick attribute.
            const bidNo = decodeURIComponent(encodedBidNo);
            try {
                const response = await fetch('/api/tenders/status', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ bid_no: bidNo, status: newStatus })
                });
                const resData = await response.json();
                if (resData.error) {
                    alert(resData.error);
                } else {
                    // Update locally and re-populate
                    const tender = tenders.find(t => t.bid_no === bidNo);
                    if (tender) {
                        tender.status = newStatus;
                        initDashboard();
                    }
                }
            } catch (err) {
                console.error("Error overriding status:", err);
                alert("Failed to update tender evaluation status.");
            }
        }

        // ==========================================================
        // Live Excel Curation & 10-Minute Activity Engine
        // ==========================================================

        function formatTimer(seconds) {
            if (seconds <= 0) return "00:00";
            const mins = Math.floor(seconds / 60);
            const secs = seconds % 60;
            return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
        }

        function updateLiveExcelHUD() {
            const hud = document.getElementById('liveExcelHud');
            const title = document.getElementById('liveExcelTitle');
            const subtitle = document.getElementById('liveExcelSubtitle');
            const timerText = document.getElementById('liveExcelTimerText');
            const countTag = document.getElementById('liveExcelCountTag');
            const btnDownload = document.getElementById('btnDownloadLiveExcel');
            const btnClose = document.getElementById('btnCloseLiveExcel');
            const savedCount = document.getElementById('savedExcelsCount');

            if (!hud) return;

            if (savedCount) {
                savedCount.innerText = (liveExcelState.todaySavedFiles || []).length;
            }

            if (liveExcelState.isActive) {
                hud.classList.add('is-active');
                if (title) title.innerText = 'Live Excel Curation Session (Active)';
                if (subtitle) {
                    subtitle.innerText = liveExcelState.updateCount > 0
                        ? `${liveExcelState.updateCount} change(s) recorded. Resets on new change. Auto-saves at 00:00.`
                        : 'Session started. 10m window open — select tenders or it will be removed on timeout.';
                }
                if (timerText) timerText.innerText = formatTimer(liveExcelState.secondsRemaining);
                if (countTag) {
                    countTag.innerText = `${liveExcelState.tenderBids.size} Tender(s) in Workbook`;
                    countTag.className = liveExcelState.tenderBids.size > 0 ? 'tag tag-success' : 'tag tag-neutral';
                }
                if (btnDownload) {
                    btnDownload.style.display = 'inline-flex';
                    btnDownload.disabled = false;
                }
                if (btnClose) {
                    btnClose.style.display = 'inline-flex';
                    btnClose.disabled = liveExcelState.tenderBids.size === 0;
                }
            } else {
                hud.classList.remove('is-active');
                if (title) {
                    if (liveExcelState.status === 'saved' && liveExcelState.lastSavedFilename) {
                        title.innerText = `Live Excel Finalized: ${liveExcelState.lastSavedFilename}`;
                    } else if (liveExcelState.status === 'removed') {
                        title.innerText = 'Live Excel Expired & Removed (0 Updates)';
                    } else {
                        title.innerText = 'Live Excel: Idle';
                    }
                }
                if (subtitle) {
                    subtitle.innerText = 'Starts automatically after a scrape, or click ➕ Move to Excel on any card to curate.';
                }
                if (timerText) timerText.innerText = '--:--';
                if (countTag) {
                    countTag.innerText = 'No Active Session';
                    countTag.className = 'tag tag-neutral';
                }
                if (btnDownload) {
                    btnDownload.style.display = 'none';
                }
                if (btnClose) {
                    btnClose.style.display = 'none';
                }
            }
        }

        function updateCardLiveExcelButtons() {
            document.querySelectorAll('.btn-live-excel').forEach(btn => {
                const bidNo = decodeURIComponent(btn.getAttribute('data-bid-no') || '');
                if (!bidNo) return;
                const inExcel = liveExcelState.tenderBids.has(bidNo);
                if (inExcel) {
                    btn.classList.add('in-excel');
                    btn.innerHTML = '<span>📊 In Live Excel ✓</span>';
                    btn.title = 'Click to remove from Live Excel';
                } else {
                    btn.classList.remove('in-excel');
                    btn.innerHTML = '<span>➕ Move to Excel</span>';
                    btn.title = 'Move tender to Live Excel summary';
                }
            });
        }

        function updateLiveExcelFromAPI(data) {
            if (!data) return;
            liveExcelState.isActive = Boolean(data.is_active);
            liveExcelState.status = data.status || 'idle';
            liveExcelState.secondsRemaining = data.seconds_remaining || 0;
            liveExcelState.timeoutSeconds = data.timeout_seconds || 600;
            liveExcelState.updateCount = data.update_count || 0;
            liveExcelState.tenderBids = new Set(data.tender_bids || []);
            liveExcelState.lastSavedFilename = data.last_saved_filename || null;
            liveExcelState.todaySavedFiles = data.today_saved_files || [];
            liveExcelState.lastMessage = data.last_message || '';

            updateLiveExcelHUD();
            updateCardLiveExcelButtons();
        }

        async function fetchLiveExcelStatus() {
            try {
                const res = await fetch('/api/live-excel');
                if (!res.ok) return;
                const data = await res.json();
                updateLiveExcelFromAPI(data);
            } catch (err) {
                console.error("Error fetching live excel status:", err);
            }
        }

        async function toggleLiveExcel(encodedBidNo) {
            const bidNo = decodeURIComponent(encodedBidNo);
            try {
                const res = await fetch('/api/live-excel/toggle', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ bid_no: bidNo })
                });
                const data = await res.json();
                if (data.error) {
                    alert(data.error);
                    return;
                }
                if (data.status) {
                    updateLiveExcelFromAPI(data.status);
                } else {
                    await fetchLiveExcelStatus();
                }
            } catch (err) {
                console.error("Error toggling live excel:", err);
            }
        }

        async function confirmCloseLiveExcel(save = true) {
            if (save && liveExcelState.tenderBids.size === 0) {
                alert("No tenders in active Excel to save.");
                return;
            }
            const msg = save
                ? "Close and permanently save current Live Excel workbook now?"
                : "Discard current Live Excel session?";
            if (!confirm(msg)) return;

            try {
                const res = await fetch('/api/live-excel/close', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ save: save })
                });
                const data = await res.json();
                if (data.error) {
                    alert(data.error);
                    return;
                }
                if (data.status) {
                    updateLiveExcelFromAPI(data.status);
                } else {
                    await fetchLiveExcelStatus();
                }
                if (save && data.filename) {
                    alert(`Workbook saved successfully as ${data.filename}!`);
                }
            } catch (err) {
                console.error("Error closing live excel:", err);
            }
        }

        function openSavedExcelsModal() {
            const modal = document.getElementById('savedExcelsModal');
            if (!modal) return;
            renderSavedExcelsList();
            modal.style.display = 'flex';
        }

        function closeSavedExcelsModal() {
            const modal = document.getElementById('savedExcelsModal');
            if (modal) modal.style.display = 'none';
        }

        function renderSavedExcelsList() {
            const container = document.getElementById('savedExcelsList');
            if (!container) return;
            const files = liveExcelState.todaySavedFiles || [];
            if (files.length === 0) {
                container.innerHTML = `
                    <div style="text-align: center; padding: 2rem; color: var(--text-secondary);">
                        <div style="font-size: 2rem; margin-bottom: 0.5rem;">📄</div>
                        <div>No daily Excels exported yet today.</div>
                    </div>
                `;
                return;
            }

            container.innerHTML = files.map(f => `
                <div style="display: flex; align-items: center; justify-content: space-between; padding: 0.75rem 1rem; border: 1px solid var(--border-color); border-radius: 8px; background: var(--surface-sunken); gap: 1rem;">
                    <div style="display: flex; align-items: center; gap: 0.75rem;">
                        <span style="font-size: 1.5rem;">📊</span>
                        <div>
                            <strong style="font-size: 0.9rem; color: var(--text-primary);">${escapeHtml(f.filename)}</strong>
                            <div style="font-size: 0.75rem; color: var(--text-secondary);">
                                ${f.is_today ? '🟢 Today' : ''} • Saved at ${escapeHtml(f.modified || '')} • ${(f.size_bytes / 1024).toFixed(1)} KB
                            </div>
                        </div>
                    </div>
                    <a href="${f.url}" class="btn btn-secondary" style="padding: 0.4rem 0.8rem; font-size: 0.8rem; text-decoration: none;" download>
                        ⬇️ Download
                    </a>
                </div>
            `).join('');
        }

        let tickerStarted = false;
        function startLiveExcelTicker() {
            if (tickerStarted) return;
            tickerStarted = true;
            setInterval(() => {
                if (liveExcelState.isActive && liveExcelState.secondsRemaining > 0) {
                    liveExcelState.secondsRemaining -= 1;
                    const timerText = document.getElementById('liveExcelTimerText');
                    if (timerText) timerText.innerText = formatTimer(liveExcelState.secondsRemaining);
                    if (liveExcelState.secondsRemaining === 0) {
                        setTimeout(fetchLiveExcelStatus, 800);
                    }
                }
            }, 1000);

            // Periodic poll every 8s
            setInterval(fetchLiveExcelStatus, 8000);
        }

        // ==========================================================
        // Today's Scrapes Triage Logic
        // ==========================================================

        function updateTodayTriageCounts() {
            const today = localTodayISO();
            const todayTenders = tenders.filter(t => (t.first_seen || '') === today);
            
            let proceedCount = 0;
            let previewCount = 0;
            let rejectCount = 0;

            todayTenders.forEach(t => {
                const rec = (t.analysis && t.analysis.recommendation) || '';
                const st = t.status || 'Pending Review';
                if (st === 'Shortlisted' || rec === 'Pursue') {
                    proceedCount++;
                } else if (st === 'Rejected' || rec === 'Drop') {
                    rejectCount++;
                } else {
                    previewCount++;
                }
            });

            const cntAll = document.getElementById('cnt-today-all');
            const cntProceed = document.getElementById('cnt-today-proceed');
            const cntPreview = document.getElementById('cnt-today-preview');
            const cntReject = document.getElementById('cnt-today-reject');

            if (cntAll) cntAll.innerText = todayTenders.length;
            if (cntProceed) cntProceed.innerText = proceedCount;
            if (cntPreview) cntPreview.innerText = previewCount;
            if (cntReject) cntReject.innerText = rejectCount;
        }

        function setTodayTriage(triageCategory) {
            currentTodayTriage = triageCategory;
            const today = localTodayISO();

            // Set date filter to today
            currentDate = today;
            renderDateFilters();

            // Update pill active classes
            const pills = {
                'all': document.getElementById('pill-today-all'),
                'proceed': document.getElementById('pill-today-proceed'),
                'preview': document.getElementById('pill-today-preview'),
                'reject': document.getElementById('pill-today-reject'),
            };
            Object.keys(pills).forEach(k => {
                if (pills[k]) {
                    pills[k].classList.remove('active', 'active-proceed', 'active-preview', 'active-reject');
                    if (k === triageCategory) {
                        if (k === 'proceed') pills[k].classList.add('active-proceed');
                        else if (k === 'preview') pills[k].classList.add('active-preview');
                        else if (k === 'reject') pills[k].classList.add('active-reject');
                        else pills[k].classList.add('active');
                    }
                }
            });

            filterData();
        }

        async function addTodayProceedToLiveExcel() {
            const today = localTodayISO();
            const todayProceed = tenders.filter(t => {
                if ((t.first_seen || '') !== today) return false;
                const rec = (t.analysis && t.analysis.recommendation) || '';
                const st = t.status || 'Pending Review';
                return st === 'Shortlisted' || rec === 'Pursue';
            });

            if (todayProceed.length === 0) {
                alert("No Proceed/Shortlisted tenders found for today.");
                return;
            }

            const bidNos = todayProceed.map(t => t.bid_no);
            try {
                const res = await fetch('/api/live-excel/add-batch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ bid_nos: bidNos })
                });
                const data = await res.json();
                if (data.error) {
                    alert(data.error);
                    return;
                }
                updateLiveExcelFromAPI(data);
                alert(`Added ${bidNos.length} Proceed tenders to the Live Excel session! 10m countdown reset.`);
            } catch (e) {
                console.error("Error adding proceed tenders to live excel:", e);
            }
        }

        function showSetupState() {
            const container = document.getElementById('mainContainer');
            container.innerHTML = `
                <div class="state-container">
                    <div class="state-icon">📡</div>
                    <h2 class="state-title">No Scraped Data Discovered</h2>
                    <p class="state-message">
                        The dashboard backend is running, but it looks like you haven't run the scraper yet or the metadata file is missing in the <code>tenders/</code> folder.
                    </p>
                    <p class="state-message">
                        Click the "Scrape Portal" button in the top right to start a keyword search and download RFPs.
                    </p>
                </div>
            `;
        }

        function initDashboard() {
            const container = document.getElementById('mainContainer');
            
            // Restore content area if it was replaced by setup state
            if (!document.getElementById('contentArea')) {
                window.location.reload();
                return;
            }

            // Populate metrics
            const total = tenders.length;
            const shortlisted = tenders.filter(t => t.status === 'Shortlisted').length;
            const ratio = total > 0 ? Math.round((shortlisted / total) * 100) : 0;

            document.getElementById('metric-total').innerText = total;
            document.getElementById('metric-downloaded').innerText = shortlisted;
            document.getElementById('metric-ratio').innerText = ratio + '%';

            // Populate badges and render filters
            updateBadges();
            updateTodayTriageCounts();
            updateLiveExcelHUD();
            filterData();
        }

        function renderKeywordFilters() {
            const filtersContainer = document.getElementById('keywordFilters');
            if (!filtersContainer) return;
            
            // Scope keyword options to the currently selected Date and Business Line (broad type)
            let scopedTenders = tenders;
            if (currentDate !== 'all') {
                scopedTenders = scopedTenders.filter(t => {
                    const seen = t.first_seen || '';
                    return currentDate === 'unknown' ? !seen : seen === currentDate;
                });
            }
            if (currentBusinessLine !== 'all') {
                scopedTenders = scopedTenders.filter(t => {
                    return t.analysis && t.analysis.business_line && t.analysis.business_line.id === currentBusinessLine;
                });
            }

            let html = `
                <button class="filter-btn ${currentKeyword === 'all' ? 'active' : ''}" id="kw-all" onclick="setKeywordFilter('all')">
                    <span>All Keywords</span>
                    <span class="badge-count">${scopedTenders.length}</span>
                </button>
            `;
            
            const kwMap = new Map();
            scopedTenders.forEach(t => {
                const rawKw = t.keyword || '';
                rawKw.split(',').forEach(k => {
                    const clean = k.trim();
                    if (clean) {
                        const lower = clean.toLowerCase();
                        if (!kwMap.has(lower)) {
                            kwMap.set(lower, { display: clean, count: 0 });
                        }
                        kwMap.get(lower).count += 1;
                    }
                });
            });
            
            const sortedKws = Array.from(kwMap.values()).sort((a, b) => a.display.localeCompare(b.display));
            sortedKws.forEach(item => {
                const kwLower = item.display.toLowerCase();
                html += `
                    <button class="filter-btn ${currentKeyword === kwLower ? 'active' : ''}" onclick="setKeywordFilter('${kwLower}')">
                        <span style="text-transform: capitalize;">${item.display}</span>
                        <span class="badge-count">${item.count}</span>
                    </button>
                `;
            });
            
            filtersContainer.innerHTML = html;
        }

        // --- Scrape-date navigation -------------------------------------------
        // Tenders carry `first_seen` (YYYY-MM-DD), stamped when a scrape first
        // discovers them. Dates render newest-first so the most recent run is the
        // default landing point instead of one long undifferentiated scroll.

        function localTodayISO() {
            const d = new Date();
            const pad = n => String(n).padStart(2, '0');
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
        }

        function formatSeenDate(iso) {
            const today = localTodayISO();
            if (iso === today) return 'Today';
            const parts = iso.split('-').map(Number);
            if (parts.length !== 3 || parts.some(isNaN)) return iso;
            const d = new Date(parts[0], parts[1] - 1, parts[2]);
            const diff = Math.round(
                (new Date(today.split('-')[0], today.split('-')[1] - 1, today.split('-')[2]) - d)
                / 86400000);
            if (diff === 1) return 'Yesterday';
            return d.toLocaleDateString(undefined,
                { day: 'numeric', month: 'short', weekday: 'short' });
        }

        function renderDateFilters() {
            const container = document.getElementById('dateFilters');
            if (!container) return;

            const counts = new Map();
            let undated = 0;
            tenders.forEach(t => {
                const seen = t.first_seen || '';
                if (!seen) { undated++; return; }
                counts.set(seen, (counts.get(seen) || 0) + 1);
            });

            const today = localTodayISO();
            let html = `
                <button class="filter-btn ${currentDate === 'all' ? 'active' : ''}" onclick="setDateFilter('all')">
                    <span>All Dates</span>
                    <span class="badge-count">${tenders.length}</span>
                </button>
            `;

            // A scrape running today should show its bucket even before the first
            // tender lands, so the count visibly fills as results arrive.
            const dates = Array.from(counts.keys());
            if (!counts.has(today)) dates.push(today);
            dates.sort().reverse();

            dates.forEach(d => {
                const count = counts.get(d) || 0;
                const isToday = d === today;
                html += `
                    <button class="filter-btn ${currentDate === d ? 'active' : ''}"
                            onclick="setDateFilter('${d}')"
                            ${isToday ? 'style="box-shadow: inset 2px 0 0 var(--primary-accent);"' : ''}>
                        <span>${isToday ? '🟢 ' : ''}${formatSeenDate(d)}</span>
                        <span class="badge-count">${count}</span>
                    </button>
                `;
            });

            if (undated > 0) {
                html += `
                    <button class="filter-btn ${currentDate === 'unknown' ? 'active' : ''}" onclick="setDateFilter('unknown')">
                        <span>Undated</span>
                        <span class="badge-count">${undated}</span>
                    </button>
                `;
            }

            container.innerHTML = html;
        }

        function renderBusinessLineFilters() {
            const container = document.getElementById('businessLineFilters');
            if (!container) return;

            let targetTenders = tenders;
            if (currentDate !== 'all') {
                targetTenders = targetTenders.filter(t => {
                    const seen = t.first_seen || '';
                    return currentDate === 'unknown' ? !seen : seen === currentDate;
                });
            }

            const lines = (companyProfile && companyProfile.business_lines && companyProfile.business_lines.length > 0)
                ? companyProfile.business_lines
                : (DEFAULT_COMPANY_PROFILE.business_lines || []);

            let html = `
                <button class="filter-btn ${currentBusinessLine === 'all' ? 'active' : ''}" id="bl-all" onclick="setBusinessLineFilter('all')">
                    <span>All Lines</span>
                    <span class="badge-count">${targetTenders.length}</span>
                </button>
            `;

            lines.forEach(line => {
                const count = targetTenders.filter(t => t.analysis && t.analysis.business_line && t.analysis.business_line.id === line.id).length;
                const isActive = currentBusinessLine === line.id;
                html += `
                    <button class="filter-btn ${isActive ? 'active' : ''}" id="bl-${line.id}" onclick="setBusinessLineFilter('${line.id}')">
                        <span>${line.label || line.id}</span>
                        <span class="badge-count">${count}</span>
                    </button>
                `;
            });

            container.innerHTML = html;
        }

        // --- Procurement portal filter ---------------------------------------
        // Previously a hardcoded list of three portals wired to a function that
        // was never defined, so every button was a no-op reading zero. It is now
        // built from the portals actually present in the loaded data, grouped by
        // the family each belongs to.

        const PORTAL_FAMILIES = [
            { id: 'core_procurement',      label: 'Marketplace' },
            { id: 'defence',               label: 'Defence' },
            { id: 'defence_psu',           label: 'Defence' },
            { id: 'defence_indigenization', label: 'Defence' },
            { id: 'defence_innovation',    label: 'Defence' },
            { id: 'space_defence',         label: 'Space & Defence' },
            { id: 'central_gov',           label: 'Central Government' },
            { id: 'central_psu',           label: 'Central PSU' },
            { id: 'railways',              label: 'Railways' },
            { id: 'railways_psu',          label: 'Railways' },
            { id: 'state_gov',             label: 'State Government' },
        ];

        function portalFamilyLabel(category) {
            const found = PORTAL_FAMILIES.find(f => f.id === category);
            return found ? found.label : 'Other Portals';
        }

        function portalLabel(sourceId, fallbackName) {
            const known = sourceCatalog[sourceId];
            if (known && known.name) return known.name;
            return fallbackName || sourceId;
        }

        async function loadSourceCatalog() {
            try {
                const response = await fetch('/api/sources');
                const data = await response.json();
                const catalog = {};
                (data.sources || []).forEach(src => {
                    if (src && src.id) catalog[src.id] = { name: src.name || src.id, category: src.category || '' };
                });
                sourceCatalog = catalog;
            } catch (err) {
                // Names then fall back to whatever the tender records carry.
                console.error("Failed to load portal catalog:", err);
            }
            renderSourceFilters();
        }

        function renderSourceFilters() {
            const container = document.getElementById('sourceFilters');
            if (!container) return;

            // Portal options honour the date filter for the same reason the
            // keyword options do: they describe the slice you are looking at.
            let scoped = tenders;
            if (currentDate !== 'all') {
                scoped = scoped.filter(t => {
                    const seen = t.first_seen || '';
                    return currentDate === 'unknown' ? !seen : seen === currentDate;
                });
            }

            const counts = new Map();
            scoped.forEach(t => {
                const id = t.source_id || 'unknown';
                if (!counts.has(id)) {
                    counts.set(id, { id: id, name: portalLabel(id, t.source_name), count: 0 });
                }
                counts.get(id).count += 1;
            });

            let html = `
                <button class="filter-btn ${currentSource === 'all' ? 'active' : ''}" onclick="setSourceFilter('all')">
                    <span>All Portals</span>
                    <span class="badge-count">${scoped.length}</span>
                </button>
            `;

            // Group by family, families ordered by their biggest portal.
            const families = new Map();
            Array.from(counts.values()).forEach(entry => {
                const category = (sourceCatalog[entry.id] || {}).category || '';
                const label = portalFamilyLabel(category);
                if (!families.has(label)) families.set(label, []);
                families.get(label).push(entry);
            });

            const orderedFamilies = Array.from(families.entries()).map(([label, entries]) => {
                entries.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
                return { label: label, entries: entries, total: entries.reduce((s, e) => s + e.count, 0) };
            }).sort((a, b) => b.total - a.total);

            orderedFamilies.forEach(family => {
                if (orderedFamilies.length > 1) {
                    html += `<div class="filter-group-label">${escapeHtml(family.label)}</div>`;
                }
                family.entries.forEach(entry => {
                    html += `
                        <button class="filter-btn ${currentSource === entry.id ? 'active' : ''}"
                                onclick="setSourceFilter('${escapeHtml(entry.id)}')"
                                title="${escapeHtml(entry.name)}">
                            <span>${escapeHtml(entry.name)}</span>
                            <span class="badge-count">${entry.count}</span>
                        </button>
                    `;
                });
            });

            container.innerHTML = html;
        }

        function setSourceFilter(sourceId) {
            currentSource = sourceId;
            renderSourceFilters();
            filterData();
        }

        function setDateFilter(d) {
            currentDate = d;
            if (d !== localTodayISO()) {
                currentTodayTriage = 'all';
                const pills = ['pill-today-all', 'pill-today-proceed', 'pill-today-preview', 'pill-today-reject'];
                pills.forEach(id => {
                    const el = document.getElementById(id);
                    if (el) el.classList.remove('active', 'active-proceed', 'active-preview', 'active-reject');
                });
            } else {
                const pillAll = document.getElementById('pill-today-all');
                if (pillAll && currentTodayTriage === 'all') pillAll.classList.add('active');
            }
            renderDateFilters();
            renderBusinessLineFilters();
            renderSourceFilters();
            renderKeywordFilters();
            filterData();
        }

        function setBadge(id, value) {
            const el = document.getElementById(id);
            if (el) el.innerText = value;
        }

        /* One pass over the tenders, not fifteen. At 2,500 records the old
           chain of .filter() calls walked the array ~15 times on every render
           and re-parsed every deadline five of those times. */
        function updateBadges() {
            renderDateFilters();
            renderBusinessLineFilters();
            renderSourceFilters();
            renderKeywordFilters();

            const counts = {
                shortlisted: 0, pending: 0, rejected: 0,
                pursue: 0, review: 0, drop: 0,
                under5l: 0, sweet: 0, over3cr: 0,
                dlActionable: 0, dl1520: 0, dlCritical: 0, dlWeek: 0,
                dl2weeks: 0, dlOver20: 0,
            };

            tenders.forEach(t => {
                const status = t.status || 'Pending Review';
                if (status === 'Shortlisted') counts.shortlisted++;
                else if (status === 'Rejected') counts.rejected++;
                else counts.pending++;

                const analysis = t.analysis;
                if (analysis) {
                    if (analysis.recommendation === 'Pursue') counts.pursue++;
                    else if (analysis.recommendation === 'Review') counts.review++;
                    else if (analysis.recommendation === 'Drop') counts.drop++;

                    const value = analysis.est_value_inr;
                    if (value > 0 && value < 500000) counts.under5l++;
                    else if (value >= 500000 && value <= 30000000) counts.sweet++;
                    else if (value > 30000000) counts.over3cr++;
                }

                const days = gemDaysToDeadline(t.end_date);
                if (days !== null) {
                    if (days >= actionableMinDays()) counts.dlActionable++;
                    if (days >= 0 && days <= 3) counts.dlCritical++;
                    else if (days >= 4 && days <= 7) counts.dlWeek++;
                    else if (days >= 8 && days <= 14) counts.dl2weeks++;
                    if (days >= 15 && days <= 20) counts.dl1520++;
                    else if (days > 20) counts.dlOver20++;
                }
            });

            const total = tenders.length;
            setBadge('stat-cnt-all', total);
            setBadge('stat-cnt-short', counts.shortlisted);
            setBadge('stat-cnt-pending', counts.pending);
            setBadge('stat-cnt-rejected', counts.rejected);

            setBadge('rec-cnt-all', total);
            setBadge('rec-cnt-pursue', counts.pursue);
            setBadge('rec-cnt-review', counts.review);
            setBadge('rec-cnt-drop', counts.drop);

            setBadge('vb-cnt-all', total);
            setBadge('vb-cnt-under-5l', counts.under5l);
            setBadge('vb-cnt-sweet', counts.sweet);
            setBadge('vb-cnt-over-3cr', counts.over3cr);

            setBadge('dl-cnt-all', total);
            setBadge('dl-cnt-actionable', counts.dlActionable);
            setBadge('dl-cnt-15-20', counts.dl1520);
            setBadge('dl-cnt-critical', counts.dlCritical);
            setBadge('dl-cnt-week', counts.dlWeek);
            setBadge('dl-cnt-2weeks', counts.dl2weeks);
            setBadge('dl-cnt-over20', counts.dlOver20);
        }

        function setKeywordFilter(kw) {
            currentKeyword = kw;
            renderKeywordFilters();
            filterData();
        }

        function setStatusFilter(status) {
            const btns = document.querySelectorAll('#statusFilters .filter-btn');
            btns.forEach(b => b.classList.remove('active'));

            if (status === 'all') document.getElementById('stat-all').classList.add('active');
            else if (status === 'Shortlisted') document.getElementById('stat-shortlisted').classList.add('active');
            else if (status === 'Pending Review') document.getElementById('stat-pending').classList.add('active');
            else if (status === 'Rejected') document.getElementById('stat-rejected').classList.add('active');

            currentStatus = status;
            filterData();
        }

        function setRecFilter(rec) {
            const btns = document.querySelectorAll('#recFilters .filter-btn');
            btns.forEach(b => b.classList.remove('active'));

            if (rec === 'all') document.getElementById('rec-all').classList.add('active');
            else if (rec === 'Pursue') document.getElementById('rec-pursue').classList.add('active');
            else if (rec === 'Review') document.getElementById('rec-review').classList.add('active');
            else if (rec === 'Drop') document.getElementById('rec-drop').classList.add('active');

            currentRec = rec;
            filterData();
        }

        function setBusinessLineFilter(bl) {
            currentBusinessLine = bl;
            renderBusinessLineFilters();
            renderKeywordFilters();
            filterData();
        }

        function setValueBandFilter(vb) {
            const btns = document.querySelectorAll('#valueBandFilters .filter-btn');
            btns.forEach(b => b.classList.remove('active'));

            if (vb === 'all') document.getElementById('vb-all').classList.add('active');
            else if (vb === 'under-5l') document.getElementById('vb-under-5l').classList.add('active');
            else if (vb === 'sweet') document.getElementById('vb-sweet').classList.add('active');
            else if (vb === 'over-3cr') document.getElementById('vb-over-3cr').classList.add('active');

            currentValueBand = vb;
            filterData();
        }

        function setDeadlineFilter(dl) {
            const btns = document.querySelectorAll('#deadlineFilters .filter-btn');
            btns.forEach(b => b.classList.remove('active'));

            if (dl === 'actionable') document.getElementById('dl-actionable').classList.add('active');
            else if (dl === 'all') document.getElementById('dl-all').classList.add('active');
            else if (dl === '15-20') document.getElementById('dl-15-20').classList.add('active');
            else if (dl === 'critical') document.getElementById('dl-critical').classList.add('active');
            else if (dl === 'week') document.getElementById('dl-week').classList.add('active');
            else if (dl === '2weeks') document.getElementById('dl-2weeks').classList.add('active');
            else if (dl === 'over20') document.getElementById('dl-over20').classList.add('active');

            currentDeadlineBand = dl;
            filterData();
        }

        // Deadline strings repeat heavily across a scrape, and this is called
        // once per tender per band per render. Cached by string; cleared on
        // every data refresh so the day count cannot go stale.
        const deadlineDaysCache = new Map();

        function clearDeadlineCache() {
            deadlineDaysCache.clear();
        }

        function actionableMinDays() {
            const configured = scoringConfig && scoringConfig.date_window
                ? Number(scoringConfig.date_window.min_days_to_bid)
                : 5;
            return Number.isFinite(configured) && configured >= 0 ? configured : 5;
        }

        function gemDateTimestamp(dateStr) {
            if (!dateStr) return Number.NEGATIVE_INFINITY;
            const match = String(dateStr).trim().match(
                /^(\d{1,2})-(\d{1,2})-(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)?)?$/i
            );
            if (match) {
                let hours = Number(match[4] || 0);
                const meridiem = (match[6] || '').toUpperCase();
                if (meridiem === 'PM' && hours < 12) hours += 12;
                if (meridiem === 'AM' && hours === 12) hours = 0;
                return new Date(
                    Number(match[3]), Number(match[2]) - 1, Number(match[1]),
                    hours, Number(match[5] || 0)
                ).getTime();
            }
            const fallback = Date.parse(dateStr);
            return Number.isNaN(fallback) ? Number.NEGATIVE_INFINITY : fallback;
        }

        // Feature C: parse GeM "DD-MM-YYYY HH:MM AM/PM" end date → whole days from now
        // (negative = already closed). Returns null when the date is unparseable.
        function gemDaysToDeadline(dateStr) {
            if (!dateStr) return null;
            if (deadlineDaysCache.has(dateStr)) return deadlineDaysCache.get(dateStr);
            const days = computeDaysToDeadline(dateStr);
            deadlineDaysCache.set(dateStr, days);
            return days;
        }

        function computeDaysToDeadline(dateStr) {
            try {
                const parts = dateStr.trim().split(' ');
                if (parts.length < 2) return null;
                const dateParts = parts[0].split('-');
                const timeParts = parts[1].split(':');
                if (dateParts.length < 3 || timeParts.length < 2) return null;
                const day = parseInt(dateParts[0]);
                const month = parseInt(dateParts[1]) - 1;
                const year = parseInt(dateParts[2]);
                let hours = parseInt(timeParts[0]);
                const minutes = parseInt(timeParts[1]);
                if (parts[2] && parts[2].toUpperCase() === 'PM' && hours < 12) hours += 12;
                if (parts[2] && parts[2].toUpperCase() === 'AM' && hours === 12) hours = 0;
                if ([day, month, year, hours, minutes].some(isNaN)) return null;
                const deadline = new Date(year, month, day, hours, minutes);
                if (isNaN(deadline.getTime())) return null;
                return Math.ceil((deadline - new Date()) / (1000 * 60 * 60 * 24));
            } catch (e) {
                return null;
            }
        }

        function filterData() {
            searchStr = document.getElementById('searchInput').value.toLowerCase().trim();

            let filtered = tenders.filter(t => {
                const matchKeyword = currentKeyword === 'all' || (t.keyword || '').toLowerCase().includes(currentKeyword);
                
                const tStatus = t.status || 'Pending Review';
                const matchStatus = currentStatus === 'all' || tStatus === currentStatus;
                
                const matchSearch = searchStr === '' ||
                                    (t.title || '').toLowerCase().includes(searchStr) ||
                                    (t.bid_no || '').toLowerCase().includes(searchStr) ||
                                    (t.department || '').toLowerCase().includes(searchStr);

                const matchSource = currentSource === 'all' || (t.source_id || 'unknown') === currentSource;

                // Phase 2 Filters
                const analysis = t.analysis || {};

                const matchRec = currentRec === 'all' || (analysis.recommendation && analysis.recommendation === currentRec);
                const matchBL = currentBusinessLine === 'all' || (analysis.business_line && analysis.business_line.id === currentBusinessLine);
                
                let matchVal = true;
                if (currentValueBand !== 'all') {
                    const estVal = analysis.est_value_inr || 0;
                    if (currentValueBand === 'under-5l') {
                        matchVal = estVal < 500000 && estVal > 0;
                    } else if (currentValueBand === 'sweet') {
                        matchVal = estVal >= 500000 && estVal <= 30000000;
                    } else if (currentValueBand === 'over-3cr') {
                        matchVal = estVal > 30000000;
                    }
                }

                let matchDeadline = true;
                if (currentDeadlineBand !== 'all') {
                    const days = gemDaysToDeadline(t.end_date);
                    if (days === null) {
                        matchDeadline = false;
                    } else if (currentDeadlineBand === 'actionable') {
                        matchDeadline = days >= actionableMinDays();
                    } else if (currentDeadlineBand === '15-20') {
                        matchDeadline = days >= 15 && days <= 20;
                    } else if (currentDeadlineBand === 'critical') {
                        matchDeadline = days >= 0 && days <= 3;
                    } else if (currentDeadlineBand === 'week') {
                        matchDeadline = days >= 4 && days <= 7;
                    } else if (currentDeadlineBand === '2weeks') {
                        matchDeadline = days >= 8 && days <= 14;
                    } else if (currentDeadlineBand === 'over20') {
                        matchDeadline = days > 20;
                    }
                }

                // Scrape date: which run discovered this tender
                let matchDate = true;
                if (currentDate !== 'all') {
                    const seen = t.first_seen || '';
                    matchDate = currentDate === 'unknown' ? !seen : seen === currentDate;
                }

                // Today's Scrapes Triage Filter (Proceed, Preview, Reject)
                let matchTodayTriage = true;
                if (currentTodayTriage !== 'all' && currentDate === localTodayISO()) {
                    const rec = (analysis.recommendation || '');
                    if (currentTodayTriage === 'proceed') {
                        matchTodayTriage = (tStatus === 'Shortlisted' || rec === 'Pursue');
                    } else if (currentTodayTriage === 'preview') {
                        matchTodayTriage = (tStatus === 'Pending Review' || rec === 'Review');
                    } else if (currentTodayTriage === 'reject') {
                        matchTodayTriage = (tStatus === 'Rejected' || rec === 'Drop');
                    }
                }

                return matchKeyword && matchStatus && matchSearch && matchSource && matchRec
                    && matchBL && matchVal && matchDeadline && matchDate && matchTodayTriage;
            });

            // Sort logic
            const sortVal = document.getElementById('tendersSortSelect') ? document.getElementById('tendersSortSelect').value : 'none';
            if (sortVal !== 'none') {
                filtered.sort((a, b) => {
                    if (sortVal === 'published-newest') {
                        const publishedDiff = gemDateTimestamp(b.start_date) - gemDateTimestamp(a.start_date);
                        if (publishedDiff !== 0) return publishedDiff;
                        const prA = (a.analysis && a.analysis.priority_score != null) ? a.analysis.priority_score : -1;
                        const prB = (b.analysis && b.analysis.priority_score != null) ? b.analysis.priority_score : -1;
                        return prB - prA;
                    } else if (sortVal === 'priority-desc') {
                        const prA = (a.analysis && a.analysis.priority_score !== undefined && a.analysis.priority_score !== null) ? a.analysis.priority_score : -1;
                        const prB = (b.analysis && b.analysis.priority_score !== undefined && b.analysis.priority_score !== null) ? b.analysis.priority_score : -1;
                        return prB - prA;
                    } else if (sortVal === 'deadline-soonest') {
                        const parseGemDate = (dateStr) => {
                            if (!dateStr) return new Date(8640000000000000);
                            try {
                                const parts = dateStr.trim().split(' ');
                                if (parts.length < 2) return new Date(8640000000000000);
                                const dateParts = parts[0].split('-');
                                const timeParts = parts[1].split(':');
                                if (dateParts.length < 3) return new Date(8640000000000000);
                                
                                const day = parseInt(dateParts[0]);
                                const month = parseInt(dateParts[1]) - 1;
                                const year = parseInt(dateParts[2]);
                                
                                let hours = parseInt(timeParts[0]);
                                const minutes = parseInt(timeParts[1]);
                                
                                if (parts[2] && parts[2].toUpperCase() === 'PM' && hours < 12) hours += 12;
                                if (parts[2] && parts[2].toUpperCase() === 'AM' && hours === 12) hours = 0;
                                
                                return new Date(year, month, day, hours, minutes);
                            } catch (e) {
                                return new Date(8640000000000000);
                            }
                        };
                        const dateA = parseGemDate(a.end_date);
                        const dateB = parseGemDate(b.end_date);
                        return dateA - dateB;
                    } else if (sortVal === 'score-desc') {
                        const scoreA = (a.analysis && a.analysis.score !== null) ? a.analysis.score : -1;
                        const scoreB = (b.analysis && b.analysis.score !== null) ? b.analysis.score : -1;
                        return scoreB - scoreA;
                    } else if (sortVal === 'score-asc') {
                        const scoreA = (a.analysis && a.analysis.score !== null) ? a.analysis.score : 999;
                        const scoreB = (b.analysis && b.analysis.score !== null) ? b.analysis.score : 999;
                        return scoreA - scoreB;
                    } else if (sortVal === 'fit-desc') {
                        const fitA = (a.analysis && a.analysis.fit_score !== undefined && a.analysis.fit_score !== null) ? a.analysis.fit_score : -1;
                        const fitB = (b.analysis && b.analysis.fit_score !== undefined && b.analysis.fit_score !== null) ? b.analysis.fit_score : -1;
                        return fitB - fitA;
                    } else if (sortVal === 'fit-asc') {
                        const fitA = (a.analysis && a.analysis.fit_score !== undefined && a.analysis.fit_score !== null) ? a.analysis.fit_score : 999;
                        const fitB = (b.analysis && b.analysis.fit_score !== undefined && b.analysis.fit_score !== null) ? b.analysis.fit_score : 999;
                        return fitA - fitB;
                    }
                    return 0;
                });
            }

            renderTenders(filtered);
            updateTodayTriageCounts();
            updateCardLiveExcelButtons();
        }

        /* ====================================================================
           GROUPED RENDERING

           A scrape returns thousands of tenders. Rendering them as one flat
           list produced a page nobody could navigate and a DOM the browser
           struggled with, so the result set is bucketed into sections and only
           an open section builds its cards.
           ==================================================================== */

        // Ordered buckets, so a section that is empty this time still appears
        // in the same place next time. `keyOf` returns the bucket a tender
        // belongs to; anything not in `order` is appended alphabetically.
        const GROUPINGS = {
            recommendation: {
                order: ['Pursue', 'Review', 'Drop', 'Unscored'],
                accents: {
                    'Pursue': 'var(--success-color)',
                    'Review': 'var(--warning-color)',
                    'Drop': 'var(--failed-color)',
                    'Unscored': 'var(--neutral-color)',
                },
                subs: {
                    'Pursue': 'Strong fit — worth a bid decision',
                    'Review': 'Needs a human read before deciding',
                    'Drop': 'Screened out by fit or eligibility',
                    'Unscored': 'No analysis on record yet',
                },
                keyOf: t => (t.analysis && t.analysis.recommendation) || 'Unscored',
            },
            status: {
                order: ['Shortlisted', 'Pending Review', 'Rejected'],
                accents: {
                    'Shortlisted': 'var(--success-color)',
                    'Pending Review': 'var(--warning-color)',
                    'Rejected': 'var(--failed-color)',
                },
                keyOf: t => t.status || 'Pending Review',
            },
            deadline: {
                order: ['🔥 Closing in 3 days', '⏳ 4–7 days', '📆 8–14 days', '📅 15–20 days',
                        '🚀 More than 20 days', '⌛ Closed', 'No deadline on record'],
                accents: {
                    '🔥 Closing in 3 days': 'var(--failed-color)',
                    '⏳ 4–7 days': 'var(--warning-color)',
                    '📆 8–14 days': 'var(--primary-accent)',
                    '📅 15–20 days': 'var(--primary-accent)',
                    '🚀 More than 20 days': 'var(--success-color)',
                    '⌛ Closed': 'var(--neutral-color)',
                    'No deadline on record': 'var(--neutral-color)',
                },
                keyOf: t => {
                    const days = gemDaysToDeadline(t.end_date);
                    if (days === null) return 'No deadline on record';
                    if (days < 0) return '⌛ Closed';
                    if (days <= 3) return '🔥 Closing in 3 days';
                    if (days <= 7) return '⏳ 4–7 days';
                    if (days <= 14) return '📆 8–14 days';
                    if (days <= 20) return '📅 15–20 days';
                    return '🚀 More than 20 days';
                },
            },
            value: {
                order: ['Under ₹5 Lakh', '₹5 Lakh – ₹3 Crore', 'Above ₹3 Crore', 'Value not stated'],
                accents: {
                    'Under ₹5 Lakh': 'var(--neutral-color)',
                    '₹5 Lakh – ₹3 Crore': 'var(--success-color)',
                    'Above ₹3 Crore': 'var(--warning-color)',
                    'Value not stated': 'var(--neutral-color)',
                },
                subs: { '₹5 Lakh – ₹3 Crore': 'The configured sweet spot' },
                keyOf: t => {
                    const value = (t.analysis && t.analysis.est_value_inr) || 0;
                    if (!value) return 'Value not stated';
                    if (value < 500000) return 'Under ₹5 Lakh';
                    if (value <= 30000000) return '₹5 Lakh – ₹3 Crore';
                    return 'Above ₹3 Crore';
                },
            },
            source: {
                accents: {},
                keyOf: t => portalLabel(t.source_id || 'unknown', t.source_name),
            },
            business_line: {
                accents: {},
                keyOf: t => (t.analysis && t.analysis.business_line && t.analysis.business_line.label)
                    || 'Unclassified',
            },
            date: {
                accents: {},
                keyOf: t => (t.first_seen ? formatSeenDate(t.first_seen) : 'Undated'),
                sortKeyOf: t => t.first_seen || '',
                descending: true,
            },
        };

        function buildGroups(items, mode) {
            const spec = GROUPINGS[mode];
            if (!spec) return [{ key: 'all', label: '', sub: '', accent: '', items: items }];

            const buckets = new Map();
            items.forEach(tender => {
                const key = spec.keyOf(tender);
                if (!buckets.has(key)) {
                    buckets.set(key, {
                        key: key,
                        label: key,
                        sub: (spec.subs && spec.subs[key]) || '',
                        accent: spec.accents[key] || 'var(--primary-accent)',
                        sortKey: spec.sortKeyOf ? spec.sortKeyOf(tender) : '',
                        items: [],
                    });
                }
                buckets.get(key).items.push(tender);
            });

            const groups = Array.from(buckets.values());
            const order = spec.order || [];
            groups.sort((a, b) => {
                const ia = order.indexOf(a.key);
                const ib = order.indexOf(b.key);
                if (ia !== -1 || ib !== -1) {
                    if (ia === -1) return 1;
                    if (ib === -1) return -1;
                    return ia - ib;
                }
                if (spec.sortKeyOf) {
                    const cmp = String(a.sortKey).localeCompare(String(b.sortKey));
                    return spec.descending ? -cmp : cmp;
                }
                // Undeclared buckets (portals, business lines): biggest first.
                return b.items.length - a.items.length || a.label.localeCompare(b.label);
            });
            return groups;
        }

        function onGroupByChange() {
            const select = document.getElementById('tendersGroupSelect');
            currentGroupBy = select ? select.value : 'none';
            // A new grouping means new section keys; start from a clean slate
            // rather than carrying over an unrelated section's open state.
            openGroupKeys.clear();
            groupLimits.clear();
            filterData();
        }

        function toggleGroup(encodedKey) {
            const key = decodeURIComponent(encodedKey);
            const group = renderedGroups.find(g => g.key === key);
            if (!group) return;

            const element = document.querySelector(`[data-group-key="${CSS.escape(key)}"]`);
            if (!element) return;

            if (openGroupKeys.has(key)) {
                openGroupKeys.delete(key);
                element.classList.remove('open');
                element.querySelector('.group-body').innerHTML = '';
                element.querySelector('.group-header').setAttribute('aria-expanded', 'false');
                return;
            }

            openGroupKeys.add(key);
            element.classList.add('open');
            element.querySelector('.group-header').setAttribute('aria-expanded', 'true');
            element.querySelector('.group-body').innerHTML = renderGroupBody(group);
        }

        function showMoreInGroup(encodedKey) {
            const key = decodeURIComponent(encodedKey);
            const group = renderedGroups.find(g => g.key === key);
            if (!group) return;
            groupLimits.set(key, (groupLimits.get(key) || GROUP_PAGE_SIZE) + GROUP_PAGE_SIZE);
            const element = document.querySelector(`[data-group-key="${CSS.escape(key)}"]`);
            if (element) element.querySelector('.group-body').innerHTML = renderGroupBody(group);
        }

        function renderGroupBody(group) {
            const limit = groupLimits.get(group.key) || GROUP_PAGE_SIZE;
            const visible = group.items.slice(0, limit);
            let html = visible.map(buildTenderCard).join('');

            const remaining = group.items.length - visible.length;
            if (remaining > 0) {
                const encoded = encodeURIComponent(group.key);
                html += `
                    <button class="group-more" onclick="showMoreInGroup('${encoded}')">
                        Show ${Math.min(remaining, GROUP_PAGE_SIZE)} more &middot; ${remaining} remaining
                    </button>
                `;
            }
            return html;
        }

        function renderTenders(items) {
            const listContainer = document.getElementById('tendersList');
            document.getElementById('visible-count').innerText = items.length;
            document.getElementById('total-count').innerText = tenders.length;

            if (items.length === 0) {
                renderedGroups = [];
                listContainer.innerHTML = `
                    <div class="state-container" style="margin: 2rem auto; width: 100%;">
                        <div style="font-size: 2.5rem;">🔍</div>
                        <h3 class="state-title">No matching tenders found</h3>
                        <p class="state-message">Try adjusting your filters or search query.</p>
                    </div>
                `;
                return;
            }

            renderedGroups = buildGroups(items, currentGroupBy);

            if (currentGroupBy === 'none') {
                const group = renderedGroups[0];
                groupLimits.set(group.key, groupLimits.get(group.key) || GROUP_PAGE_SIZE);
                listContainer.innerHTML = renderGroupBody(group);
                return;
            }

            // The first section opens by default so the page is never a wall of
            // collapsed headers. This also has to fire when a filter change
            // means none of the sections the user had open still exist --
            // otherwise the results are all there but nothing is showing.
            const anyOpenStillPresent = renderedGroups.some(g => openGroupKeys.has(g.key));
            if (!anyOpenStillPresent && renderedGroups.length > 0) {
                openGroupKeys.add(renderedGroups[0].key);
            }

            listContainer.innerHTML = renderedGroups.map(group => {
                const isOpen = openGroupKeys.has(group.key);
                const encoded = encodeURIComponent(group.key);
                return `
                    <section class="tender-group ${isOpen ? 'open' : ''}"
                             data-group-key="${escapeHtml(group.key)}"
                             style="--group-accent: ${group.accent};">
                        <button class="group-header" onclick="toggleGroup('${encoded}')"
                                aria-expanded="${isOpen ? 'true' : 'false'}">
                            <svg class="group-chevron" width="14" height="14" fill="none"
                                 viewBox="0 0 24 24" stroke="currentColor" stroke-width="3">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
                            </svg>
                            <span class="group-swatch"></span>
                            <span>
                                <span class="group-label">${escapeHtml(group.label)}</span>
                                ${group.sub ? `<span class="group-sub">${escapeHtml(group.sub)}</span>` : ''}
                            </span>
                            <span class="group-count">${group.items.length}</span>
                        </button>
                        <div class="group-body">${isOpen ? renderGroupBody(group) : ''}</div>
                    </section>
                `;
            }).join('');
        }

        function buildTenderCard(tender) {
                // Score Badge and Classification Badge
                let statusClass = 'status-pending';
                let statusText = tender.status || 'Pending Review';
                if (statusText === 'Shortlisted') statusClass = 'status-shortlisted';
                if (statusText === 'Rejected') statusClass = 'status-rejected';

                let scoreBadge = '';
                let detailsAccordion = '';
                let confidenceHtml = '';
                let reanalyzeBtn = '';
                let businessLineTag = '';
                let eligibilityTag = '';
                let recommendationChip = '';
                let urgencyTag = '';

                // Feature C: deadline urgency chip (independent of PDF analysis)
                const daysToDeadline = gemDaysToDeadline(tender.end_date);
                if (daysToDeadline !== null) {
                    if (daysToDeadline < 0) {
                        urgencyTag = `<span class="tag tag-urgency-expired" title="Bid closing date has passed">⌛ Expired</span>`;
                    } else if (daysToDeadline <= 3) {
                        urgencyTag = `<span class="tag tag-urgency-critical" title="Closes in ${daysToDeadline} day(s)">🔥 Closing soon (${daysToDeadline}d)</span>`;
                    } else if (daysToDeadline <= 7) {
                        urgencyTag = `<span class="tag tag-urgency-soon" title="Closes in ${daysToDeadline} day(s)">⏳ Soon (${daysToDeadline}d)</span>`;
                    }
                }

                const isInLiveExcel = Boolean(liveExcelState && liveExcelState.tenderBids && liveExcelState.tenderBids.has(tender.bid_no));
                let liveExcelTag = isInLiveExcel ? `<span class="tag tag-in-excel" style="background: var(--success-bg); color: var(--success-color); border: 1px solid var(--success-border); font-weight: 700;">📊 In Live Excel</span>` : '';

                const isFinalized = Boolean(finalizedBidsMap && finalizedBidsMap[tender.bid_no]);
                const finalRec = isFinalized ? finalizedBidsMap[tender.bid_no] : null;
                let finalizedTag = isFinalized ? `<span class="tag tag-finalized" title="Finalized into Master Sheet as SL. NO #${finalRec.sl_no}">⭐ Finalized (#${finalRec.sl_no})</span>` : '';

                const analysis = tender.analysis;

                if (analysis) {
                    // Recommendation Chip (Phase 2)
                    if (analysis.recommendation) {
                        let recClass = '';
                        if (analysis.recommendation === 'Pursue') recClass = 'recommendation-pursue';
                        else if (analysis.recommendation === 'Review') recClass = 'recommendation-review';
                        else if (analysis.recommendation === 'Drop') recClass = 'recommendation-drop';
                        
                        recommendationChip = `<span class="rec-chip ${recClass}">${analysis.recommendation}</span>`;
                    }

                    // Business line tag (Phase 2)
                    if (analysis.business_line) {
                        const mk = analysis.business_line.matched_keywords;
                        const mkTitle = (mk && mk.length) ? ` title="Matched: ${escapeHtml(mk.join(', '))}"` : '';
                        businessLineTag = `<span class="tag tag-business-line"${mkTitle}>${escapeHtml(analysis.business_line.label)}</span>`;
                    }

                    // Eligibility tag (Phase 2). An unresolved verdict gets its
                    // own badge — showing nothing at all read as "no concerns",
                    // which is the same false reassurance as labelling it
                    // Eligible.
                    if (analysis.eligibility) {
                        const verdict = analysis.eligibility.verdict;
                        const detailTitle = escapeHtml(analysis.eligibility.detail || '');
                        if (verdict === 'turnover_gap') {
                            eligibilityTag = `<span class="tag tag-eligibility-warning" title="${detailTitle || 'Turnover requirement gap'}">⚠️ Turnover Gap</span>`;
                        } else if (verdict === 'eligible') {
                            eligibilityTag = `<span class="tag tag-eligibility-ok">✓ Eligible</span>`;
                        } else if (analysis.analysis_status !== 'failed') {
                            eligibilityTag = `<span class="tag tag-eligibility-unknown" title="${detailTitle || 'Eligibility could not be confirmed'}">? Eligibility Unconfirmed</span>`;
                        }
                    }

                    if (analysis.analysis_status === 'failed') {
                        scoreBadge = `<span class="score-badge" style="background: var(--neutral-bg); color: var(--neutral-color); border-color: var(--neutral-border);">Analysis Failed</span>`;
                        
                        const reasonsHtml = (analysis.reasons || []).map(r => `<li>${escapeHtml(r)}</li>`).join('');
                        detailsAccordion = `
                            <details class="tender-analysis-details" ontoggle="hydrateAnalysis(this, '${encodeURIComponent(tender.bid_no)}')">
                                <summary>View Automated RFP Score Details</summary>
                                <div class="analysis-content">
                                    <div class="analysis-reasons">
                                        <strong>Analysis Error:</strong>
                                        <ul style="color: var(--failed-color);">
                                            ${reasonsHtml}
                                        </ul>
                                    </div>
                                </div>
                            </details>
                        `;
                        
                        reanalyzeBtn = `
                            <button class="btn btn-secondary" onclick="reanalyzeBid('${encodeURIComponent(tender.bid_no)}')" style="padding: 0.45rem 1rem; font-size: 0.8rem; border-color: var(--primary-accent);">
                                <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 1121.21 8H17" />
                                </svg>
                                Re-analyze
                            </button>
                        `;
                    } else {
                        // Score Badge (Risk / Phase 1)
                        let riskClass = 'score-medium';
                        let scoreText = '';
                        // BE-26: card_only = no PDF; Fit scored from card metadata, Risk unknown
                        const cardOnly = analysis.analysis_status === 'card_only' || analysis.score === null || analysis.score === undefined;
                        // Only GeM documents go through the RFP parser. Other
                        // portals are card-scored by design, so they get no
                        // "fetch the PDF" affordance that could never work.
                        const isGemTender = (tender.source_id || 'gem') === 'gem';
                        if (cardOnly) {
                            scoreText = '';
                        } else if (analysis.score_scale === 100) {
                            const shortlistMin = (scoringConfig && scoringConfig.status_thresholds) ? scoringConfig.status_thresholds.shortlist_min : 70;
                            const rejectMax = (scoringConfig && scoringConfig.status_thresholds) ? scoringConfig.status_thresholds.reject_max : 40;
                            if (analysis.score >= shortlistMin) riskClass = 'score-high';
                            else if (analysis.score <= rejectMax) riskClass = 'score-low';
                            scoreText = `Risk: ${analysis.score}/100`;
                        } else {
                            if (analysis.score >= 7) riskClass = 'score-high';
                            else if (analysis.score <= 4) riskClass = 'score-low';
                            scoreText = `Score: ${analysis.score}/10`;
                        }
                        
                        let fitBadge = '';
                        if (analysis.fit_score !== undefined && analysis.fit_score !== null) {
                            let fitClass = 'score-medium';
                            if (analysis.fit_score >= 70) fitClass = 'score-high';
                            else if (analysis.fit_score <= 40) fitClass = 'score-low';
                            fitBadge = `<span class="score-badge ${fitClass}">Fit: ${analysis.fit_score}/100</span>`;
                        }

                        let priorityBadge = '';
                        if (analysis.priority_score !== undefined && analysis.priority_score !== null) {
                            let prClass = 'score-medium';
                            if (analysis.priority_score >= 70) prClass = 'score-high';
                            else if (analysis.priority_score <= 40) prClass = 'score-low';
                            priorityBadge = `<span class="score-badge ${prClass}" style="font-weight: 700;" title="Blended best-match ranking (Fit + Risk)">⭐ ${analysis.priority_score}</span>`;
                        }

                        const noPdfTitle = isGemTender
                            ? 'No RFP PDF — Fit scored from card metadata; tender terms (Risk) unknown'
                            : 'This portal is not parsed for RFP documents — Fit scored from listing metadata; tender terms (Risk) unknown';
                        const riskBadge = cardOnly
                            ? `<span class="score-badge" style="background: var(--neutral-bg); color: var(--neutral-color); border-color: var(--neutral-border);" title="${noPdfTitle}">No PDF</span>`
                            : `<span class="score-badge ${riskClass}">${scoreText}</span>`;
                        scoreBadge = `
                            <div style="display: flex; gap: 0.35rem; align-items: center;">
                                ${priorityBadge}
                                ${fitBadge}
                                ${riskBadge}
                            </div>
                        `;
                        if (cardOnly && isGemTender) {
                            reanalyzeBtn = `
                                <button class="btn btn-secondary" onclick="reanalyzeBid('${encodeURIComponent(tender.bid_no)}')" style="padding: 0.45rem 1rem; font-size: 0.8rem; border-color: var(--primary-accent);">
                                    <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                        <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 1121.21 8H17" />
                                    </svg>
                                    Fetch PDF & Re-analyze
                                </button>
                            `;
                        }
                        
                        if (analysis.confidence !== undefined && analysis.confidence !== null) {
                            const parsed = analysis.parsed_fields || 0;
                            const total = analysis.total_fields || 8;
                            const pct = Math.round(analysis.confidence * 100);
                            const isMuted = analysis.confidence === 1.0 ? 'confidence-muted' : '';
                            confidenceHtml = `<div class="confidence-indicator ${isMuted}">Parsed ${parsed}/${total} fields · ${pct}%</div>`;
                        }
                        
                        const epbgVal = analysis.epbg_required === 'Yes' ? (analysis.epbg_percentage || 'Yes') : 'No';
                        const prebidVal = analysis.pre_bid_required === 'Yes' ? (analysis.pre_bid_date || 'Yes') : 'No';
                        
                        let breakdownHtml = '';
                        if (analysis.breakdown && analysis.breakdown.length > 0) {
                            const totalWeight = analysis.breakdown.reduce((sum, item) => sum + item.weight, 0);
                            breakdownHtml = `
                                <div class="breakdown-container">
                                    <strong>Risk Score Breakdown:</strong>
                                    ${analysis.breakdown.map(item => {
                                        const maxPoints = totalWeight > 0 ? (100 * item.weight / totalWeight) : 0;
                                        const maxPointsFormatted = maxPoints.toFixed(1);
                                        const pct = maxPoints > 0 ? Math.min(100, Math.max(0, (item.points / maxPoints) * 100)) : 0;
                                        
                                        let barColor = 'var(--neutral-color)';
                                        if (item.subscore >= 0.7) {
                                            barColor = 'var(--success-color)';
                                        } else if (item.subscore <= 0.4) {
                                            barColor = 'var(--failed-color)';
                                        } else {
                                            barColor = 'var(--warning-color)';
                                        }
                                        
                                        return `
                                            <div class="breakdown-row">
                                                <div class="breakdown-header">
                                                    <span class="breakdown-name">${escapeHtml(item.criterion)} (Weight: ${item.weight})</span>
                                                    <span class="breakdown-values">${item.points.toFixed(1)} / ${maxPointsFormatted} pts</span>
                                                </div>
                                                <div class="breakdown-bar-bg">
                                                    <div class="breakdown-bar-fill" style="width: ${pct}%; background-color: ${barColor};"></div>
                                                </div>
                                                ${item.detail ? `<div class="breakdown-detail">${escapeHtml(item.detail)}</div>` : ''}
                                            </div>
                                        `;
                                    }).join('')}
                                </div>
                            `;
                        } else {
                            const reasonsHtml = (analysis.reasons || []).map(r => `<li>${escapeHtml(r)}</li>`).join('');
                            breakdownHtml = `
                                <div class="analysis-reasons">
                                    <strong>Evaluation Analysis:</strong>
                                    <ul>
                                        ${reasonsHtml}
                                    </ul>
                                </div>
                            `;
                        }

                        let fitBreakdownHtml = '';
                        if (analysis.fit_breakdown && analysis.fit_breakdown.length > 0) {
                            const totalFitWeight = analysis.fit_breakdown.reduce((sum, item) => sum + item.weight, 0);
                            fitBreakdownHtml = `
                                <div class="breakdown-container">
                                    <strong>Company Fit Breakdown:</strong>
                                    ${analysis.fit_breakdown.map(item => {
                                        const maxPoints = totalFitWeight > 0 ? (100 * item.weight / totalFitWeight) : 0;
                                        const maxPointsFormatted = maxPoints.toFixed(1);
                                        const pct = maxPoints > 0 ? Math.min(100, Math.max(0, (item.points / maxPoints) * 100)) : 0;
                                        
                                        let barColor = 'var(--neutral-color)';
                                        if (item.subscore >= 0.7) {
                                            barColor = 'var(--success-color)';
                                        } else if (item.subscore <= 0.4) {
                                            barColor = 'var(--failed-color)';
                                        } else {
                                            barColor = 'var(--warning-color)';
                                        }
                                        
                                        return `
                                            <div class="breakdown-row">
                                                <div class="breakdown-header">
                                                    <span class="breakdown-name">${escapeHtml(item.criterion)} (Weight: ${item.weight})</span>
                                                    <span class="breakdown-values">${item.points.toFixed(1)} / ${maxPointsFormatted} pts</span>
                                                </div>
                                                <div class="breakdown-bar-bg">
                                                    <div class="breakdown-bar-fill" style="width: ${pct}%; background-color: ${barColor};"></div>
                                                </div>
                                                ${item.detail ? `<div class="breakdown-detail">${escapeHtml(item.detail)}</div>` : ''}
                                            </div>
                                        `;
                                    }).join('')}
                                </div>
                            `;
                        }

                        let breakdownsWrapperHtml = '';
                        if (fitBreakdownHtml) {
                            breakdownsWrapperHtml = `
                                <div class="breakdowns-row">
                                    ${breakdownHtml}
                                    ${fitBreakdownHtml}
                                </div>
                            `;
                        } else {
                            breakdownsWrapperHtml = breakdownHtml;
                        }

                        let eligibilityDetailsHtml = '';
                        if (analysis.eligibility && analysis.eligibility.detail) {
                            // Anything that is not an explicit 'eligible' is a
                            // review, not a pass: this panel used to label every
                            // non-gap verdict "Eligible", including 'unknown'.
                            const presentation = eligibilityPresentation(analysis.eligibility.verdict);
                            const eligibilityClass = presentation.className;
                            const eligibilityColor = presentation.color;
                            const eligibilityVerdictLabel = presentation.label;

                            const flagsHtml = (analysis.eligibility.flags || []).map(f => `<span class="tag tag-keyword" style="font-size: 0.7rem; padding: 0.1rem 0.4rem;">${escapeHtml(f)}</span>`).join(' ');
                            
                            eligibilityDetailsHtml = `
                                <div class="analysis-reasons" style="border-top: 1px solid var(--border-color); padding-top: 0.75rem; margin-top: 0.5rem; width: 100%;">
                                    <strong>Eligibility Verdict:</strong> <span class="${eligibilityClass}" style="font-weight: 700; color: ${eligibilityColor};">${eligibilityVerdictLabel}</span>
                                    <p style="font-size: 0.775rem; color: var(--text-secondary); margin-top: 0.25rem;">${escapeHtml(analysis.eligibility.detail)}</p>
                                    ${flagsHtml ? `<div style="margin-top: 0.35rem; display: flex; gap: 0.35rem; flex-wrap: wrap;">${flagsHtml}</div>` : ''}
                                </div>
                            `;
                        }

                        detailsAccordion = `
                            <details class="tender-analysis-details" ontoggle="hydrateAnalysis(this, '${encodeURIComponent(tender.bid_no)}')">
                                <summary>View Automated RFP Score Details</summary>
                                <div class="analysis-content">
                                    <div class="analysis-grid">
                                        <div><strong>EMD Amount:</strong> ${escapeHtml(analysis.emd_status) || 'N/A'}</div>
                                        <div><strong>Startup Relaxation:</strong> ${escapeHtml(analysis.startup_exemption) || 'No'}</div>
                                        <div><strong>MSE Relaxation:</strong> ${escapeHtml(analysis.mse_exemption) || 'No'}</div>
                                        <div><strong>Pre-Bid Meeting:</strong> ${escapeHtml(prebidVal)}</div>
                                        <div><strong>ePBG Guarantee:</strong> ${escapeHtml(epbgVal)}</div>
                                    </div>
                                    ${breakdownsWrapperHtml}
                                    ${eligibilityDetailsHtml}
                                </div>
                            </details>
                        `;
                    }
                } else {
                    scoreBadge = `<span class="score-badge score-low" style="background: rgba(255,255,255,0.02); color: var(--text-secondary); border-color: var(--border-color);">Score: --/10</span>`;
                }

                const localPath = (tender.local_pdf_path || '').startsWith('tenders/')
                    ? tender.local_pdf_path 
                    : `tenders/downloads/${tender.local_pdf_path}`;
                
                const actionButtons = tender.downloaded
                    ? `<a href="${encodeURI(localPath)}" target="_blank" class="btn btn-primary" style="padding: 0.45rem 1rem; font-size: 0.8rem;">
                            <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                            </svg>
                            View RFP Document
                       </a>`
                    : `<span class="state-message" style="font-size: 0.8rem; font-style: italic;">RFP download not available.</span>`;

                // Keywords and tags
                const keywordsList = (tender.keyword || '').split(',')
                    .map(kw => `<span class="tag tag-keyword">${escapeHtml(kw.trim())}</span>`).join(' ');

                // Phase 2 extra grid fields
                let extraDetailsHtml = '';
                if (analysis && (analysis.est_value_inr !== undefined || analysis.buyer_org || analysis.primary_item || analysis.consignee_state)) {
                    let valStr = 'N/A';
                    if (analysis.est_value_inr) {
                        if (analysis.est_value_inr >= 10000000) {
                            valStr = `₹${(analysis.est_value_inr / 10000000).toFixed(2)} Cr`;
                        } else if (analysis.est_value_inr >= 100000) {
                            valStr = `₹${(analysis.est_value_inr / 100000).toFixed(2)} Lakh`;
                        } else {
                            valStr = `₹${analysis.est_value_inr.toLocaleString('en-IN')}`;
                        }
                    }
                    
                    const buyerStr = [analysis.buyer_org, analysis.buyer_dept].filter(Boolean).join(' | ') || 'N/A';
                    const itemStr = analysis.primary_item ? `${analysis.primary_item}${analysis.item_category ? ` (${analysis.item_category})` : ''}` : 'N/A';
                    const stateStr = analysis.consignee_state || 'N/A';
                    const estBadge = analysis.est_value_estimated
                        ? ` <span class="est-emd-badge" title="Bid value not stated; estimated from EMD (${analysis.est_value_source || 'derived'})">est. from EMD</span>`
                        : '';

                    extraDetailsHtml = `
                        <div class="detail-item">
                            <span class="detail-label">Est. Value</span>
                            <span class="detail-value" style="color: var(--secondary-accent); font-weight: 700;">${valStr}${estBadge}</span>
                        </div>`;
                    extraDetailsHtml += `
                        <div class="detail-item" style="grid-column: span 2;" title="${escapeHtml(buyerStr)}">
                            <span class="detail-label">Buyer Detail</span>
                            <span class="detail-value" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%;">${escapeHtml(buyerStr)}</span>
                        </div>
                        <div class="detail-item">
                            <span class="detail-label">Consignee State</span>
                            <span class="detail-value">${escapeHtml(stateStr)}</span>
                        </div>
                        <div class="detail-item" style="grid-column: span 4;" title="${escapeHtml(itemStr)}">
                            <span class="detail-label">Primary Item / Category</span>
                            <span class="detail-value" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%;">${escapeHtml(itemStr)}</span>
                        </div>
                    `;
                }

                // Source Portal Badge
                const sourceName = tender.source_name || (tender.source_id ? tender.source_id.toUpperCase() : 'GEM');
                const sourceBadge = `<span class="tag tag-portal">🌐 ${escapeHtml(sourceName)}</span>`;

                return `
                    <div class="tender-card">
                        <div class="tender-header">
                            <div class="tender-title-area">
                                <div class="tender-tags">
                                    ${sourceBadge}
                                    <span class="tag tag-id">${escapeHtml(tender.bid_no)}</span>
                                    ${keywordsList}
                                    ${businessLineTag}
                                    ${eligibilityTag}
                                    ${urgencyTag}
                                    ${liveExcelTag}
                                    ${finalizedTag}
                                </div>
                                <h3 class="tender-title">${escapeHtml(tender.title)}</h3>
                            </div>
                            <div class="status-area">
                                <div style="display: flex; gap: 0.35rem; align-items: center;">
                                    ${recommendationChip}
                                    <div class="status-badge ${statusClass}"><span class="status-dot"></span>${statusText}</div>
                                </div>
                                ${scoreBadge}
                                ${confidenceHtml}
                            </div>
                        </div>

                        <div class="tender-details-grid">
                            <div class="detail-item">
                                <span class="detail-label">Quantity</span>
                                <span class="detail-value">${escapeHtml(tender.quantity) || 'N/A'}</span>
                            </div>
                            <div class="detail-item" style="grid-column: span 2;">
                                <span class="detail-label">Department / Organisation</span>
                                <span class="detail-value">${escapeHtml(tender.department) || 'N/A'}</span>
                            </div>
                            <div class="detail-item">
                                <span class="detail-label">End Date (Deadline)</span>
                                <span class="detail-value">${escapeHtml(tender.end_date) || 'N/A'}</span>
                            </div>
                            ${extraDetailsHtml}
                        </div>

                        ${detailsAccordion}

                        <div class="tender-actions">
                            <div style="display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap;">
                                ${actionButtons}
                                <a href="${encodeURI(tender.pdf_url || '')}" target="_blank" class="btn btn-secondary" style="padding: 0.45rem 1rem; font-size: 0.8rem;">
                                    <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                        <path stroke-linecap="round" stroke-linejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                                    </svg>
                                    GeM Link
                                </a>
                                ${reanalyzeBtn}
                                <button class="btn-live-excel ${isInLiveExcel ? 'in-excel' : ''}"
                                        data-bid-no="${encodeURIComponent(tender.bid_no)}"
                                        onclick="toggleLiveExcel('${encodeURIComponent(tender.bid_no)}')"
                                        title="${isInLiveExcel ? 'Click to remove from Live Excel' : 'Move tender to Live Excel summary'}">
                                    ${isInLiveExcel ? '<span>📊 In Live Excel ✓</span>' : '<span>➕ Move to Excel</span>'}
                                </button>
                                <button class="btn-finalize ${isFinalized ? 'is-finalized' : ''}"
                                        data-bid-no="${encodeURIComponent(tender.bid_no)}"
                                        onclick="${isFinalized ? `openFinalizedModalFor('${encodeURIComponent(tender.bid_no)}')` : `openQuickFinalize('${encodeURIComponent(tender.bid_no)}')`}"
                                        title="${isFinalized ? `Finalized as SL. NO #${finalRec.sl_no}. Click to inspect in Master Tracker.` : 'Finalize and save to Google Sheet & Master Excel'}">
                                    ${isFinalized ? `<span>⭐ Finalized (#${finalRec.sl_no})</span>` : `<span>⭐ Finalize</span>`}
                                </button>
                            </div>
                            
                            <div class="override-controls">
                                <button class="btn-override ${statusText === 'Shortlisted' ? 'active-short' : ''}" onclick="overrideStatus('${encodeURIComponent(tender.bid_no)}', 'Shortlisted')" title="Shortlist Tender">
                                    🟢 Shortlist
                                </button>
                                <button class="btn-override ${statusText === 'Pending Review' ? 'active-pending' : ''}" onclick="overrideStatus('${encodeURIComponent(tender.bid_no)}', 'Pending Review')" title="Mark Pending">
                                    🟡 Pending
                                </button>
                                <button class="btn-override ${statusText === 'Rejected' ? 'active-rejected' : ''}" onclick="overrideStatus('${encodeURIComponent(tender.bid_no)}', 'Rejected')" title="Reject Tender">
                                    🔴 Reject
                                </button>
                            </div>
                        </div>
                    </div>
                `;
        }

        // Portals Hub Modal Logic
        let allSourcesList = [];
        let activeSourceCategory = 'all';

        async function openSourcesModal() {
            document.getElementById('sourcesModal').style.display = 'flex';
            await loadSourcesData();
        }

        function closeSourcesModal() {
            document.getElementById('sourcesModal').style.display = 'none';
        }

        async function loadSourcesData() {
            try {
                const res = await fetch('/api/sources');
                if (!res.ok) throw new Error("Failed to fetch sources");
                const data = await res.json();
                allSourcesList = data.sources || [];
                renderSourcesList();
            } catch (err) {
                console.error("Error loading portal sources:", err);
            }
        }

        function filterSourceCategory(cat, btnElem) {
            activeSourceCategory = cat;
            const container = document.getElementById('sourceCategoryTabs');
            if (container) {
                const btns = container.querySelectorAll('button');
                btns.forEach(b => b.classList.remove('active'));
            }
            if (btnElem) btnElem.classList.add('active');
            renderSourcesList();
        }

        function renderSourcesList() {
            const grid = document.getElementById('sourcesGrid');
            if (!grid) return;

            const searchKw = (document.getElementById('sourceSearchInput').value || '').toLowerCase().trim();

            const filtered = allSourcesList.filter(src => {
                const nameMatch = (src.name || '').toLowerCase().includes(searchKw) || (src.id || '').toLowerCase().includes(searchKw) || (src.description || '').toLowerCase().includes(searchKw);
                let catMatch = true;
                if (activeSourceCategory === 'defence') {
                    catMatch = (src.category || '').includes('defence') || (src.id || '').includes('def') || (src.id || '').includes('srijan') || (src.id || '').includes('hal') || (src.id || '').includes('bdl');
                } else if (activeSourceCategory === 'central') {
                    catMatch = (src.category || '').includes('central') || (src.id || '').includes('cppp') || (src.id || '').includes('ntpc') || (src.id || '').includes('gem');
                } else if (activeSourceCategory === 'state') {
                    catMatch = (src.category || '').includes('state') || (src.id || '').includes('state');
                } else if (activeSourceCategory === 'sourcing') {
                    catMatch = (src.category || '').includes('sourcing') || (src.id || '').includes('digikey') || (src.id || '').includes('robu');
                }
                return nameMatch && catMatch;
            });

            if (filtered.length === 0) {
                grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 2rem; color: var(--text-secondary);">No matching portals found.</div>`;
                return;
            }

            grid.innerHTML = filtered.map(src => {
                const isEnabled = src.enabled !== false;
                // A portal only actually fetches when it is enabled AND has an
                // adapter for its engine. `native` = GeM, driven by the main
                // scrape pipeline rather than the multi-source fan-out.
                const badge = (text, colour) =>
                    `<span style="background: ${colour}26; color: ${colour}; border: 1px solid ${colour}4d; font-size: 0.7rem; font-weight: 700; padding: 2px 6px; border-radius: 4px; white-space: nowrap;">${text}</span>`;

                let statusBadge;
                if (!isEnabled) {
                    statusBadge = badge('DISABLED', 'var(--failed-color)');
                } else if (src.native) {
                    statusBadge = badge('NATIVE', 'var(--primary-accent)');
                } else if (src.supported === false) {
                    statusBadge = badge('NO ADAPTER', 'var(--warning-color)');
                } else {
                    statusBadge = badge('ACTIVE', 'var(--success-color)');
                }

                return `
                    <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border-color); border-radius: 10px; padding: 0.85rem; display: flex; flex-direction: column; justify-content: space-between; gap: 0.6rem;">
                        <div>
                            <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 0.5rem; margin-bottom: 0.25rem;">
                                <h4 style="margin: 0; font-size: 0.9rem; font-weight: 600; color: var(--text-primary);">${src.name}</h4>
                                ${statusBadge}
                            </div>
                            <p style="margin: 0 0 0.4rem 0; font-size: 0.75rem; color: var(--text-secondary); line-height: 1.35;">${src.description || 'Monitored e-Procurement Portal'}</p>
                            <div style="display: flex; gap: 0.4rem; align-items: center; flex-wrap: wrap;">
                                <span style="font-size: 0.68rem; font-weight: 600; text-transform: uppercase; background: var(--primary-accent-soft); color: var(--primary-accent); padding: 1px 5px; border-radius: 3px;">${src.category}</span>
                                <span style="font-size: 0.68rem; color: var(--text-secondary);">Engine: <strong>${src.engine}</strong></span>
                            </div>
                            ${(isEnabled && src.supported === false && !src.native)
                                ? `<p style="margin: 0.35rem 0 0 0; font-size: 0.68rem; color: var(--warning-color);">Not scraped: ${src.blocked_reason || `no adapter for the <strong>${src.engine}</strong> engine yet.`}</p>`
                                : ''}
                        </div>

                        <div style="display: flex; justify-content: space-between; align-items: center; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 0.5rem; margin-top: 0.25rem;">
                            <a href="${src.url}" target="_blank" style="font-size: 0.75rem; color: var(--primary-accent); text-decoration: none; display: flex; align-items: center; gap: 0.2rem;">
                                🌐 Open Portal
                            </a>
                            <label style="display: flex; align-items: center; gap: 0.4rem; cursor: pointer; font-size: 0.78rem; color: var(--text-primary); font-weight: 500;">
                                <input type="checkbox" ${isEnabled ? 'checked' : ''} onchange="toggleSourceStatus('${src.id}', this.checked)" style="width: 15px; height: 15px; cursor: pointer;">
                                Enable
                            </label>
                        </div>
                    </div>
                `;
            }).join('');
        }

        async function toggleSourceStatus(sourceId, enabled) {
            try {
                const res = await fetch('/api/sources', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: sourceId, enabled: enabled })
                });
                const data = await res.json();
                if (data.success) {
                    const found = allSourcesList.find(s => s.id === sourceId);
                    if (found) found.enabled = enabled;
                    renderSourcesList();
                } else {
                    alert(data.error || "Failed to update source");
                }
            } catch (err) {
                console.error("Error toggling source:", err);
            }
        }

        /* ====================================================================
           SPLIT-FLAP TENDER BOARD INTRO

           The letters tick through glyphs and settle one column at a time, the
           way a mechanical departure board does. Every cell is rebuilt on each
           call, so the intro can be replayed without a reload.
           ==================================================================== */
        const SPLASH_ROWS = ['GEMSENTRY', 'TENDER INTEL'];
        const FLAP_GLYPHS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
        const FLAP_STAGGER = 40;   // ms between neighbouring cells starting
        const FLAP_TICK = 60;      // ms per glyph swap
        const BOARD_HOLD = 700;    // let the settled board actually be seen

        const splashOverlay = document.getElementById('splashOverlay');
        const splashReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
        let splashTimers = [];

        function clearSplashTimers() {
            splashTimers.forEach(clearTimeout);
            splashTimers = [];
        }

        function buildFlapRow(rowEl, text) {
            rowEl.innerHTML = '';
            return Array.from(text).map(ch => {
                const cell = document.createElement('span');
                cell.className = ch === ' ' ? 'flap space' : 'flap';
                rowEl.appendChild(cell);
                return { cell: cell, target: ch };
            });
        }

        function finishSplash() {
            clearSplashTimers();
            if (splashOverlay) splashOverlay.classList.add('hide');
        }

        function runSplash() {
            if (!splashOverlay) return;
            clearSplashTimers();
            splashOverlay.classList.remove('hide');

            const cells = buildFlapRow(document.getElementById('flapRow1'), SPLASH_ROWS[0])
                .concat(buildFlapRow(document.getElementById('flapRow2'), SPLASH_ROWS[1]));

            if (splashReducedMotion.matches) {
                cells.forEach(c => {
                    if (c.target === ' ') return;
                    c.cell.textContent = c.target;
                    c.cell.classList.add('settled');
                });
                splashTimers.push(setTimeout(finishSplash, 900));
                return;
            }

            let lastSettle = 0;
            cells.forEach((c, i) => {
                if (c.target === ' ') return;
                const start = i * FLAP_STAGGER;
                const settle = start + 320 + Math.floor(Math.random() * 140);
                lastSettle = Math.max(lastSettle, settle);

                for (let t = start; t < settle; t += FLAP_TICK) {
                    splashTimers.push(setTimeout(() => {
                        c.cell.textContent = FLAP_GLYPHS[Math.floor(Math.random() * FLAP_GLYPHS.length)];
                        c.cell.classList.remove('ticking');
                        void c.cell.offsetWidth;
                        c.cell.classList.add('ticking');
                    }, t));
                }

                splashTimers.push(setTimeout(() => {
                    c.cell.textContent = c.target;
                    c.cell.classList.remove('ticking');
                    void c.cell.offsetWidth;
                    c.cell.classList.add('ticking', 'settled');
                }, settle));
            });

            splashTimers.push(setTimeout(finishSplash, lastSettle + BOARD_HOLD));
        }

        // ==========================================================
        // Master Sheet & Google Sheets Integration Logic
        // ==========================================================
        let finalizedData = { total_count: 0, highest_serial_no: 1016, records: [] };
        let finalizedBidsMap = {};
        let activeFinalizedTab = 'study';

        async function loadFinalizedData() {
            try {
                const res = await fetch('/api/finalized');
                const data = await res.json();
                if (data && data.records) {
                    finalizedData = data;
                    finalizedBidsMap = {};
                    data.records.forEach(r => {
                        if (r.bid_no) finalizedBidsMap[r.bid_no] = r;
                    });

                    // Update header badge
                    const badge = document.getElementById('finalizedCountBadge');
                    if (badge) badge.innerText = data.total_count || 0;

                    // Update footer info
                    const footerHighest = document.getElementById('footerHighestSl');
                    if (footerHighest) footerHighest.innerText = '#' + (data.highest_serial_no || '---');
                    const footerTotal = document.getElementById('footerTotalFinalized');
                    if (footerTotal) footerTotal.innerText = data.total_count || 0;

                    // Update tab count badges
                    const counts = data.counts_by_sheet || {};
                    const bStudy = document.getElementById('countBadgeStudy');
                    if (bStudy) bStudy.innerText = counts['UNDER DETAILED STUDY'] || 0;
                    const bMaster = document.getElementById('countBadgeMaster');
                    if (bMaster) bMaster.innerText = counts['MASTER'] || 0;
                    const bPart = document.getElementById('countBadgePart');
                    if (bPart) bPart.innerText = counts['(TENDER DETAILS (PARTICIPATED)'] || 0;

                    // Update external sheet link
                    if (data.spreadsheet_url) {
                        const link = document.getElementById('btnOpenGSheetLink');
                        if (link) link.href = data.spreadsheet_url;
                    }

                    // Re-render table if modal is visible
                    renderFinalizedTable();
                    updateCardFinalizeButtons();
                }
            } catch (e) {
                console.error("Error loading finalized tenders:", e);
            }
        }

        function updateCardFinalizeButtons() {
            document.querySelectorAll('.btn-finalize').forEach(btn => {
                const bidNo = decodeURIComponent(btn.getAttribute('data-bid-no') || '');
                if (!bidNo) return;
                const rec = finalizedBidsMap[bidNo];
                if (rec) {
                    btn.classList.add('is-finalized');
                    btn.innerHTML = `<span>⭐ Finalized (#${rec.sl_no})</span>`;
                    btn.title = `Finalized as SL. NO #${rec.sl_no} in ${rec.target_sheet || 'Master Sheet'}. Click to view.`;
                    btn.onclick = () => openFinalizedModalFor(encodeURIComponent(bidNo));
                } else {
                    btn.classList.remove('is-finalized');
                    btn.innerHTML = `<span>⭐ Finalize</span>`;
                    btn.title = 'Finalize and save to Google Sheet & Master Excel';
                    btn.onclick = () => openQuickFinalize(encodeURIComponent(bidNo));
                }
            });
        }

        function openQuickFinalize(encodedBidNo) {
            const bidNo = decodeURIComponent(encodedBidNo);
            const tender = tenders.find(t => t.bid_no === bidNo) || { bid_no: bidNo, title: 'Unknown Title' };
            
            document.getElementById('finalizeBidNo').value = bidNo;
            document.getElementById('finalizeBidNoDisplay').innerText = bidNo;
            document.getElementById('finalizeTitleDisplay').innerText = tender.title || 'N/A';
            document.getElementById('finalizeOrgDisplay').innerText = tender.department || ((tender.analysis || {}).buyer_org) || 'N/A';
            
            const nextSl = (finalizedData.highest_serial_no || 1016) + 1;
            document.getElementById('finalizeNextSlBadge').innerText = `Next SL. NO: #${nextSl}`;
            
            // Auto-select category
            const analysis = tender.analysis || {};
            const catSelect = document.getElementById('finalizeWorkCategory');
            const bl = ((analysis.business_line || {}).label || '').toUpperCase();
            if (bl.includes('DRONE')) catSelect.value = 'DRONES';
            else if (bl.includes('POWER')) catSelect.value = 'POWER SUPPLY';
            else if (bl.includes('LAB')) catSelect.value = 'LAB';
            else catSelect.value = 'SUPPLY';

            document.getElementById('finalizeApproval').value = 'TO BE SUBMIT';
            document.getElementById('finalizeRemarks').value = analysis.pre_bid_date ? `Pre-bid: ${analysis.pre_bid_date}` : '';

            // Show dynamic webhook status banner
            const syncNotice = document.getElementById('finalizeSyncStatusNotice');
            if (syncNotice) {
                if (finalizedData && finalizedData.has_webhook) {
                    syncNotice.style.display = 'block';
                    syncNotice.style.background = 'rgba(16, 185, 129, 0.12)';
                    syncNotice.style.border = '1px solid rgba(16, 185, 129, 0.35)';
                    syncNotice.style.color = '#10b981';
                    syncNotice.innerHTML = '<span>✓ Google Sheet Webhook is active & will sync in real time.</span>';
                } else {
                    syncNotice.style.display = 'block';
                    syncNotice.style.background = 'rgba(245, 158, 11, 0.12)';
                    syncNotice.style.border = '1px solid rgba(245, 158, 11, 0.35)';
                    syncNotice.style.color = '#f59e0b';
                    syncNotice.innerHTML = '<span>ℹ️ Webhook URL not set in Settings. Tender will be saved to Master Excel (Downloads), and can be synced to Google Sheet once the Apps Script URL is added.</span>';
                }
            }

            document.getElementById('finalizeConfirmModal').style.display = 'flex';
        }

        function closeQuickFinalize() {
            document.getElementById('finalizeConfirmModal').style.display = 'none';
        }

        async function submitFinalize(event) {
            event.preventDefault();
            const bidNo = document.getElementById('finalizeBidNo').value;
            const targetSheet = document.getElementById('finalizeTargetSheet').value;
            const workCat = document.getElementById('finalizeWorkCategory').value;
            const approval = document.getElementById('finalizeApproval').value;
            const oemAuth = document.getElementById('finalizeOemAuth').value;
            const remarks = document.getElementById('finalizeRemarks').value;

            const btn = document.getElementById('btnSubmitFinalize');
            const origHtml = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<span>Saving...</span>';

            try {
                const res = await fetch('/api/finalized/finalize', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        bid_no: bidNo,
                        target_sheet: targetSheet,
                        custom_fields: {
                            work_category: workCat,
                            approval: approval,
                            oem_authorization: oemAuth,
                            remarks: remarks
                        }
                    })
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    closeQuickFinalize();
                    await loadFinalizedData();
                    const gRes = data.google_response || {};
                    if (gRes.status === 'ok') {
                        showFinalizeNotification(`⭐ Tender ${bidNo} finalized as #${data.sl_no}! Synced to Google Sheet & Master Excel.`);
                    } else {
                        showFinalizeNotification(`⭐ Tender ${bidNo} saved as #${data.sl_no} to Master Excel. (Google Sheet sync skipped: add Webhook URL in Settings to sync)`);
                    }
                    filterData();
                } else {
                    alert(data.error || 'Failed to finalize tender.');
                }
            } catch (err) {
                console.error("Error finalizing:", err);
                alert("Failed to finalize tender: " + err);
            } finally {
                btn.disabled = false;
                btn.innerHTML = origHtml;
            }
        }

        function openFinalizedModal(defaultTab = 'study') {
            document.getElementById('finalizedHubModal').style.display = 'flex';
            switchFinalizedTab(defaultTab);
            loadFinalizedData();
            loadFinalizedConfig();
        }

        function openFinalizedModalFor(encodedBidNo) {
            const bidNo = decodeURIComponent(encodedBidNo);
            const rec = finalizedBidsMap[bidNo];
            let tab = 'study';
            if (rec && rec.target_sheet === 'MASTER') tab = 'master';
            if (rec && rec.target_sheet && rec.target_sheet.includes('PARTICIPATED')) tab = 'part';
            openFinalizedModal(tab);
        }

        function closeFinalizedModal() {
            document.getElementById('finalizedHubModal').style.display = 'none';
        }

        function switchFinalizedTab(tab) {
            activeFinalizedTab = tab;
            ['study', 'master', 'part', 'settings'].forEach(t => {
                const pill = document.getElementById(`tabBtn${t.charAt(0).toUpperCase() + t.slice(1)}`);
                if (pill) {
                    if (t === tab) pill.classList.add('active');
                    else pill.classList.remove('active');
                }
            });

            const tableView = document.getElementById('finalizedTableView');
            const settingsView = document.getElementById('finalizedSettingsView');

            if (tab === 'settings') {
                tableView.style.display = 'none';
                settingsView.style.display = 'flex';
            } else {
                tableView.style.display = 'block';
                settingsView.style.display = 'none';
                renderFinalizedTable();
            }
        }

        function renderFinalizedTable() {
            const tbody = document.getElementById('finalizedTableBody');
            if (!tbody) return;

            let filtered = finalizedData.records || [];
            if (activeFinalizedTab === 'study') {
                filtered = filtered.filter(r => (r.target_sheet || 'UNDER DETAILED STUDY') === 'UNDER DETAILED STUDY');
            } else if (activeFinalizedTab === 'master') {
                filtered = filtered.filter(r => r.target_sheet === 'MASTER');
            } else if (activeFinalizedTab === 'part') {
                filtered = filtered.filter(r => (r.target_sheet || '').includes('PARTICIPATED'));
            }

            if (filtered.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="9" style="text-align: center; padding: 2.5rem; color: var(--text-secondary);">
                            <div style="font-size: 2rem; margin-bottom: 0.5rem;">📋</div>
                            <div>No tenders finalized in this sheet yet.</div>
                            <div style="font-size: 0.75rem; margin-top: 0.25rem;">Click <strong>⭐ Finalize</strong> on any tender card to add it here.</div>
                        </td>
                    </tr>
                `;
                return;
            }

            tbody.innerHTML = filtered.map(r => {
                const link = r.drive_link || r.rfp_link || '';
                let linkHtml = '<span style="color: var(--text-muted); font-size: 0.82rem;">None</span>';
                if (link) {
                    const isGdrive = link.includes('drive.google.com');
                    if (isGdrive) {
                        linkHtml = `<a href="${escapeHtml(link)}" target="_blank" class="gdrive-link-badge">📁 Drive RFP ↗</a>`;
                    } else {
                        linkHtml = `<a href="${escapeHtml(link)}" target="_blank" class="gdrive-link-badge" style="background: rgba(14, 124, 107, 0.1); color: var(--secondary-accent); border-color: rgba(14, 124, 107, 0.3);">📄 GeM RFP ↗</a>`;
                    }
                }

                let statusBadge = '';
                if ((r.target_sheet || '').includes('PARTICIPATED')) {
                    const res = r.won_lost_result || 'PARTICIPATING';
                    const isWon = res.toLowerCase().includes('won');
                    statusBadge = `<span class="${isWon ? 'chip-won' : 'chip-lost'}">${escapeHtml(res)}</span>`;
                } else if (r.target_sheet === 'MASTER') {
                    statusBadge = `<span class="chip-study" style="background: rgba(59, 130, 246, 0.12); color: #3b82f6; border-color: rgba(59, 130, 246, 0.3);">Master Sheet</span>`;
                } else {
                    statusBadge = `<span class="chip-study">Detailed Study</span>`;
                }

                const isParticipated = (r.target_sheet || '').includes('PARTICIPATED');
                const encodedBid = encodeURIComponent(r.bid_no);

                return `
                    <tr>
                        <td><span class="badge-sl">#${r.sl_no}</span></td>
                        <td style="font-weight: 700; font-family: var(--font-mono); color: var(--primary-accent); font-size: 0.9rem;">${escapeHtml(r.bid_no)}</td>
                        <td style="max-width: 340px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 0.88rem; font-weight: 500;" title="${escapeHtml(r.title)}">${escapeHtml(r.title)}</td>
                        <td style="max-width: 220px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 0.85rem; color: var(--text-secondary);" title="${escapeHtml(r.organisation)}">${escapeHtml(r.organisation)}</td>
                        <td><span class="tag tag-keyword" style="font-size: 0.78rem; padding: 0.2rem 0.5rem; font-weight: 600;">${escapeHtml(r.work_category || 'SUPPLY')}</span></td>
                        <td style="white-space: nowrap; font-size: 0.85rem; font-weight: 600;">${escapeHtml(r.end_date || 'N/A')}</td>
                        <td>${linkHtml}</td>
                        <td>${statusBadge}</td>
                        <td style="text-align: center; white-space: nowrap;">
                            <div style="display: inline-flex; gap: 0.35rem; align-items: center;">
                                ${!isParticipated ? `
                                    <button class="btn btn-secondary" onclick="openParticipatedModalFor('${encodedBid}')" title="Move to Participated Sheet (Mark Won/Lost)" style="padding: 0.3rem 0.55rem; font-size: 0.75rem; border-color: rgba(16, 185, 129, 0.5); color: #34d399;">
                                        🏆 Participated
                                    </button>
                                ` : ''}
                                <button class="btn btn-secondary" onclick="deleteFinalizedPrompt('${encodedBid}', ${r.sl_no})" title="Delete from Google Sheet & Master Excel" style="padding: 0.3rem 0.55rem; font-size: 0.75rem; border-color: rgba(239, 68, 68, 0.4); color: var(--failed-color);">
                                    🗑️
                                </button>
                            </div>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        async function deleteFinalizedPrompt(encodedBidNo, slNo) {
            const bidNo = decodeURIComponent(encodedBidNo);
            if (!confirm(`Are you sure you want to remove Tender ${bidNo} (SL #${slNo}) from the Google Sheet and Master Excel?\n\nThis will maintain proper indexing and remove any accidental entry.`)) {
                return;
            }

            try {
                const res = await fetch('/api/finalized/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ bid_no: bidNo, sl_no: slNo })
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    await loadFinalizedData();
                    showFinalizeNotification(`🗑️ Tender ${bidNo} removed from Google Sheet and Master Excel.`);
                    filterData();
                } else {
                    alert(data.error || 'Failed to delete tender.');
                }
            } catch (e) {
                console.error("Error deleting tender:", e);
                alert("Delete failed: " + e);
            }
        }

        function openParticipatedModalFor(encodedBidNo) {
            const bidNo = decodeURIComponent(encodedBidNo);
            const rec = finalizedBidsMap[bidNo];
            if (!rec) return;

            document.getElementById('partBidNo').value = bidNo;
            document.getElementById('partBidNoDisplay').innerText = bidNo;
            document.getElementById('partSlBadge').innerText = `SL. NO: #${rec.sl_no}`;
            document.getElementById('partTitleDisplay').innerText = rec.title || 'N/A';
            document.getElementById('partTenderValue').value = rec.est_value_inr || '';
            document.getElementById('partSoLink').value = rec.so_link || rec.rfp_link || '';

            document.getElementById('participatedTransitionModal').style.display = 'flex';
        }

        function closeParticipatedModal() {
            document.getElementById('participatedTransitionModal').style.display = 'none';
        }

        async function submitParticipatedMove(event) {
            event.preventDefault();
            const bidNo = document.getElementById('partBidNo').value;
            const wonLost = document.getElementById('partWonLost').value;
            const subStatus = document.getElementById('partSubmissionStatus').value;
            const val = document.getElementById('partTenderValue').value;
            const soStatus = document.getElementById('partSoStatus').value;
            const soLink = document.getElementById('partSoLink').value;
            const remarks = document.getElementById('partFinalRemarks').value;

            try {
                const res = await fetch('/api/finalized/move-to-participated', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        bid_no: bidNo,
                        won_lost_result: wonLost,
                        submission_status: subStatus,
                        tender_value: val,
                        so_status: soStatus,
                        so_link: soLink,
                        final_remarks: remarks
                    })
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    closeParticipatedModal();
                    await loadFinalizedData();
                    showFinalizeNotification(`🏆 Tender ${bidNo} moved to Participated Sheet with outcome "${wonLost}"!`);
                    switchFinalizedTab('part');
                } else {
                    alert(data.error || 'Failed to update participated status.');
                }
            } catch (e) {
                console.error("Error updating participated:", e);
                alert("Failed to update: " + e);
            }
        }

        async function loadFinalizedConfig() {
            try {
                const res = await fetch('/api/finalized/config');
                const cfg = await res.json();
                if (cfg) {
                    document.getElementById('cfgAppsScriptUrl').value = cfg.apps_script_url || '';
                    document.getElementById('cfgDriveMountPath').value = cfg.google_drive_mount_path || '';
                    document.getElementById('cfgLocalExcelPath').value = cfg.local_master_excel_path || '';
                }
            } catch (e) {
                console.error("Error loading finalized config:", e);
            }
        }

        async function saveMasterConfig() {
            const appsScriptUrl = document.getElementById('cfgAppsScriptUrl').value.trim();
            const driveMount = document.getElementById('cfgDriveMountPath').value.trim();
            const localExcel = document.getElementById('cfgLocalExcelPath').value.trim();

            try {
                const res = await fetch('/api/finalized/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        apps_script_url: appsScriptUrl,
                        google_drive_mount_path: driveMount,
                        local_master_excel_path: localExcel
                    })
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    showFinalizeNotification("⚙️ Google Sheet & Drive settings saved successfully.");
                }
            } catch (e) {
                alert("Failed saving config: " + e);
            }
        }

        async function testCurrentWebhook() {
            const url = document.getElementById('cfgAppsScriptUrl').value.trim();
            const resDiv = document.getElementById('webhookTestResult');
            if (!url) {
                alert("Please enter a Google Apps Script Webhook URL first.");
                return;
            }
            resDiv.style.display = 'block';
            resDiv.style.color = 'var(--text-secondary)';
            resDiv.innerText = 'Testing connection...';

            try {
                const res = await fetch('/api/finalized/test-webhook', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ apps_script_url: url })
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    resDiv.style.color = 'var(--success-color)';
                    resDiv.innerText = '✓ Connected! Google Sheet Webhook responded successfully.';
                } else {
                    resDiv.style.color = 'var(--failed-color)';
                    resDiv.innerText = '✗ Webhook error: ' + (data.message || data.error || 'Unknown error');
                }
            } catch (e) {
                resDiv.style.color = 'var(--failed-color)';
                resDiv.innerText = '✗ Network error: ' + e;
            }
        }

        async function copyAppsScriptCode() {
            try {
                const res = await fetch('/api/finalized/script');
                const data = await res.json();
                if (data.script) {
                    await navigator.clipboard.writeText(data.script);
                    const btnText = document.getElementById('btnCopyScriptText');
                    btnText.innerText = '✓ Copied to Clipboard!';
                    setTimeout(() => { btnText.innerText = '📋 Copy Apps Script Code'; }, 3000);
                }
            } catch (e) {
                alert("Could not copy script: " + e);
            }
        }

        function showFinalizeNotification(msg) {
            const toast = document.createElement('div');
            toast.style.cssText = 'position: fixed; bottom: 2rem; right: 2rem; background: #1f2937; color: #f9fafb; border: 1px solid #f59e0b; padding: 0.85rem 1.25rem; border-radius: 8px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); z-index: 9999; font-size: 0.85rem; font-weight: 600; display: flex; align-items: center; gap: 0.5rem;';
            toast.innerHTML = `<span>${escapeHtml(msg)}</span>`;
            document.body.appendChild(toast);
            setTimeout(() => {
                toast.style.opacity = '0';
                toast.style.transition = 'opacity 0.4s ease';
                setTimeout(() => toast.remove(), 400);
            }, 4000);
        }

        async function syncAllFinalizedToGoogle() {
            const btnHeader = document.getElementById('btnHeaderSyncAll');
            const btnSettings = document.getElementById('btnSyncAllGoogleSettings');
            const origHeaderHtml = btnHeader ? btnHeader.innerHTML : '';
            const origSettingsHtml = btnSettings ? btnSettings.innerHTML : '';

            if (btnHeader) {
                btnHeader.innerHTML = '<span>⏳ Syncing...</span>';
                btnHeader.disabled = true;
            }
            if (btnSettings) {
                btnSettings.innerHTML = '⏳ Syncing...';
                btnSettings.disabled = true;
            }

            try {
                const res = await fetch('/api/finalized/sync-all', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'ok') {
                    showFinalizeNotification(`✓ ${data.message}`);
                    await loadFinalizedData();
                } else {
                    alert(data.message || data.error || 'Failed to sync to Google Sheet.');
                }
            } catch (e) {
                alert("Error syncing to Google Sheet: " + e);
            } finally {
                if (btnHeader) {
                    btnHeader.innerHTML = origHeaderHtml;
                    btnHeader.disabled = false;
                }
                if (btnSettings) {
                    btnSettings.innerHTML = origSettingsHtml;
                    btnSettings.disabled = false;
                }
            }
        }

        if (splashOverlay) {
            splashOverlay.addEventListener('click', finishSplash);
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && !splashOverlay.classList.contains('hide')) finishSplash();
            });
        }
