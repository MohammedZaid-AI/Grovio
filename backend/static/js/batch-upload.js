/* Bulk invoice upload — self-contained. Talks only to /admin/upload/batch and
   /admin/batch/*. Extracted files reuse the existing pending-confirmation UI. */
(() => {
    'use strict';
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    const input = $('bulk-file-input');
    if (!input) return;  // markup absent -> nothing to wire

    let currentBatch = null;
    let poll = null;

    const toast = (m, t) => (window.showToast ? window.showToast(m, t) : console.log(m));

    $('bulk-browse-btn').addEventListener('click', () => input.click());
    input.addEventListener('change', () => {
        const n = input.files.length;
        $('bulk-file-count').textContent = n ? `${n} file${n > 1 ? 's' : ''} selected` : '';
        $('bulk-upload-btn').disabled = n === 0;
    });

    $('bulk-upload-btn').addEventListener('click', async () => {
        if (!input.files.length) return;
        const fd = new FormData();
        fd.append('doc_type', $('bulk-doc-type').value);
        for (const f of input.files) fd.append('files', f);

        $('bulk-upload-btn').disabled = true;
        try {
            const res = await fetch('/admin/upload/batch', { method: 'POST', body: fd, credentials: 'same-origin' });
            const data = await res.json();
            if (!res.ok || !data.success) { toast(data.message || 'Upload failed.', 'error'); $('bulk-upload-btn').disabled = false; return; }
            currentBatch = data.batch_id;
            input.value = ''; $('bulk-file-count').textContent = '';
            $('bulk-review').style.display = 'block';
            toast(`Uploaded ${data.total} file(s). Extracting…`, 'success');
            startPolling();
        } catch {
            toast('Network error during upload.', 'error');
            $('bulk-upload-btn').disabled = false;
        }
    });

    $('bulk-approve-btn').addEventListener('click', async () => {
        if (!currentBatch) return;
        $('bulk-approve-btn').disabled = true;
        try {
            const res = await fetch(`/admin/batch/${currentBatch}/approve`, { method: 'POST', credentials: 'same-origin' });
            const data = await res.json();
            toast(`Approved ${data.approved}/${data.attempted} invoice(s).`, data.approved ? 'success' : 'warning');
            refresh();
        } catch { toast('Approval failed.', 'error'); }
    });

    window.retryBatchFile = async (fileId) => {
        try {
            const res = await fetch(`/admin/batch/${currentBatch}/retry/${fileId}`, { method: 'POST', credentials: 'same-origin' });
            const data = await res.json();
            toast(data.message || (data.success ? 'Retry started.' : 'Retry failed.'), data.success ? 'success' : 'error');
            if (data.success) startPolling();
        } catch { toast('Retry failed.', 'error'); }
    };

    function startPolling() {
        clearInterval(poll);
        refresh();
        poll = setInterval(refresh, 1500);
    }

    const BADGE = { PENDING: 'badge', PROCESSING: 'badge', EXTRACTED: 'badge-success', FAILED: 'badge-error' };
    const LABEL = { PENDING: '⏳ Queued', PROCESSING: '⚙️ Extracting…', EXTRACTED: '✅ Extracted', FAILED: '❌ Failed' };

    async function refresh() {
        if (!currentBatch) return;
        let data;
        try {
            const res = await fetch(`/admin/batch/${currentBatch}`, { credentials: 'same-origin' });
            if (!res.ok) return;
            data = await res.json();
        } catch { return; }

        const c = data.counts;
        $('bulk-progress').textContent =
            `${c.EXTRACTED} extracted · ${c.FAILED} failed · ${c.PENDING + c.PROCESSING} in progress · ${c.confirmed} approved`;

        $('bulk-review-body').innerHTML = data.files.map(f => {
            const confirmed = f.doc_status === 'CONFIRMED';
            const statusHtml = confirmed
                ? '<span class="badge badge-success">✔ Approved</span>'
                : `<span class="badge ${BADGE[f.status] || 'badge'}">${LABEL[f.status] || f.status}</span>`;
            let action = '—';
            if (f.status === 'FAILED' && f.pending_doc_id === null) {
                action = `<button class="btn btn-secondary btn-small" onclick="retryBatchFile(${f.id})">Retry</button>`;
            }
            const total = (f.total_amount !== null && f.total_amount !== undefined) ? `₹${esc(f.total_amount)}` : '—';
            return `<tr${f.status === 'FAILED' ? ' class="inconsistent-row"' : ''}>
                <td><strong>${esc(f.filename)}</strong>${f.error ? `<br><span style="font-size:var(--fs-eyebrow); color:var(--danger);">${esc(f.error)}</span>` : ''}</td>
                <td>${statusHtml}</td>
                <td>${esc(f.invoice_number) || '—'}</td>
                <td>${total}</td>
                <td style="text-align:center;">${action}</td>
            </tr>`;
        }).join('');

        // Enable bulk approve once there is something extracted-and-unconfirmed.
        const approvable = data.files.some(f => f.status === 'EXTRACTED' && f.doc_status === 'PENDING');
        $('bulk-approve-btn').disabled = !approvable;

        if (data.processing_done) {
            clearInterval(poll);
            poll = null;
            $('bulk-upload-btn').disabled = false;
        }
    }
})();
