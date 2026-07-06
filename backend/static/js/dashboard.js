// Grovio Dashboard Controller

document.addEventListener('DOMContentLoaded', () => {
    // Check elements
    const logoutBtn = document.getElementById('logout-btn');
    const salesBillDropzone = document.getElementById('sales-bill-dropzone');
    const salesBillInput = document.getElementById('sales-bill-input');
    const groceryInvoiceDropzone = document.getElementById('grocery-invoice-dropzone');
    const groceryInvoiceInput = document.getElementById('grocery-invoice-input');
    const pendingContainer = document.getElementById('pending-container');

    // Load initial pending list
    fetchPendingDocs();

    // Setup Logout
    if (logoutBtn) {
        logoutBtn.addEventListener('click', async () => {
            try {
                const response = await fetch('/admin/logout', { method: 'POST' });
                if (response.ok) {
                    showToast('Logged out successfully', 'success');
                    setTimeout(() => {
                        window.location.href = '/admin/login';
                    }, 500);
                }
            } catch (err) {
                showToast('Failed to logout. Please reload.', 'error');
            }
        });
    }

    // Setup Dropzones
    setupDropzone(salesBillDropzone, salesBillInput, '/admin/upload/sales-bill', 'sales-bill-loading');
    setupDropzone(groceryInvoiceDropzone, groceryInvoiceInput, '/admin/upload/grocery-invoice', 'grocery-invoice-loading');

    // -------------------------------------------------------
    // Helper: Toast Notifications
    // -------------------------------------------------------
    function showToast(message, type = 'success') {
        const container = document.getElementById('toast-container');
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `<span>${type === 'success' ? '✅' : type === 'warning' ? '⚠️' : '❌'}</span> <div>${message}</div>`;
        container.appendChild(toast);
        
        // Trigger animation
        setTimeout(() => toast.classList.add('show'), 10);
        
        // Auto remove
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, 5000);
    }

    // -------------------------------------------------------
    // Helper: Drag and Drop Setup
    // -------------------------------------------------------
    function setupDropzone(dropzone, input, endpoint, loaderId) {
        const loader = document.getElementById(loaderId);

        dropzone.addEventListener('click', () => input.click());

        input.addEventListener('change', () => {
            if (input.files.length > 0) {
                uploadFile(input.files[0], endpoint, loader);
            }
        });

        // Drag events
        ['dragenter', 'dragover'].forEach(eventName => {
            dropzone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropzone.style.borderColor = 'var(--accent)';
                dropzone.style.background = 'rgba(59, 130, 246, 0.05)';
            }, false);
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropzone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropzone.style.borderColor = 'var(--glass-border)';
                dropzone.style.background = 'rgba(255, 255, 255, 0.02)';
            }, false);
        });

        dropzone.addEventListener('drop', (e) => {
            const dt = e.dataTransfer;
            const files = dt.files;
            if (files.length > 0) {
                uploadFile(files[0], endpoint, loader);
            }
        });
    }

    // -------------------------------------------------------
    // Helper: Upload File AJAX
    // -------------------------------------------------------
    async function uploadFile(file, endpoint, loader) {
        // Enforce front-end validations (size < 10MB)
        const maxSize = 10 * 1024 * 1024;
        if (file.size > maxSize) {
            showToast('File too large. Maximum size is 10MB.', 'error');
            return;
        }

        const allowedTypes = ['application/pdf', 'image/jpeg', 'image/jpg', 'image/png'];
        if (!allowedTypes.includes(file.type)) {
            showToast('Invalid file type. Only PDF and JPEG/PNG images are allowed.', 'error');
            return;
        }

        const formData = new FormData();
        formData.append('file', file);

        loader.classList.add('active');

        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                body: formData
            });

            const data = await response.json();
            if (response.status === 401) {
                window.location.href = '/admin/login';
                return;
            }

            if (response.ok && data.success) {
                showToast('Document uploaded and parsed successfully!', 'success');
                fetchPendingDocs(); // Refresh pending items
            } else {
                showToast(data.message || 'Failed to parse document.', 'error');
            }
        } catch (err) {
            showToast('Network error during document upload.', 'error');
        } finally {
            loader.classList.remove('active');
        }
    }

    // -------------------------------------------------------
    // Helper: Fetch & Render Pending
    // -------------------------------------------------------
    async function fetchPendingDocs() {
        try {
            const response = await fetch('/admin/pending-documents');
            if (response.status === 401) {
                window.location.href = '/admin/login';
                return;
            }

            const docs = await response.json();
            renderPendingList(docs);
        } catch (err) {
            showToast('Failed to fetch pending documents.', 'error');
        }
    }

    function escapeHtml(text) {
        if (text === null || text === undefined) return '';
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    window.applyCorrection = (docId, itemIdx) => {
        const doc = window.currentPendingDocs.find(d => d.id === docId);
        if (!doc) return;
        const item = doc.payload.items[itemIdx];
        if (!item || !item.suggested_correction) return;
        
        const sug = item.suggested_correction;
        const qtyInput = document.getElementById(`input-qty-${docId}-${itemIdx}`);
        const priceInput = document.getElementById(`input-price-${docId}-${itemIdx}`);
        const totalInput = document.getElementById(`input-total-${docId}-${itemIdx}`);
        
        if (qtyInput && sug.quantity !== null) qtyInput.value = sug.quantity;
        if (priceInput && sug.unit_price !== null) priceInput.value = sug.unit_price;
        if (totalInput && sug.total !== null) totalInput.value = sug.total;
        
        showToast('Correction applied successfully!', 'success');
    };

    window.recalcRowTotal = (docId, itemIdx) => {
        const qtyInput = document.getElementById(`input-qty-${docId}-${itemIdx}`);
        const priceInput = document.getElementById(`input-price-${docId}-${itemIdx}`);
        const totalInput = document.getElementById(`input-total-${docId}-${itemIdx}`);
        
        if (qtyInput && priceInput && totalInput) {
            const qty = parseFloat(qtyInput.value);
            const price = parseFloat(priceInput.value);
            if (!isNaN(qty) && !isNaN(price)) {
                totalInput.value = (qty * price).toFixed(2);
            }
        }
    };

    function renderPendingList(docs) {
        window.currentPendingDocs = docs;
        if (!docs || docs.length === 0) {
            pendingContainer.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">📭</div>
                    <p>No pending documents to confirm.</p>
                </div>
            `;
            return;
        }

        pendingContainer.innerHTML = '';
        docs.forEach(doc => {
            const payload = doc.payload || {};
            const items = payload.items || [];
            const docId = doc.id;
            const docType = doc.doc_type;
            const supplier = payload.supplier || '';
            const invNumber = payload.invoice_number || '';
            const docDate = payload.date || '';
            const totalAmount = payload.total_amount || 0.0;

            const isInvoice = docType === 'SUPPLIER_INVOICE';
            const badgeClass = isInvoice ? 'invoice' : 'bill';
            const badgeLabel = isInvoice ? 'Supplier Invoice' : 'Customer Sales Bill';
            const titleText = isInvoice ? `Invoice from Supplier` : `Sales Bill`;

            // Check for warnings and inconsistencies
            const warnings = [];
            items.forEach((item, idx) => {
                if (item.is_inconsistent) {
                    warnings.push(`Line item #${idx + 1} '${item.product || 'Unknown'}' is inconsistent. Please verify and correct.`);
                }
            });

            // Construct Card
            const card = document.createElement('div');
            card.className = 'pending-card';
            card.id = `doc-card-${docId}`;

            // Header Section with editable input fields
            let headerHtml = `
                <div class="pending-card-header" style="flex-direction: column; align-items: stretch; gap: 0.5rem;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span class="doc-type-badge ${badgeClass}">${badgeLabel}</span>
                        <div class="doc-title">${titleText} (Pending ID: ${docId})</div>
                    </div>
                    
                    <div class="edit-meta-grid">
                        ${isInvoice ? `
                        <div class="meta-field">
                            <label>Supplier</label>
                            <input type="text" class="edit-input" id="edit-supplier-${docId}" value="${escapeHtml(supplier)}">
                        </div>
                        ` : ''}
                        <div class="meta-field">
                            <label>Invoice/Bill Number</label>
                            <input type="text" class="edit-input" id="edit-inv-number-${docId}" value="${escapeHtml(invNumber)}">
                        </div>
                        <div class="meta-field">
                            <label>Date (YYYY-MM-DD)</label>
                            <input type="text" class="edit-input" id="edit-date-${docId}" value="${escapeHtml(docDate)}">
                        </div>
                        <div class="meta-field">
                            <label>Total Amount (₹)</label>
                            <input type="number" step="any" class="edit-input" id="edit-total-amount-${docId}" value="${totalAmount}">
                        </div>
                    </div>
                </div>
            `;

            // Warnings Section
            let warningsHtml = '';
            if (warnings.length > 0) {
                warningsHtml = `
                    <div class="warning-box" style="margin-top: 1rem;">
                        ${warnings.map(w => `<div>⚠️ ${w}</div>`).join('')}
                    </div>
                `;
            }

            // Items Table Section
            let tableHeaders = isInvoice 
                ? `<tr><th>Ingredient Product</th><th>Quantity</th><th>Unit</th><th>Unit Price</th><th>Total</th></tr>`
                : `<tr><th>Dish / Item</th><th>Quantity</th><th>Unit Price</th><th>Total Price</th></tr>`;

            let tableRows = items.map((item, itemIdx) => {
                const trClass = item.is_inconsistent ? 'class="inconsistent-row"' : '';
                const warningIcon = item.is_inconsistent ? '<span class="warning-icon" title="Inconsistent line item">⚠️</span>' : '';
                
                let suggestionHtml = '';
                if (item.is_inconsistent && item.suggested_correction) {
                    const sug = item.suggested_correction;
                    suggestionHtml = `
                        <div class="suggestion-badge">
                            <span>💡 Suggested: Qty ${sug.quantity !== null && sug.quantity !== undefined ? sug.quantity : '-'}, Price ₹${sug.unit_price !== null && sug.unit_price !== undefined ? sug.unit_price : '-'}, Total ₹${sug.total !== null && sug.total !== undefined ? sug.total : '-'}</span>
                            <button type="button" class="btn-apply-suggestion" onclick="applyCorrection(${docId}, ${itemIdx})">Apply</button>
                        </div>
                    `;
                }

                if (isInvoice) {
                    return `
                        <tr ${trClass}>
                            <td>
                                <div style="display: flex; flex-direction: column;">
                                    <div style="display: flex; align-items: center; gap: 0.25rem;">
                                        ${warningIcon}
                                        <input type="text" class="edit-input" id="input-prod-${docId}-${itemIdx}" value="${escapeHtml(item.product)}">
                                    </div>
                                    ${suggestionHtml}
                                </div>
                            </td>
                            <td>
                                <input type="number" step="any" class="edit-input num-input${item.quantity === null || item.quantity === undefined ? ' missing-value' : ''}" id="input-qty-${docId}-${itemIdx}" value="${item.quantity !== null && item.quantity !== undefined ? item.quantity : ''}" placeholder="${item.quantity === null || item.quantity === undefined ? 'Enter qty' : ''}" oninput="recalcRowTotal(${docId}, ${itemIdx}); this.classList.remove('missing-value');">
                            </td>
                            <td>
                                <input type="text" class="edit-input" id="input-unit-${docId}-${itemIdx}" value="${escapeHtml(item.unit)}">
                            </td>
                            <td>
                                <input type="number" step="any" class="edit-input num-input" id="input-price-${docId}-${itemIdx}" value="${item.unit_price !== null ? item.unit_price : ''}" oninput="recalcRowTotal(${docId}, ${itemIdx})">
                            </td>
                            <td>
                                <input type="number" step="any" class="edit-input num-input" id="input-total-${docId}-${itemIdx}" value="${item.total !== null ? item.total : ''}">
                            </td>
                        </tr>
                    `;
                } else {
                    return `
                        <tr ${trClass}>
                            <td>
                                <div style="display: flex; flex-direction: column;">
                                    <div style="display: flex; align-items: center; gap: 0.25rem;">
                                        ${warningIcon}
                                        <input type="text" class="edit-input" id="input-prod-${docId}-${itemIdx}" value="${escapeHtml(item.product)}">
                                    </div>
                                    ${suggestionHtml}
                                </div>
                            </td>
                            <td>
                                <input type="number" step="any" class="edit-input num-input${item.quantity === null || item.quantity === undefined ? ' missing-value' : ''}" id="input-qty-${docId}-${itemIdx}" value="${item.quantity !== null && item.quantity !== undefined ? item.quantity : ''}" placeholder="${item.quantity === null || item.quantity === undefined ? 'Enter qty' : ''}" oninput="recalcRowTotal(${docId}, ${itemIdx}); this.classList.remove('missing-value');">
                            </td>
                            <td>
                                <input type="number" step="any" class="edit-input num-input" id="input-price-${docId}-${itemIdx}" value="${item.unit_price !== null ? item.unit_price : ''}" oninput="recalcRowTotal(${docId}, ${itemIdx})">
                            </td>
                            <td>
                                <input type="number" step="any" class="edit-input num-input" id="input-total-${docId}-${itemIdx}" value="${item.total !== null ? item.total : ''}">
                            </td>
                        </tr>
                    `;
                }
            }).join('');

            let tableHtml = `
                <div class="items-container" style="margin-top: 1rem;">
                    <table class="items-table">
                        <thead>
                            ${tableHeaders}
                        </thead>
                        <tbody>
                            ${tableRows}
                        </tbody>
                    </table>
                </div>
            `;

            // Actions Section
            let actionsHtml = `
                <div class="actions">
                    <button class="btn btn-secondary" onclick="handleDocAction(${docId}, 'reject')">Reject</button>
                    <button class="btn btn-success" onclick="handleDocAction(${docId}, 'confirm')">Confirm</button>
                </div>
            `;

            card.innerHTML = headerHtml + warningsHtml + tableHtml + actionsHtml;
            pendingContainer.appendChild(card);
        });
    }

    // -------------------------------------------------------
    // Action handler (Exposed to window for onclick attributes)
    // -------------------------------------------------------
    window.handleDocAction = async (docId, action) => {
        const card = document.getElementById(`doc-card-${docId}`);
        const doc = window.currentPendingDocs.find(d => d.id === docId);
        if (!doc) return;

        let requestBody = {};
        if (action === 'confirm') {
            const payload = doc.payload;
            const items = payload.items || [];
            
            const updatedItems = items.map((item, itemIdx) => {
                const prodVal = document.getElementById(`input-prod-${docId}-${itemIdx}`)?.value || '';
                const qtyVal = document.getElementById(`input-qty-${docId}-${itemIdx}`)?.value;
                const unitVal = document.getElementById(`input-unit-${docId}-${itemIdx}`)?.value || '';
                const priceVal = document.getElementById(`input-price-${docId}-${itemIdx}`)?.value;
                const totalVal = document.getElementById(`input-total-${docId}-${itemIdx}`)?.value;
                
                return {
                    product: prodVal,
                    quantity: qtyVal !== '' && qtyVal !== undefined ? parseFloat(qtyVal) : null,
                    unit: unitVal,
                    unit_price: priceVal !== '' && priceVal !== undefined ? parseFloat(priceVal) : null,
                    total: totalVal !== '' && totalVal !== undefined ? parseFloat(totalVal) : null
                };
            });

            // Pre-validation: block confirm if any item has null/empty quantity
            const missingQtyItems = [];
            updatedItems.forEach((item, idx) => {
                if (item.quantity === null || isNaN(item.quantity) || item.quantity <= 0) {
                    missingQtyItems.push({ name: item.product || `Item #${idx + 1}`, idx });
                }
            });
            if (missingQtyItems.length > 0) {
                const names = missingQtyItems.map(m => `"${m.name}"`).join(', ');
                showToast(`Please fill in the quantity for ${names} before confirming.`, 'error');
                // Highlight the missing fields
                missingQtyItems.forEach(m => {
                    const input = document.getElementById(`input-qty-${docId}-${m.idx}`);
                    if (input) {
                        input.classList.add('missing-value');
                        input.focus();
                    }
                });
                return;
            }
            
            const supplierInput = document.getElementById(`edit-supplier-${docId}`);
            const invNumberInput = document.getElementById(`edit-inv-number-${docId}`);
            const dateInput = document.getElementById(`edit-date-${docId}`);
            const totalAmountInput = document.getElementById(`edit-total-amount-${docId}`);
            
            const updatedPayload = {
                doc_type: payload.doc_type,
                supplier: supplierInput ? supplierInput.value : (payload.supplier || ''),
                invoice_number: invNumberInput ? invNumberInput.value : (payload.invoice_number || ''),
                date: dateInput ? dateInput.value : (payload.date || ''),
                items: updatedItems,
                total_amount: totalAmountInput ? parseFloat(totalAmountInput.value || 0) : (payload.total_amount || 0.0)
            };
            
            requestBody = { payload: updatedPayload };
        }

        const endpoint = `/admin/${action}/${docId}`;

        if (card) {
            card.style.opacity = '0.5';
            card.style.pointerEvents = 'none';
        }

        try {
            const response = await fetch(endpoint, { 
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: action === 'confirm' ? JSON.stringify(requestBody) : undefined
            });
            const data = await response.json();

            if (response.status === 401) {
                window.location.href = '/admin/login';
                return;
            }

            if (response.ok && data.success) {
                showToast(data.message || 'Action completed successfully.', 'success');
                if (card) {
                    card.style.transform = 'scale(0.9)';
                    card.style.opacity = '0';
                    setTimeout(() => {
                        card.remove();
                        fetchPendingDocs();
                    }, 300);
                } else {
                    fetchPendingDocs();
                }
            } else {
                showToast(data.message || 'Action failed.', 'error');
                if (card) {
                    card.style.opacity = '1';
                    card.style.pointerEvents = 'all';
                }
            }
        } catch (err) {
            showToast('Network error processing confirmation.', 'error');
            if (card) {
                card.style.opacity = '1';
                card.style.pointerEvents = 'all';
            }
        }
    };

    // -------------------------------------------------------
    // Recipe Manager Logic
    // -------------------------------------------------------
    const recipeForm = document.getElementById('recipe-form');
    const recipeDishInput = document.getElementById('recipe-dish-name');
    const addIngRowBtn = document.getElementById('btn-add-ingredient-row');
    const recipeIngredientsRows = document.getElementById('recipe-ingredients-rows');
    const recipeListContainer = document.getElementById('recipe-list-container');

    // Fetch and render initial recipes list
    fetchRecipes();

    // Default: Add one empty row
    if (recipeIngredientsRows) {
        addIngredientRow();
    }

    // Event listener: Add row button
    if (addIngRowBtn) {
        addIngRowBtn.addEventListener('click', () => addIngredientRow());
    }

    function addIngredientRow(name = '', qty = '', unit = '') {
        if (!recipeIngredientsRows) return;
        const row = document.createElement('div');
        row.className = 'recipe-ingredient-row';
        row.innerHTML = `
            <input type="text" class="form-input ing-name-field" placeholder="Ingredient name" value="${escapeHtml(name)}" required style="flex: 2;">
            <input type="number" step="any" class="form-input ing-qty-field" placeholder="Qty" value="${qty}" required style="flex: 1;">
            <input type="text" class="form-input ing-unit-field" placeholder="Unit (e.g. kg)" value="${escapeHtml(unit)}" required style="flex: 1;">
            <button type="button" class="btn-remove-row" title="Remove row">&times;</button>
        `;

        // Event listener: Remove row button
        row.querySelector('.btn-remove-row').addEventListener('click', () => {
            const rows = recipeIngredientsRows.querySelectorAll('.recipe-ingredient-row');
            if (rows.length > 1) {
                row.remove();
            } else {
                showToast('A recipe must have at least one ingredient mapping.', 'warning');
            }
        });

        recipeIngredientsRows.appendChild(row);
    }

    async function fetchRecipes() {
        try {
            const response = await fetch('/admin/recipes');
            if (response.status === 401) return;
            const recipes = await response.json();
            renderRecipesList(recipes);
        } catch (err) {
            console.error('Failed to fetch recipes:', err);
        }
    }

    function renderRecipesList(recipes) {
        if (!recipeListContainer) return;
        
        const keys = Object.keys(recipes);
        if (keys.length === 0) {
            recipeListContainer.innerHTML = `
                <div style="text-align: center; color: var(--text-secondary); padding: 2rem 0; font-size: 0.9rem;">
                    🍳 No recipes configured yet. Fill in the form on the left to add one!
                </div>
            `;
            return;
        }

        recipeListContainer.innerHTML = '';
        keys.forEach(dish => {
            const ingredients = recipes[dish];
            const itemCard = document.createElement('div');
            itemCard.className = 'recipe-item-card';

            const pills = ingredients.map(ing => {
                return `<span class="recipe-ingredient-pill">${escapeHtml(ing.ingredient_name)}: ${ing.quantity_per_unit} ${escapeHtml(ing.unit)}</span>`;
            }).join('');

            itemCard.innerHTML = `
                <div class="recipe-item-header">
                    <span class="recipe-item-name">${escapeHtml(dish)}</span>
                    <div class="recipe-item-actions">
                        <button type="button" class="btn-icon edit" title="Edit recipe">✏️</button>
                        <button type="button" class="btn-icon delete" title="Delete recipe">🗑️</button>
                    </div>
                </div>
                <div class="recipe-ingredient-pill-list">
                    ${pills}
                </div>
            `;

            // Event listener: Edit button
            itemCard.querySelector('.edit').addEventListener('click', () => {
                recipeDishInput.value = dish;
                recipeIngredientsRows.innerHTML = '';
                ingredients.forEach(ing => {
                    addIngredientRow(ing.ingredient_name, ing.quantity_per_unit, ing.unit);
                });
                showToast(`Loaded '${dish}' recipe into editor.`, 'success');
                recipeDishInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
            });

            // Event listener: Delete button
            itemCard.querySelector('.delete').addEventListener('click', async () => {
                if (confirm(`Are you sure you want to delete the recipe mapping for '${dish}'?`)) {
                    try {
                        const response = await fetch('/admin/recipes/delete', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ dish_name: dish })
                        });
                        const resData = await response.json();
                        if (response.ok && resData.success) {
                            showToast(resData.message || 'Recipe deleted successfully.', 'success');
                            // If currently editing this dish, clear the form
                            if (recipeDishInput.value === dish) {
                                recipeForm.reset();
                                recipeIngredientsRows.innerHTML = '';
                                addIngredientRow();
                            }
                            fetchRecipes();
                        } else {
                            showToast(resData.message || 'Failed to delete recipe.', 'error');
                        }
                    } catch (err) {
                        showToast('Network error during deletion.', 'error');
                    }
                }
            });

            recipeListContainer.appendChild(itemCard);
        });
    }

    // Submit Recipe Form
    if (recipeForm) {
        recipeForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const dishName = recipeDishInput.value.trim();
            const rowElements = recipeIngredientsRows.querySelectorAll('.recipe-ingredient-row');
            
            const ingredientsList = [];
            let isValid = true;

            rowElements.forEach((row, idx) => {
                const nameInput = row.querySelector('.ing-name-field');
                const qtyInput = row.querySelector('.ing-qty-field');
                const unitInput = row.querySelector('.ing-unit-field');

                const name = nameInput.value.trim();
                const qty = parseFloat(qtyInput.value);
                const unit = unitInput.value.trim();

                if (!name || isNaN(qty) || qty <= 0 || !unit) {
                    isValid = false;
                    row.style.borderColor = 'var(--danger)';
                    setTimeout(() => row.style.borderColor = 'var(--glass-border)', 2000);
                } else {
                    ingredientsList.push({
                        ingredient_name: name,
                        quantity_per_unit: qty,
                        unit: unit
                    });
                }
            });

            if (!isValid || ingredientsList.length === 0) {
                showToast('Please verify all ingredient fields are complete and quantities are positive.', 'error');
                return;
            }

            try {
                const response = await fetch('/admin/recipes', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        dish_name: dishName,
                        ingredients: ingredientsList
                    })
                });
                const resData = await response.json();
                if (response.ok && resData.success) {
                    showToast(resData.message || 'Recipe saved successfully!', 'success');
                    recipeForm.reset();
                    recipeIngredientsRows.innerHTML = '';
                    addIngredientRow();
                    fetchRecipes();
                } else {
                    showToast(resData.message || 'Failed to save recipe.', 'error');
                }
            } catch (err) {
                showToast('Network error saving recipe.', 'error');
            }
        });
    }
});
