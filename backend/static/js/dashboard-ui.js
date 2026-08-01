/* ==========================================================================
   Grovio — presentation-only layer.

   This file NEVER mutates business state. It only reads the existing admin
   endpoints and paints the hero, health ring, suggestions, alerts, activity
   feed, notification badge and the client-side search filter.

   All business logic (uploads, confirmations, inventory CRUD, recipes,
   deductions, modals, toasts) lives in dashboard.js and is untouched.
   ========================================================================== */

(() => {
    'use strict';

    const $ = (id) => document.getElementById(id);

    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));

    const num = (v) => (typeof v === 'number' && isFinite(v) ? v : 0);

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    async function getJSON(url) {
        try {
            const res = await fetch(url, { credentials: 'same-origin' });
            if (!res.ok) return null;          // 401 / 500 -> stay quiet, this is decoration
            return await res.json();
        } catch {
            return null;
        }
    }

    /* ------------------------------------------------------------------
       Numbers that count up — premium, but never at the cost of clarity
       ------------------------------------------------------------------ */
    function countTo(el, target) {
        if (!el) return;
        if (reduceMotion) { el.textContent = String(target); return; }

        const start = performance.now();
        const dur = 650;
        const from = 0;

        const tick = (now) => {
            const t = Math.min((now - start) / dur, 1);
            const eased = 1 - Math.pow(1 - t, 3);              // easeOutCubic
            el.textContent = String(Math.round(from + (target - from) * eased));
            if (t < 1) requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
    }

    /* ------------------------------------------------------------------
       Hero: greeting + date
       ------------------------------------------------------------------ */
    function paintHero() {
        const h = new Date().getHours();
        const greeting = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
        const org = ($('org-name')?.textContent || 'there').trim();

        const gEl = $('hero-greeting');
        if (gEl) gEl.textContent = `${greeting}, ${org}`;

        const dEl = $('hero-date');
        if (dEl) {
            const when = new Date().toLocaleDateString(undefined, {
                weekday: 'long', month: 'long', day: 'numeric'
            });
            dEl.textContent = `${when} · your agent is monitoring stock, invoices and approvals.`;
        }

        const initial = $('org-initial');
        if (initial) initial.textContent = (org[0] || 'R').toUpperCase();
    }

    /* The status reflects a real signal: did the admin API answer? */
    function paintStatus(online) {
        const dot = $('ai-status-dot');
        const txt = $('ai-status-text');
        if (txt) txt.textContent = online ? 'All systems operational' : 'Connection issue';
        if (dot) dot.classList.toggle('bad', !online);
    }

    /* ------------------------------------------------------------------
       Derive everything from the real inventory payload
       ------------------------------------------------------------------ */
    function classify(items) {
        const out = [], low = [], untracked = [];
        let tracked = 0, healthy = 0;

        for (const it of items) {
            const stock = num(it.current_stock);
            const min = it.minimum_stock;
            const hasMin = typeof min === 'number' && min > 0;

            if (stock <= 0) {
                out.push(it);
            } else if (hasMin && stock < min) {
                low.push(it);
            }

            if (!hasMin) {
                untracked.push(it);
                continue;                       // untracked items skew the score -> exclude entirely
            }
            tracked++;
            if (stock > 0 && stock >= min) healthy++;
        }

        const pct = tracked > 0 ? Math.round((healthy / tracked) * 100) : null;
        return { out, low, untracked, tracked, healthy, pct };
    }

    function paintHealth(c) {
        const ring = $('health-ring');
        const pctEl = $('health-pct');
        const note = $('health-note');

        if (c.pct === null) {
            if (pctEl) pctEl.textContent = 'n/a';
            if (note) note.textContent = 'Set a minimum stock on products to enable the health score.';
            return;
        }

        const color = c.pct >= 80 ? 'var(--success)' : c.pct >= 50 ? 'var(--warning)' : 'var(--danger)';
        if (ring) {
            ring.style.setProperty('--ring-color', color);
            // next frame so the conic-gradient sweep is actually visible
            requestAnimationFrame(() => ring.style.setProperty('--pct', String(c.pct)));
        }
        if (pctEl) {
            if (reduceMotion) {
                pctEl.textContent = `${c.pct}%`;
            } else {
                const start = performance.now();
                const tick = (now) => {
                    const t = Math.min((now - start) / 900, 1);
                    const eased = 1 - Math.pow(1 - t, 3);
                    pctEl.textContent = `${Math.round(c.pct * eased)}%`;
                    if (t < 1) requestAnimationFrame(tick);
                };
                requestAnimationFrame(tick);
            }
        }
        if (note) note.textContent = `${c.healthy} of ${c.tracked} tracked products are at or above their minimum.`;

        countTo($('legend-ok'), c.healthy);
        countTo($('legend-low'), c.low.length);
        countTo($('legend-out'), c.out.length);
    }

    function paintStats(items, c, approvals) {
        countTo($('stat-products'), items.length);
        countTo($('stat-low'), c.low.length);
        countTo($('stat-out'), c.out.length);
        countTo($('stat-approvals'), approvals);

        const meta = $('stat-products-meta');
        if (meta) {
            meta.textContent = c.untracked.length
                ? `${c.untracked.length} without a minimum set`
                : 'All products have a minimum set';
        }

        // Command strip
        const setTxt = (id, v) => { const e = $(id); if (e) e.textContent = String(v); };
        setTxt('cmd-products', items.length);
        setTxt('cmd-attention', c.low.length + c.out.length);
        setTxt('cmd-approvals', approvals);
    }

    /* Suggestions are derived, not invented: reorder what is actually short. */
    function paintSuggestions(c) {
        const host = $('rec-list');
        const count = $('rec-count');
        if (!host) return;

        const rows = [
            ...c.out.map(i => ({ i, urgent: true })),
            ...c.low.map(i => ({ i, urgent: false })),
        ].slice(0, 5);

        if (count) count.textContent = String(c.out.length + c.low.length);

        if (!rows.length) {
            host.innerHTML = '<div class="panel-empty">Nothing to reorder — stock levels look healthy.</div>';
            return;
        }

        host.innerHTML = rows.map(({ i, urgent }, idx) => {
            const unit = esc(i.unit || '');
            const stock = num(i.current_stock);
            const min = num(i.minimum_stock);
            const gap = Math.max(min - stock, 0);
            const pct = min > 0 ? Math.min(Math.round((stock / min) * 100), 100) : 0;
            return `
                <div class="rec" style="--i:${idx}">
                    <span class="rec-ico ${urgent ? 'bad' : 'warn'}">${urgent ? '🛑' : '⚠️'}</span>
                    <div class="rec-body">
                        <div class="rec-title">Reorder ${esc(i.product_name)}</div>
                        <div class="rec-meta">${stock.toFixed(1)} ${unit} left${min ? ` · min ${min.toFixed(1)} ${unit}` : ''}${gap ? ` · short ${gap.toFixed(1)} ${unit}` : ''}</div>
                        <div class="meter"><div class="meter-fill ${urgent ? 'bad' : 'warn'}" style="--w:${pct}%; --i:${idx}"></div></div>
                    </div>
                </div>`;
        }).join('');
    }

    function paintAlerts(c, pendingDocs, deductions) {
        const host = $('alert-list');
        const count = $('alert-count');
        if (!host) return;

        const alerts = [];
        for (const i of c.out.slice(0, 4)) {
            alerts.push({ ico: 'bad', icon: '🛑', title: `${i.product_name} is out of stock`, meta: 'Reorder to avoid service impact' });
        }
        if (pendingDocs > 0) {
            alerts.push({ ico: 'warn', icon: '📂', title: `${pendingDocs} document${pendingDocs > 1 ? 's' : ''} awaiting confirmation`, meta: 'Open the Documents tab' });
        }
        if (deductions > 0) {
            alerts.push({ ico: 'warn', icon: '⚖️', title: `${deductions} inventory deduction${deductions > 1 ? 's' : ''} pending`, meta: 'Open the Deductions tab' });
        }
        if (c.untracked.length) {
            alerts.push({ ico: '', icon: '📦', title: `${c.untracked.length} product${c.untracked.length > 1 ? 's' : ''} without a minimum`, meta: 'Excluded from the health score' });
        }

        if (count) count.textContent = String(alerts.length);

        host.innerHTML = alerts.length
            ? alerts.map((a, idx) => `
                <div class="rec" style="--i:${idx}">
                    <span class="rec-ico ${a.ico}">${a.icon}</span>
                    <div class="rec-body">
                        <div class="rec-title">${esc(a.title)}</div>
                        <div class="rec-meta">${esc(a.meta)}</div>
                    </div>
                </div>`).join('')
            : '<div class="panel-empty">No alerts — everything is running smoothly.</div>';
    }

    function paintActivity(logs) {
        const host = $('activity-list');
        if (!host) return;

        if (!logs.length) {
            host.innerHTML = '<div class="panel-empty">No recent inventory changes.</div>';
            return;
        }

        host.innerHTML = logs.slice(0, 6).map((l, idx) => {
            const when = l.created_at ? new Date(String(l.created_at).replace(' ', 'T')) : null;
            const stamp = when && !isNaN(when)
                ? when.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
                : (l.created_at || '');
            const action = String(l.action_type || '').replace(/_/g, ' ').toLowerCase();
            const delta = (l.old_stock !== null && l.old_stock !== undefined)
                ? `${num(l.old_stock).toFixed(1)} → ${num(l.new_stock).toFixed(1)}`
                : `set to ${num(l.new_stock).toFixed(1)}`;
            return `
                <div class="tl-item" style="--i:${idx}">
                    <div class="tl-title">${esc(l.product_name)} · <span style="color:var(--text-2)">${esc(action)}</span></div>
                    <div class="tl-meta">${esc(delta)} · ${esc(l.source || '')} · ${esc(stamp)}</div>
                </div>`;
        }).join('');
    }

    function paintNotifications(n) {
        const dot = $('notif-count');
        if (!dot) return;
        dot.textContent = String(n);
        dot.classList.toggle('show', n > 0);
    }

    /* ------------------------------------------------------------------
       Global search — pure client-side filtering of what's on screen
       ------------------------------------------------------------------ */
    function setupSearch() {
        const input = $('global-search');
        if (!input) return;

        const filter = () => {
            const q = input.value.trim().toLowerCase();
            const match = (el) => !q || el.textContent.toLowerCase().includes(q);

            document.querySelectorAll('#inventory-table-body tr').forEach(tr => {
                if (tr.querySelector('td[colspan]')) return;      // placeholder row
                tr.style.display = match(tr) ? '' : 'none';
            });
            document.querySelectorAll('.recipe-item-card').forEach(el => {
                el.style.display = match(el) ? '' : 'none';
            });
            document.querySelectorAll('.pending-card').forEach(el => {
                el.style.display = match(el) ? '' : 'none';
            });
        };

        input.addEventListener('input', filter);

        // "/" focuses search, Escape clears it.
        document.addEventListener('keydown', (e) => {
            const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '');
            if (e.key === '/' && !typing) {
                e.preventDefault();
                input.focus();
            } else if (e.key === 'Escape' && document.activeElement === input) {
                input.value = '';
                filter();
                input.blur();
            }
        });
    }

    function setupNotifJump() {
        const btn = $('notif-btn');
        if (!btn) return;
        btn.addEventListener('click', () => {
            // Jump to whichever queue actually has work.
            const deductions = Number($('stat-approvals')?.dataset.deductions || 0);
            window.location.hash = deductions > 0 ? '#deductions' : '#documents';
            document.querySelector('.workspace-head')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    }

    /* ------------------------------------------------------------------
       Boot
       ------------------------------------------------------------------ */
    async function refresh() {
        const [invRes, docsRes, dedRes, auditRes] = await Promise.all([
            getJSON('/admin/inventory'),
            getJSON('/admin/pending-documents'),
            getJSON('/admin/inventory-deductions/pending'),
            getJSON('/admin/inventory/audit-log?limit=8'),
        ]);

        paintStatus(invRes !== null);

        const items = Array.isArray(invRes?.data) ? invRes.data : [];
        const docs = Array.isArray(docsRes) ? docsRes : (Array.isArray(docsRes?.data) ? docsRes.data : []);
        const deds = Array.isArray(dedRes) ? dedRes : (Array.isArray(dedRes?.data) ? dedRes.data : []);
        const logs = Array.isArray(auditRes?.data) ? auditRes.data : [];

        const c = classify(items);
        const approvals = docs.length + deds.length;

        const approvalsEl = $('stat-approvals');
        if (approvalsEl) approvalsEl.dataset.deductions = String(deds.length);

        paintStats(items, c, approvals);
        paintHealth(c);
        paintSuggestions(c);
        paintAlerts(c, docs.length, deds.length);
        paintActivity(logs);
        paintNotifications(approvals);
    }

    document.addEventListener('DOMContentLoaded', () => {
        paintHero();
        setupSearch();
        setupNotifJump();
        refresh();
        // Keep the rail honest after the user changes inventory in another tab.
        window.addEventListener('focus', refresh);
        // ...and immediately after any in-app mutation (dashboard.js emits this).
        document.addEventListener('grovio:data-changed', refresh);
    });
})();
