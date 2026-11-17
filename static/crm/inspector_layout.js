/* ==========================================================================
   GeMSentry CRM Inspector Layout Helpers
   - Collapsible panel deck (Folder & Sync / Response / Details) so the
     document viewer keeps the vertical space by default.
   - Focus mode (hide all inspector chrome) and native fullscreen viewer.
   Toggling panels / focus never re-renders the inspector, so the PDF iframe
   is not reloaded.
   Security: Strictly uses crmEl / textContent.
   ========================================================================== */
(function () {
    'use strict';

    const el = window.crmEl;
    const FOCUS_CLASS = 'is-focus';

    /**
     * Ask the browser PDF viewer to fit the page width to the pane.
     * @param {string|null} url
     * @returns {string|null}
     */
    function withFitWidth(url) {
        if (!url || url.includes('#')) return url;
        return `${url}#view=FitH`;
    }

    /**
     * @typedef {{ id: string, label: string, badge?: string, node: Node }} InspectorPanel
     */

    /**
     * Build a chip bar plus a body holding every panel; only one panel is open at a time.
     * @param {InspectorPanel[]} panels
     * @returns {{ bar: HTMLElement, body: HTMLElement }}
     */
    function buildPanelDeck(panels) {
        const state = window.CRMState;
        const valid = panels.filter((p) => p && p.node);
        const known = new Set(valid.map((p) => p.id));
        if (!known.has(state.inspector.openPanel)) {
            state.inspector.openPanel = null;
        }

        const chips = new Map();
        const sections = new Map();

        const sync = () => {
            const openId = state.inspector.openPanel;
            chips.forEach((chip, id) => {
                const isOpen = id === openId;
                chip.classList.toggle('active', isOpen);
                chip.setAttribute('aria-expanded', String(isOpen));
            });
            sections.forEach((section, id) => {
                section.hidden = id !== openId;
            });
            body.hidden = !openId;
        };

        valid.forEach((panel) => {
            const chip = el('button', {
                type: 'button',
                className: 'crm-panel-chip',
                'aria-controls': `crmPanel_${panel.id}`,
                onClick: () => {
                    state.inspector.openPanel = state.inspector.openPanel === panel.id ? null : panel.id;
                    sync();
                }
            }, [
                el('span', { text: panel.label }),
                panel.badge ? el('span', { className: 'crm-panel-chip-badge', text: panel.badge }) : null,
                el('span', { className: 'crm-panel-chip-caret', 'aria-hidden': 'true', text: '▾' })
            ]);
            chips.set(panel.id, chip);
            sections.set(panel.id, el('div', { id: `crmPanel_${panel.id}`, className: 'crm-panel-section' }, [panel.node]));
        });

        const bar = el('div', { className: 'crm-panel-bar' }, Array.from(chips.values()));
        const body = el('div', { className: 'crm-panel-body' }, Array.from(sections.values()));
        sync();
        return { bar, body };
    }

    /**
     * Apply the remembered focus-mode preference to the inspector pane.
     * @param {HTMLElement} container
     */
    function applyFocus(container) {
        if (!container) return;
        container.classList.toggle(FOCUS_CLASS, Boolean(window.CRMState.inspector.focusMode));
    }

    /**
     * Focus + fullscreen controls for the viewer toolbar.
     * @param {HTMLElement} container inspector pane
     * @param {HTMLElement} viewerArea element to show fullscreen
     * @returns {HTMLElement[]}
     */
    function buildViewerControls(container, viewerArea) {
        const state = window.CRMState;
        const focusLabel = () => (state.inspector.focusMode ? '⤡ Exit Focus' : '⤢ Focus');

        const focusBtn = el('button', {
            type: 'button',
            className: 'crm-viewer-btn',
            title: 'Hide the tender header and panels to give the document the whole pane',
            text: focusLabel(),
            onClick: () => {
                state.inspector.focusMode = !state.inspector.focusMode;
                applyFocus(container);
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

        return [focusBtn, fullscreenBtn];
    }

    /**
     * Single-row viewer toolbar: label on the left, links + controls on the right.
     * @param {string} label
     * @param {Array<Node|null>} actions
     * @returns {HTMLElement}
     */
    function buildViewerToolbar(label, actions) {
        return el('div', { className: 'crm-viewer-toolbar' }, [
            el('span', { className: 'crm-viewer-label', title: label, text: label }),
            el('div', { className: 'crm-viewer-actions' }, actions)
        ]);
    }

    /**
     * @param {string} href
     * @param {string} text
     * @param {{ download?: string, muted?: boolean }} [opts]
     * @returns {HTMLElement}
     */
    function viewerLink(href, text, opts) {
        const o = opts || {};
        const attrs = {
            href,
            className: `crm-viewer-link${o.muted ? ' muted' : ''}`,
            text
        };
        if (o.download) {
            attrs.download = o.download;
        } else {
            attrs.target = '_blank';
            attrs.rel = 'noopener noreferrer';
        }
        return el('a', attrs);
    }

    window.CRMInspectorLayout = {
        withFitWidth,
        buildPanelDeck,
        applyFocus,
        buildViewerControls,
        buildViewerToolbar,
        viewerLink
    };
})();
