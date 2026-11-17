/* ==========================================================================
   GeMSentry CRM Tender Page
   Selecting a tender in the Discovery Inbox or a folder opens this full-screen
   page: details on the left, the document full-height on the right.
   Back / Prev / Next in the top bar; Esc closes, ← / → browse, and the browser
   Back button closes the page too.
   Security: crmEl / textContent only.
   ========================================================================== */
(function () {
    'use strict';

    const el = window.crmEl;
    const clear = window.crmClear;

    const CRMTenderPage = {
        root: null,
        bodyEl: null,
        counterEl: null,
        prevBtn: null,
        nextBtn: null,
        backLabelEl: null,
        list: [],
        index: -1,
        opts: null,
        isOpen: false,
        historyPushed: false,

        ensureRoot() {
            if (this.root) return;
            this.backLabelEl = el('span', { text: 'Back to list' });
            this.counterEl = el('span', { className: 'crm-page-counter' });
            this.prevBtn = el('button', { type: 'button', className: 'crm-page-nav', title: 'Previous tender (←)', text: '‹ Prev', onClick: () => this.step(-1) });
            this.nextBtn = el('button', { type: 'button', className: 'crm-page-nav', title: 'Next tender (→)', text: 'Next ›', onClick: () => this.step(1) });
            this.bodyEl = el('div', { className: 'crm-page-body' });

            this.root = el('div', { className: 'crm-tender-page', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Tender details', hidden: 'hidden' }, [
                el('div', { className: 'crm-page-bar' }, [
                    el('button', { type: 'button', className: 'crm-page-back', title: 'Back to list (Esc)', onClick: () => this.close() }, [
                        el('span', { 'aria-hidden': 'true', text: '←' }),
                        this.backLabelEl
                    ]),
                    el('div', { className: 'crm-page-navgroup' }, [this.prevBtn, this.counterEl, this.nextBtn]),
                    el('span', { className: 'crm-page-hint', text: '← → browse · Esc back' })
                ]),
                this.bodyEl
            ]);
            document.body.appendChild(this.root);

            document.addEventListener('keydown', (e) => this.handleKeyDown(e));
            window.addEventListener('popstate', () => {
                if (this.isOpen) {
                    this.historyPushed = false;
                    this.close();
                }
            });
        },

        /**
         * @param {{ list: object[], index: number, backLabel?: string,
         *           toInspectOptions: (item: object) => object,
         *           onClose?: (item: object|null) => void }} opts
         */
        open(opts) {
            if (!opts || !Array.isArray(opts.list) || opts.list.length === 0) return;
            this.ensureRoot();
            this.opts = opts;
            this.list = opts.list;
            this.backLabelEl.textContent = opts.backLabel || 'Back to list';
            this.root.hidden = false;
            document.body.classList.add('crm-page-open');
            this.isOpen = true;
            if (!this.historyPushed) {
                history.pushState({ gemTenderPage: true }, '');
                this.historyPushed = true;
            }
            this.show(Math.max(0, Math.min(opts.index, this.list.length - 1)));
        },

        show(index) {
            this.index = index;
            const item = this.list[index];
            this.counterEl.textContent = `${index + 1} of ${this.list.length}`;
            this.prevBtn.disabled = index <= 0;
            this.nextBtn.disabled = index >= this.list.length - 1;
            this.bodyEl.scrollTop = 0;
            window.CRMInspector.inspect(this.bodyEl, this.opts.toInspectOptions(item));
        },

        step(delta) {
            if (!this.isOpen) return;
            const next = this.index + delta;
            if (next < 0 || next >= this.list.length) return;
            this.show(next);
        },

        close() {
            if (!this.isOpen) return;
            const item = this.list[this.index] || null;
            this.isOpen = false;
            this.root.hidden = true;
            document.body.classList.remove('crm-page-open');
            // Invalidate in-flight inspector loads and drop the PDF iframe.
            window.CRMInspector.requestCounter += 1;
            window.CRMInspector.context = null;
            clear(this.bodyEl);
            if (document.fullscreenElement) {
                document.exitFullscreen().catch(() => null);
            }
            if (this.historyPushed) {
                this.historyPushed = false;
                history.back();
            }
            const onClose = this.opts && this.opts.onClose;
            this.opts = null;
            if (typeof onClose === 'function') onClose(item);
        },

        handleKeyDown(e) {
            if (!this.isOpen) return;
            const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : '';
            if (tag === 'input' || tag === 'textarea' || tag === 'select' || (e.target && e.target.isContentEditable)) return;
            if (document.querySelector('.crm-dialog-backdrop')) return;
            if (e.key === 'Escape') {
                e.preventDefault();
                this.close();
            } else if (e.key === 'ArrowLeft') {
                e.preventDefault();
                this.step(-1);
            } else if (e.key === 'ArrowRight') {
                e.preventDefault();
                this.step(1);
            }
        }
    };

    window.CRMTenderPage = CRMTenderPage;
})();
