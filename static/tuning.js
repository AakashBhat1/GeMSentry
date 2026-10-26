// Tuning JSON: export / edit / import every tunable setting as one bundle.
        const TUNING_MAX_KEYWORDS = 1000;

        function toggleTuningMenu(event) {
            if (event) event.stopPropagation();
            const menu = document.getElementById('tuningMenu');
            const open = !menu.classList.contains('open');
            menu.classList.toggle('open', open);
            document.getElementById('tuningToggle').setAttribute('aria-expanded', String(open));
        }

        function closeTuningMenu() {
            document.getElementById('tuningMenu').classList.remove('open');
            document.getElementById('tuningToggle').setAttribute('aria-expanded', 'false');
        }

        document.addEventListener('click', (e) => {
            const wrap = document.querySelector('.tuning-menu-wrap');
            if (wrap && !wrap.contains(e.target)) closeTuningMenu();
        });

        async function fetchTuning(kind) {
            const url = kind === 'template' ? '/api/tuning/template' : '/api/tuning/export';
            const response = await fetch(url);
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
            return data;
        }

        function saveJsonFile(obj, filename) {
            const blob = new Blob([JSON.stringify(obj, null, 2) + '\n'], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        }

        async function downloadTuning(kind) {
            closeTuningMenu();
            try {
                const bundle = await fetchTuning(kind);
                const day = new Date().toISOString().slice(0, 10);
                saveJsonFile(bundle, kind === 'template'
                    ? 'gemsentry_tuning_template.json'
                    : `gemsentry_tuning_${day}.json`);
            } catch (err) {
                console.error('Tuning download failed:', err);
                alert(`Could not download tuning JSON: ${err.message}`);
            }
        }

        // Cut a full bundle down to the one section the dropdown selected.
        // Every fragment is still a valid bundle, so Apply works on any of them.
        function tuningFragment(bundle, section) {
            const head = { gemsentry_tuning: bundle.gemsentry_tuning || 1 };
            const profile = bundle.company_profile || {};
            switch (section) {
                case 'business_lines':
                    return { ...head, company_profile: { business_lines: profile.business_lines || [] } };
                case 'buyer_affinity':
                    return { ...head, company_profile: { buyer_affinity: profile.buyer_affinity || {} } };
                case 'scrape_keywords':
                    return { ...head, scrape_keywords: bundle.scrape_keywords || [] };
                case 'scoring_config':
                    return { ...head, scoring_config: bundle.scoring_config || {} };
                case 'company_profile':
                    return { ...head, company_profile: profile };
                default:
                    return bundle;
            }
        }

        function setTuningEditor(obj) {
            document.getElementById('tuningEditor').value = JSON.stringify(obj, null, 2);
            updateTuningStats();
        }

        async function loadTuningSection() {
            const section = document.getElementById('tuningSection').value;
            try {
                setTuningEditor(tuningFragment(await fetchTuning('current'), section));
                showTuningFeedback('', null);
            } catch (err) {
                console.error('Failed to load tuning section:', err);
                showTuningFeedback(`Could not load current config: ${err.message}`, false);
            }
        }

        async function loadTuningExample() {
            const section = document.getElementById('tuningSection').value;
            try {
                const example = await fetchTuning('template');
                const fragment = tuningFragment(example, section);
                // Keep the field guide next to the example it explains.
                setTuningEditor(section === 'all' ? example : { _readme: example._readme, ...fragment });
                showTuningFeedback('Example loaded. Applying it REPLACES the matching settings, so edit it first.', null);
            } catch (err) {
                console.error('Failed to load tuning example:', err);
                showTuningFeedback(`Could not load example: ${err.message}`, false);
            }
        }

        function openTuningModal() {
            closeTuningMenu();
            document.getElementById('tuningModal').style.display = 'flex';
            loadTuningSection();
        }

        function closeTuningModal() {
            document.getElementById('tuningModal').style.display = 'none';
            showTuningFeedback('', null);
        }

        function parseTuningEditor() {
            const text = document.getElementById('tuningEditor').value.trim();
            if (!text) throw new Error('The editor is empty.');
            try {
                return JSON.parse(text);
            } catch (err) {
                throw new Error(`Not valid JSON: ${err.message}`);
            }
        }

        function updateTuningStats() {
            const stats = document.getElementById('tuningStats');
            let bundle;
            try {
                bundle = JSON.parse(document.getElementById('tuningEditor').value);
            } catch (_) {
                stats.textContent = 'JSON not parseable yet.';
                stats.style.color = 'var(--failed-color)';
                return;
            }
            const parts = [];
            if (Array.isArray(bundle.scrape_keywords)) {
                const n = new Set(bundle.scrape_keywords
                    .filter(k => typeof k === 'string' && k.trim())
                    .map(k => k.trim().toLowerCase())).size;
                parts.push(`Scrape keywords: ${n} / ${TUNING_MAX_KEYWORDS}${n > TUNING_MAX_KEYWORDS ? ' (too many)' : ''}`);
            }
            const profile = bundle.company_profile || {};
            if (Array.isArray(profile.business_lines)) {
                const kws = profile.business_lines.reduce((sum, l) => sum + ((l && l.keywords) || []).length, 0);
                parts.push(`Business lines: ${profile.business_lines.length} (${kws} keywords)`);
            }
            if (profile.buyer_affinity && typeof profile.buyer_affinity === 'object') {
                parts.push(`Buyers: ${Object.keys(profile.buyer_affinity).length}`);
            }
            if (bundle.scoring_config) parts.push('Scoring config included');
            stats.textContent = parts.length ? parts.join(' · ') : 'No tunable section found.';
            stats.style.color = 'var(--text-secondary)';
        }

        function readTuningFile(event) {
            const file = event.target.files && event.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = () => {
                document.getElementById('tuningEditor').value = String(reader.result || '');
                updateTuningStats();
                showTuningFeedback(`Loaded ${file.name}. Press Validate to check it.`, null);
            };
            reader.onerror = () => showTuningFeedback(`Could not read ${file.name}.`, false);
            reader.readAsText(file);
            event.target.value = '';   // allow re-uploading the same file
        }

        function downloadTuningEditor() {
            try {
                const section = document.getElementById('tuningSection').value;
                saveJsonFile(parseTuningEditor(), `gemsentry_tuning_${section}.json`);
            } catch (err) {
                showTuningFeedback(err.message, false);
            }
        }

        function describeTuningSummary(summary) {
            const lines = [];
            const kw = summary.scrape_keywords;
            if (kw) lines.push(`Scrape keywords: ${kw.count} / ${kw.max} (+${kw.added} new, -${kw.removed} removed)`);
            const prof = summary.company_profile;
            if (prof) lines.push(`Company profile: replaces ${prof.replaced_keys.join(', ') || 'nothing'}`
                + ` → ${prof.business_lines} business lines, ${prof.buyer_affinity} buyers`);
            const sc = summary.scoring_config;
            if (sc) lines.push(`Scoring config: replaces ${sc.replaced_keys.join(', ') || 'nothing'}`);
            return lines.join('\n');
        }

        async function submitTuning(dryRun) {
            let bundle;
            try {
                bundle = parseTuningEditor();
            } catch (err) {
                showTuningFeedback(err.message, false);
                return;
            }
            if (!dryRun && !confirm('Apply this tuning JSON to the server config?')) return;
            try {
                const response = await fetch(`/api/tuning/import${dryRun ? '?dry_run=1' : ''}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(bundle)
                });
                const data = await response.json();
                if (!response.ok) {
                    showTuningFeedback(data.error || 'Import failed.', false);
                    return;
                }
                showTuningFeedback(`${data.message}\n${describeTuningSummary(data.summary || {})}`, true);
                if (!dryRun) {
                    await Promise.all([loadScoringConfig(), loadCompanyProfile()]);
                    if (typeof loadKeywordsForModal === 'function') loadKeywordsForModal();
                }
            } catch (err) {
                console.error('Tuning import failed:', err);
                showTuningFeedback('Server communication failed.', false);
            }
        }

        // isSuccess: true = green, false = red, null = neutral note ('' hides).
        function showTuningFeedback(message, isSuccess) {
            const feedback = document.getElementById('tuningFeedback');
            feedback.textContent = message;
            feedback.style.display = message ? 'block' : 'none';
            const palette = isSuccess === true
                ? ['var(--success-bg)', 'var(--success-color)', 'rgba(16, 185, 129, 0.2)']
                : isSuccess === false
                    ? ['var(--failed-bg)', 'var(--failed-color)', 'rgba(239, 68, 68, 0.2)']
                    : ['var(--input-bg)', 'var(--text-secondary)', 'var(--border-color)'];
            feedback.style.backgroundColor = palette[0];
            feedback.style.color = palette[1];
            feedback.style.border = `1px solid ${palette[2]}`;
        }
