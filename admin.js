
function adminApi(endpoint, options = {}) {
  const token = localStorage.getItem('quant_auth_token');
  if (!token) {
    window.location.href = 'index.html';
    throw new Error('Not authenticated');
  }
  
  const headers = {
    'Authorization': 'Bearer ' + token,
    'Content-Type': 'application/json',
    ...(options.headers || {})
  };
  
  const url = endpoint.startsWith('http') ? endpoint : `${endpoint}`;
  
  return fetch(url, { ...options, headers }).then(async res => {
    if (res.status === 401) {
      localStorage.removeItem('quant_auth_token');
      window.location.href = 'index.html';
      throw new Error('Session expired');
    }
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `API Error: ${res.status}`);
    }
    return res;
  });
}


/**
 * admin.js - Logic for the Admin Setup (Object Manager, Fields, Bulk Import)
 */

let customObjects = [];
let customFields = [];
let selectedObjectId = -1; // -1 means Stock (builtin)
let currentFieldId = null;
let importInterval = null;
let globalCatalog = null;

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initNavigation();
  loadObjects();
  setupEventListeners();
  loadFieldCatalog();
  loadProviders();
  loadFormulas();
});

function initNavigation() {
  document.querySelectorAll('.sidebar .nav-item').forEach(item => {
    item.addEventListener('click', () => {
      document.querySelectorAll('.sidebar .nav-item').forEach(n => n.classList.remove('active'));
      document.querySelectorAll('.page-section').forEach(s => s.classList.remove('active'));
      
      item.classList.add('active');
      const pageId = item.getAttribute('data-page');
      document.getElementById(`page-${pageId}`).classList.add('active');
    });
  });
}

async function loadObjects() {
  try {
    const res = await adminApi("/api/admin/objects");
    const data = await res.json();
    customObjects = data.objects || [];
    renderObjectsList();
    loadFieldsForSelected();
  } catch (err) {
    showToast("Error loading objects: " + err.message, "error");
  }
}

function renderObjectsList() {
  const container = document.getElementById("objectsList");
  let html = `<div class="admin-object-item ${selectedObjectId === -1 ? 'active' : ''}" data-id="-1">
    <i class="fas fa-cubes"></i> Stocks (Standard)
  </div>`;
  
  customObjects.forEach(obj => {
    html += `<div class="admin-object-item ${selectedObjectId === obj.id ? 'active' : ''}" data-id="${obj.id}">
      <i class="fas fa-cube"></i> ${escapeHtml(obj.plural_label)}
    </div>`;
  });
  
  container.innerHTML = html;
  
  // Bind clicks
  container.querySelectorAll('.admin-object-item').forEach(item => {
    item.addEventListener('click', (e) => {
      selectedObjectId = parseInt(item.getAttribute('data-id'), 10);
      renderObjectsList(); // Update active state
      updateDetailPanelHeader();
      
      // Reset to Fields tab
      document.getElementById('pill-fields').click();
      
      loadFieldsForSelected();
    });
  });
}

function updateDetailPanelHeader() {
  const titleSpan = document.getElementById("detailObjectName");
  const iconSpan = document.getElementById("detailObjectIcon");
  
  if (selectedObjectId === -1) {
    titleSpan.textContent = "Stocks (Standard)";
    iconSpan.innerHTML = '<i class="fas fa-cubes"></i>';
  } else {
    const obj = customObjects.find(o => o.id === selectedObjectId);
    if (obj) {
      titleSpan.textContent = obj.plural_label;
      iconSpan.innerHTML = '<i class="fas fa-cube"></i>';
    }
  }
}

async function loadFieldsForSelected() {
  try {
    const tbody = document.getElementById("customFieldsTableBody");
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px;">Loading fields...</td></tr>`;
    
    const res = await adminApi(`/api/admin/fields?object_id=${selectedObjectId}`);
    const data = await res.json();
    customFields = data.fields || [];
    
    if (customFields.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px; color:var(--text-muted);">No custom fields found for this object.</td></tr>`;
      return;
    }
    
    tbody.innerHTML = customFields.map(cf => `
      <tr>
        <td style="font-family:monospace; color:var(--accent-primary);">${escapeHtml(cf.name)}</td>
        <td style="font-weight:600;">${escapeHtml(cf.label)}</td>
        <td><span class="z-badge safe">${escapeHtml(cf.field_type)}</span></td>
        <td style="font-family:monospace; font-size:0.8rem; color:var(--text-muted); max-width:250px; overflow:hidden; text-overflow:ellipsis;">
          ${cf.field_type === 'formula' ? escapeHtml(cf.formula) : (cf.field_type === 'lookup' ? escapeHtml(cf.lookup_object) : '-')}
        </td>
        <td>
          <span class="z-badge ${cf.is_active ? 'safe' : 'distress'}">${cf.is_active ? 'Active' : 'Inactive'}</span>
        </td>
        <td style="text-align:right;">
          ${cf.is_standard ? '<span style="font-size:0.8rem; color:var(--text-muted);"><i class="fas fa-lock"></i> Standard</span>' : `
          <button class="btn btn-secondary btn-sm" onclick="editField(${cf.id})"><i class="fas fa-edit"></i></button>
          <button class="btn btn-secondary btn-sm" onclick="deleteField(${cf.id})" style="color:var(--accent-danger);"><i class="fas fa-trash"></i></button>
          `}
        </td>
      </tr>
    `).join("");
  } catch (err) {
    showToast("Error loading fields: " + err.message, "error");
  }
}

// ── Object Modals ────────────────────────────────────────────────────────────

document.getElementById("openNewObjectBtn").addEventListener("click", () => {
  document.getElementById("objectLabel").value = "";
  document.getElementById("objectPluralLabel").value = "";
  document.getElementById("objectName").value = "";
  document.getElementById("objectDesc").value = "";
  document.getElementById("objectModal").style.display = "flex";
});

document.getElementById("closeObjectModalBtn").addEventListener("click", () => {
  document.getElementById("objectModal").style.display = "none";
});
document.getElementById("cancelObjectBtn").addEventListener("click", () => {
  document.getElementById("objectModal").style.display = "none";
});

document.getElementById("saveObjectBtn").addEventListener("click", async () => {
  const payload = {
    name: document.getElementById("objectName").value.trim().toLowerCase().replace(/\s+/g, '_'),
    label: document.getElementById("objectLabel").value.trim(),
    plural_label: document.getElementById("objectPluralLabel").value.trim(),
    description: document.getElementById("objectDesc").value.trim()
  };
  
  if (!payload.name || !payload.label || !payload.plural_label) {
    showToast("Name, Label, and Plural Label are required.", "error");
    return;
  }
  
  try {
    const res = await adminApi("/api/admin/objects", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to save object");
    
    showToast("Object created successfully", "success");
    document.getElementById("objectModal").style.display = "none";
    loadObjects();
  } catch (err) {
    showToast(err.message, "error");
  }
});


// ── Field Modals ─────────────────────────────────────────────────────────────

document.getElementById("openNewFieldBtn").addEventListener("click", () => {
  currentFieldId = null;
  document.getElementById("formulaModalTitle").textContent = "Create Custom Field";
  document.getElementById("formulaFieldId").value = "";
  document.getElementById("formulaLabel").value = "";
  document.getElementById("formulaName").value = "";
  document.getElementById("formulaType").value = selectedObjectId === -1 ? "formula" : "text";
  document.getElementById("formulaDesc").value = "";
  document.getElementById("formulaEditor").value = "";
  document.getElementById("lookupObjectSelect").value = "Stock";
  
  updateFieldTypeUI();
  document.getElementById("formulaModal").style.display = "flex";
});

document.getElementById("formulaType").addEventListener("change", updateFieldTypeUI);

function updateFieldTypeUI() {
  const type = document.getElementById("formulaType").value;
  const lookupWrapper = document.getElementById("lookupObjectWrapper");
  const formulaPanel = document.getElementById("formulaEditorPanel");
  const nonFormulaPanel = document.getElementById("nonFormulaPanel");
  
  lookupWrapper.style.display = (type === "lookup") ? "block" : "none";
  
  if (type === "formula") {
    formulaPanel.style.display = "flex";
    nonFormulaPanel.style.display = "none";
  } else {
    formulaPanel.style.display = "none";
    nonFormulaPanel.style.display = "flex";
  }
  
  // Populate lookup options if needed
  if (type === "lookup") {
    const sel = document.getElementById("lookupObjectSelect");
    sel.innerHTML = `<option value="Stock">Stock</option>` + 
      customObjects.map(o => `<option value="${o.name}">${escapeHtml(o.label)}</option>`).join("");
  }
}

document.getElementById("closeFormulaModalBtn").addEventListener("click", () => {
  document.getElementById("formulaModal").style.display = "none";
});
document.getElementById("cancelFormulaBtn").addEventListener("click", () => {
  document.getElementById("formulaModal").style.display = "none";
});

document.getElementById("saveFormulaBtn").addEventListener("click", async () => {
  const type = document.getElementById("formulaType").value;
  const payload = {
    object_id: selectedObjectId === -1 ? -1 : selectedObjectId,
    name: document.getElementById("formulaName").value.trim().toLowerCase().replace(/\s+/g, '_'),
    label: document.getElementById("formulaLabel").value.trim(),
    field_type: type,
    description: document.getElementById("formulaDesc").value.trim(),
    formula: type === 'formula' ? document.getElementById("formulaEditor").value.trim() : null,
    lookup_object: type === 'lookup' ? document.getElementById("lookupObjectSelect").value : null
  };
  
  if (!payload.name || !payload.label) {
    showToast("API Name and Label are required.", "error");
    return;
  }
  
  const method = currentFieldId ? "PUT" : "POST";
  const url = currentFieldId ? `/api/admin/fields/${currentFieldId}` : `/api/admin/fields`;
  
  try {
    const res = await adminApi(url, {
      method: method,
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to save field");
    
    showToast("Field saved successfully", "success");
    document.getElementById("formulaModal").style.display = "none";
    loadFieldsForSelected();
  } catch (err) {
    showToast(err.message, "error");
  }
});

window.editField = async function(id) {
  const field = customFields.find(f => f.id === id);
  if (!field) return;
  
  currentFieldId = id;
  document.getElementById("formulaModalTitle").textContent = "Edit Custom Field";
  document.getElementById("formulaFieldId").value = id;
  document.getElementById("formulaLabel").value = field.label;
  document.getElementById("formulaName").value = field.name;
  document.getElementById("formulaType").value = field.field_type;
  document.getElementById("formulaDesc").value = field.description || "";
  
  if (field.field_type === 'formula') {
    document.getElementById("formulaEditor").value = field.formula || "";
  }
  if (field.field_type === 'lookup') {
    setTimeout(() => {
      document.getElementById("lookupObjectSelect").value = field.lookup_object || "Stock";
    }, 50);
  }
  
  updateFieldTypeUI();
  document.getElementById("formulaModal").style.display = "flex";
};

window.deleteField = async function(id) {
  if (!confirm("Are you sure you want to delete this field? Data stored in this field will be lost.")) return;
  
  try {
    const res = await adminApi(`/api/admin/fields/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Failed to delete field");
    showToast("Field deleted", "success");
    loadFieldsForSelected();
  } catch (err) {
    showToast(err.message, "error");
  }
};


// ── Catalog & Testing (For Formulas) ─────────────────────────────────────────

async function loadFieldCatalog() {
  try {
    const res = await adminApi("/api/records/fields/catalog");
    const data = await res.json();
    globalCatalog = data;
    
    renderCatalogChips("catalogCompanyFields", data.company_fields);
    renderCatalogChips("catalogValuationFields", data.valuation_fields);
    renderCatalogChips("catalogFinancialsFields", data.financials_fields);
  } catch (err) {
    console.error("Failed to load field catalog", err);
  }
}

function renderCatalogChips(containerId, fields) {
  const container = document.getElementById(containerId);
  if (!container || !fields) return;
  
  container.innerHTML = fields.map(f => {
    return `<div class="field-chip" onclick="insertFieldToFormula('${f.name}')" title="${f.type}">${f.name}</div>`;
  }).join("");
}

window.insertFieldToFormula = function(fieldName) {
  const editor = document.getElementById("formulaEditor");
  const start = editor.selectionStart;
  const end = editor.selectionEnd;
  const val = editor.value;
  editor.value = val.substring(0, start) + fieldName + val.substring(end);
  editor.focus();
  editor.selectionStart = editor.selectionEnd = start + fieldName.length;
};

document.getElementById("testFormulaBtn").addEventListener("click", async () => {
  const formula = document.getElementById("formulaEditor").value.trim();
  const resDiv = document.getElementById("formulaTestResult");
  
  if (!formula) {
    resDiv.style.display = "block";
    resDiv.style.backgroundColor = "rgba(244, 67, 54, 0.1)";
    resDiv.style.color = "var(--accent-danger)";
    resDiv.textContent = "Error: Formula is empty";
    return;
  }
  
  resDiv.style.display = "block";
  resDiv.style.backgroundColor = "rgba(255, 255, 255, 0.05)";
  resDiv.style.color = "var(--text-primary)";
  resDiv.textContent = "Testing...";
  
  try {
    // We assume testing against a dummy AAPL for syntax check
    const res = await adminApi(`/api/admin/fields/test?formula=${encodeURIComponent(formula)}&ticker=AAPL`, { method: "POST" });
    const data = await res.json();
    
    if (!res.ok) {
      throw new Error(data.detail || "Syntax Error");
    }
    
    if (data.error) {
      resDiv.style.backgroundColor = "rgba(255, 193, 7, 0.1)";
      resDiv.style.color = "var(--accent-warning)";
      resDiv.textContent = `Warning: ${data.error}`;
    } else {
      resDiv.style.backgroundColor = "rgba(76, 175, 80, 0.1)";
      resDiv.style.color = "var(--accent-safe)";
      resDiv.textContent = `Success! Test evaluation returned: ${data.result !== null ? data.result : 'null'}`;
    }
  } catch (err) {
    resDiv.style.backgroundColor = "rgba(244, 67, 54, 0.1)";
    resDiv.style.color = "var(--accent-danger)";
    resDiv.textContent = `Error: ${err.message}`;
  }
});


// ── Bulk Import Logic ────────────────────────────────────────────────────────

document.getElementById('startBulkImportBtn').addEventListener('click', async () => {
  const text = document.getElementById('bulkTickersInput').value;
  const tickers = text.replace(/[\n\s,]+/g, ',').split(',').map(t => t.trim()).filter(t => t);
  if (tickers.length === 0) return showToast("Please enter at least one ticker", "error");
  
  document.getElementById('importProgressCard').style.display = "block";
  document.getElementById('importStatusText').textContent = "Starting import...";
  document.getElementById('importErrorLogContainer').style.display = "none";
  document.getElementById('importErrorLog').innerHTML = "";
  
  try {
    const res = await adminApi("/api/stocks/bulk-import", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ tickers })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Import failed to start");
    
    startImportPolling();
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.querySelectorAll('.index-import-btn').forEach(btn => {
  btn.addEventListener('click', async (e) => {
    const index = e.target.getAttribute('data-index');
    document.getElementById('importProgressCard').style.display = "block";
    document.getElementById('importStatusText').textContent = `Fetching constituents for ${index.toUpperCase()}...`;
    
    try {
      const res = await adminApi(`/api/stocks/import-index?index=${index}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Import failed to start");
      
      if (data.new_to_import === 0) {
        showToast(data.message, "success");
        document.getElementById('importStatusText').textContent = data.message;
        document.getElementById('importProgressFill').style.width = "100%";
      } else {
        startImportPolling();
      }
    } catch (err) {
      showToast(err.message, "error");
    }
  });
});

function startImportPolling() {
  if (importInterval) clearInterval(importInterval);
  importInterval = setInterval(async () => {
    try {
      const res = await adminApi("/api/stocks/import-status");
      const data = await res.json();
      
      const total = data.total || 1;
      const processed = data.imported + data.skipped + data.errors_count;
      const pct = (processed / total) * 100;
      
      document.getElementById('importProgressFill').style.width = `${pct}%`;
      document.getElementById('importImportedCount').textContent = data.imported;
      document.getElementById('importSkippedCount').textContent = data.skipped;
      document.getElementById('importErrorCount').textContent = data.errors_count;
      document.getElementById('importTotalCount').textContent = total;
      
      if (data.errors_count > 0) {
        document.getElementById('importErrorLogContainer').style.display = "block";
        const logHtml = data.errors.map(e => `[ERROR] ${e.ticker}: ${e.error}`).join('<br>');
        document.getElementById('importErrorLog').innerHTML = logHtml;
      }
      
      if (data.done) {
        clearInterval(importInterval);
        document.getElementById('importStatusText').textContent = "Import complete!";
        showToast("Bulk import finished", "success");
      } else {
        document.getElementById('importStatusText').textContent = `Processing (${processed}/${total})...`;
      }
    } catch (err) {
      console.error("Polling error", err);
    }
  }, 1000);
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function escapeHtml(str) {
  if (!str) return "";
  return String(str).replace(/[&<>"'`=\/]/g, function (s) {
    return ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
      "/": "&#x2F;",
      "`": "&#x60;",
      "=": "&#x3D;"
    })[s];
  });
}

function showToast(message, type = "success") {
  const container = document.getElementById("toastContainer");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerHTML = `<i class="fas fa-${type === 'success' ? 'check-circle' : 'exclamation-circle'}"></i> ${message}`;
  
  container.appendChild(toast);
  
  setTimeout(() => {
    toast.style.animation = "slideOut 0.3s ease-out forwards";
    setTimeout(() => { toast.remove(); }, 300);
  }, 3000);
}

function setupEventListeners() {
  document.getElementById('formulaLabel').addEventListener('input', (e) => {
    if (!currentFieldId) {
      let val = e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_');
      document.getElementById('formulaName').value = val;
    }
  });

  // ── Layout Tabs ──
  document.getElementById('pill-fields').addEventListener('click', () => {
    document.getElementById('pill-fields').classList.add('active');
    document.getElementById('pill-fields').style.background = 'var(--primary)';
    document.getElementById('pill-fields').style.color = 'white';
    document.getElementById('pill-fields').style.border = 'none';

    document.getElementById('pill-layouts').classList.remove('active');
    document.getElementById('pill-layouts').style.background = 'transparent';
    document.getElementById('pill-layouts').style.color = 'var(--text)';
    document.getElementById('pill-layouts').style.border = '1px solid var(--border)';

    document.getElementById('view-fields').style.display = 'block';
    document.getElementById('view-layouts').style.display = 'none';
  });

  document.getElementById('pill-layouts').addEventListener('click', () => {
    document.getElementById('pill-layouts').classList.add('active');
    document.getElementById('pill-layouts').style.background = 'var(--primary)';
    document.getElementById('pill-layouts').style.color = 'white';
    document.getElementById('pill-layouts').style.border = 'none';

    document.getElementById('pill-fields').classList.remove('active');
    document.getElementById('pill-fields').style.background = 'transparent';
    document.getElementById('pill-fields').style.color = 'var(--text)';
    document.getElementById('pill-fields').style.border = '1px solid var(--border)';

    document.getElementById('view-fields').style.display = 'none';
    document.getElementById('view-layouts').style.display = 'block';
    
    loadLayoutsForSelected();
  });
  
  // Layout Builder Modals
  document.getElementById('openNewLayoutBtn').addEventListener('click', openLayoutBuilder);
  document.getElementById('closeLayoutEditorBtn').addEventListener('click', closeLayoutBuilder);
  document.getElementById('cancelLayoutEditorBtn').addEventListener('click', closeLayoutBuilder);
  document.getElementById('addLayoutSectionBtn').addEventListener('click', addLayoutSection);
  document.getElementById('saveLayoutBtn').addEventListener('click', saveLayout);
}

// ── Page Layouts Logic ──

let currentLayouts = [];
let editingLayoutId = null;
let layoutSections = []; // array of { id, columns: 1 or 2, items: [] }

async function loadLayoutsForSelected() {
  try {
    const container = document.getElementById("layoutsContainer");
    container.innerHTML = `<div style="text-align:center; padding:20px;">Loading layouts...</div>`;
    
    const res = await adminApi(`/api/admin/layouts?object_id=${selectedObjectId}`);
    const data = await res.json();
    currentLayouts = data.layouts || [];
    
    if (currentLayouts.length === 0) {
      container.innerHTML = `<div style="text-align:center; padding:20px; color:var(--text-muted);">No layouts configured. System will use default grid.</div>`;
      return;
    }
    
    container.innerHTML = currentLayouts.map(l => `
      <div class="stat-card" style="flex-direction:row; justify-content:space-between; align-items:center; padding:16px;">
        <div>
          <div style="font-weight:600;">${escapeHtml(l.name)}</div>
          <div style="font-size:0.8rem; color:var(--text-muted); margin-top:4px;">
            ${l.is_active ? '<span class="z-badge safe">Active</span>' : '<span class="z-badge distress">Inactive</span>'}
          </div>
        </div>
        <div>
          <button class="btn btn-secondary btn-sm" onclick="editLayout(${l.id})"><i class="fas fa-edit"></i> Edit</button>
          <button class="btn btn-secondary btn-sm" onclick="deleteLayout(${l.id})" style="color:var(--accent-danger);"><i class="fas fa-trash"></i></button>
        </div>
      </div>
    `).join("");
  } catch (err) {
    showToast("Error loading layouts: " + err.message, "error");
  }
}

function openLayoutBuilder() {
  editingLayoutId = null;
  document.getElementById("layoutNameInput").value = "New Layout";
  layoutSections = [{ id: Date.now(), columns: 2, items: [] }]; // default one section
  
  populateLayoutPalette();
  renderLayoutCanvas();
  document.getElementById("layoutEditorModal").style.display = "flex";
}

window.editLayout = function(id) {
  const layout = currentLayouts.find(l => l.id === id);
  if (!layout) return;
  editingLayoutId = id;
  document.getElementById("layoutNameInput").value = layout.name;
  layoutSections = layout.layout_data.sections || [];
  
  populateLayoutPalette();
  renderLayoutCanvas();
  document.getElementById("layoutEditorModal").style.display = "flex";
};

window.deleteLayout = async function(id) {
  if (!confirm("Delete this layout?")) return;
  try {
    await adminApi(`/api/admin/layouts/${id}`, { method: "DELETE" });
    showToast("Layout deleted", "success");
    loadLayoutsForSelected();
  } catch (err) {
    showToast(err.message, "error");
  }
};

function closeLayoutBuilder() {
  document.getElementById("layoutEditorModal").style.display = "none";
}

function populateLayoutPalette() {
  const palette = document.getElementById("layoutFieldPalette");
  
  // Create a list of available fields. For Stock (-1) we have some built-ins too.
  let fields = [...customFields];
  if (selectedObjectId === -1) {
    fields.unshift({name: "ticker", label: "Ticker", field_type: "text"});
    fields.unshift({name: "name", label: "Company Name", field_type: "text"});
    fields.unshift({name: "price", label: "Price", field_type: "currency"});
    fields.unshift({name: "market_cap", label: "Market Cap", field_type: "currency"});
  }
  
  palette.innerHTML = fields.map(f => `
    <div class="palette-item" draggable="true" ondragstart="layoutDragStart(event, 'field', '${f.name}')" 
         style="background:var(--bg-body); padding:8px 12px; border-radius:4px; border:1px dashed var(--border); font-size:0.85rem; cursor:grab;">
      <i class="fas fa-align-left" style="color:var(--text-muted); margin-right:6px;"></i> ${escapeHtml(f.label)} <small style="color:var(--text-muted);">(${f.name})</small>
    </div>
  `).join("");
}

function addLayoutSection() {
  layoutSections.push({ id: Date.now(), columns: 2, items: [] });
  renderLayoutCanvas();
}

window.removeLayoutSection = function(id) {
  layoutSections = layoutSections.filter(s => s.id !== id);
  renderLayoutCanvas();
};

window.removeLayoutItem = function(sectionId, index) {
  const sec = layoutSections.find(s => s.id === sectionId);
  if (sec) {
    sec.items.splice(index, 1);
    renderLayoutCanvas();
  }
};

function renderLayoutCanvas() {
  const canvas = document.getElementById("layoutCanvas");
  canvas.innerHTML = layoutSections.map(sec => `
    <div class="layout-section" style="border:1px solid var(--border); border-radius:6px; background:var(--bg-card); padding:16px;">
      <div style="display:flex; justify-content:space-between; margin-bottom:12px;">
        <h5 style="margin:0; color:var(--text-muted);">Section (${sec.columns} Columns)</h5>
        <div>
          <button class="btn btn-secondary btn-sm" onclick="removeLayoutSection(${sec.id})" style="padding:2px 6px; font-size:0.7rem; color:var(--accent-danger);"><i class="fas fa-trash"></i></button>
        </div>
      </div>
      
      <div class="layout-dropzone" ondragover="layoutDragOver(event)" ondrop="layoutDrop(event, ${sec.id})"
           style="min-height:60px; border:1px dashed var(--border); border-radius:4px; padding:8px; display:grid; grid-template-columns: repeat(${sec.columns}, 1fr); gap:8px;">
        ${sec.items.length === 0 ? '<div style="grid-column: 1 / -1; text-align:center; color:var(--text-muted); font-size:0.8rem; padding:16px;">Drag fields here...</div>' : ''}
        ${sec.items.map((item, idx) => `
          <div style="background:var(--bg-body); padding:8px; border-radius:4px; border:1px solid var(--border); display:flex; justify-content:space-between; align-items:center;">
             <span style="font-size:0.85rem;">${escapeHtml(item.name)}</span>
             <i class="fas fa-times" style="cursor:pointer; color:var(--accent-danger);" onclick="removeLayoutItem(${sec.id}, ${idx})"></i>
          </div>
        `).join("")}
      </div>
    </div>
  `).join("");
}

// Drag and Drop State
let dragPayload = null;
window.layoutDragStart = function(e, type, name) {
  dragPayload = { type, name };
};
window.layoutDragOver = function(e) {
  e.preventDefault(); // allow drop
};
window.layoutDrop = function(e, sectionId) {
  e.preventDefault();
  if (!dragPayload) return;
  const sec = layoutSections.find(s => s.id === sectionId);
  if (sec) {
    // Only add if not already in this section
    if (!sec.items.find(i => i.name === dragPayload.name)) {
       sec.items.push({ type: dragPayload.type, name: dragPayload.name });
       renderLayoutCanvas();
    }
  }
  dragPayload = null;
};

async function saveLayout() {
  const name = document.getElementById("layoutNameInput").value.trim();
  if (!name) return showToast("Layout name is required", "error");
  
  const payload = {
    object_id: selectedObjectId,
    name: name,
    layout_data: { sections: layoutSections },
    is_active: true
  };
  
  const url = editingLayoutId ? `/api/admin/layouts/${editingLayoutId}` : `/api/admin/layouts`;
  const method = editingLayoutId ? "PUT" : "POST";
  
  try {
    const res = await adminApi(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error("Failed to save layout");
    showToast("Layout saved", "success");
    closeLayoutBuilder();
    loadLayoutsForSelected();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// DATA PROVIDERS LOGIC
// ══════════════════════════════════════════════════════════════════════════════

const $ = id => document.getElementById(id);

const els_prov = {
  list: $('providersList'),
  newBtn: $('openNewProviderBtn'),
  modal: $('providerModal'),
  closeBtn: $('closeProviderModalBtn'),
  saveBtn: $('saveProviderBtn'),
  id: $('providerId'),
  name: $('providerName'),
  type: $('providerType'),
  baseUrl: $('providerBaseUrl'),
  apiKey: $('providerApiKey')
};

async function loadProviders() {
  try {
    const data = await adminApi('/api/admin/providers');
    const providers = await data.json();
    renderProviders(providers);
  } catch (e) {
    showToast('Failed to load providers: ' + e.message, 'error');
  }
}

function renderProviders(providers) {
  if (!providers || providers.length === 0) {
    els_prov.list.innerHTML = '<div style="color:var(--text-muted); text-align:center; padding: 24px;">No providers configured.</div>';
    return;
  }
  
  els_prov.list.innerHTML = providers.map(p => `
    <div class="stat-card provider-card" style="flex-direction:row; justify-content:space-between; align-items:center; padding:16px; margin-bottom:12px; border: 1px solid ${p.is_active ? 'var(--accent-primary)' : 'var(--glass-border)'}; background: ${p.is_active ? 'rgba(124, 58, 237, 0.1)' : 'var(--glass-bg)'};">
      <div>
        <div style="font-weight:600; color:var(--text-primary); display:flex; align-items:center; gap:8px;">
          ${p.name}
          ${p.is_active ? '<span style="font-size:0.7rem; background:var(--accent-primary); color:white; padding:2px 8px; border-radius:12px;">ACTIVE</span>' : ''}
          ${p.is_custom ? '<span style="font-size:0.7rem; background:var(--glass-border); color:var(--text-muted); padding:2px 8px; border-radius:12px;">CUSTOM</span>' : ''}
        </div>
        <div style="font-size:0.8rem; color:var(--text-muted); margin-top:4px;">Type: ${p.provider_type} ${p.base_url ? '| URL: ' + p.base_url : ''}</div>
      </div>
      <div style="display:flex; gap:8px;">
        ${!p.is_active ? `<button class="btn btn-primary btn-sm" onclick="activateProvider(${p.id})">Set Active</button>` : ''}
        <button class="btn btn-secondary btn-sm" onclick="editProvider(${p.id}, '${p.name}', '${p.provider_type}', '${p.base_url || ''}', '${p.api_key || ''}', ${p.is_custom})"><i class="fas fa-cog"></i> Config</button>
      </div>
    </div>
  `).join('');
}

window.activateProvider = async function(id) {
  try {
    const res = await adminApi(`/api/admin/providers/${id}/activate`, { method: 'PUT' });
    if (res.ok) {
      showToast('Provider set to active successfully!', 'success');
      loadProviders();
    }
  } catch (e) {
    showToast('Failed to activate: ' + e.message, 'error');
  }
};

window.editProvider = function(id, name, type, baseUrl, apiKey, isCustom) {
  els_prov.id.value = id;
  els_prov.name.value = name;
  els_prov.type.value = type;
  els_prov.baseUrl.value = baseUrl;
  els_prov.apiKey.value = apiKey;
  
  els_prov.name.disabled = !isCustom;
  els_prov.type.disabled = !isCustom;
  
  els_prov.modal.style.display = 'flex';
};

els_prov.newBtn?.addEventListener('click', () => {
  els_prov.id.value = '';
  els_prov.name.value = '';
  els_prov.type.value = 'custom';
  els_prov.baseUrl.value = '';
  els_prov.apiKey.value = '';
  
  els_prov.name.disabled = false;
  els_prov.type.disabled = false;
  
  els_prov.modal.style.display = 'flex';
});

els_prov.closeBtn?.addEventListener('click', () => {
  els_prov.modal.style.display = 'none';
});

els_prov.saveBtn?.addEventListener('click', async () => {
  const id = els_prov.id.value;
  const payload = {
    name: els_prov.name.value,
    provider_type: els_prov.type.value,
    base_url: els_prov.baseUrl.value,
    api_key: els_prov.apiKey.value,
    is_custom: els_prov.type.value === 'custom'
  };
  
  if (!payload.name) return showToast('Name is required', 'error');
  
  try {
    if (id) {
      // Update existing (only api key and base url are allowed to update for now)
      await adminApi(`/api/admin/providers/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: payload.api_key, base_url: payload.base_url })
      });
      showToast('Provider configured successfully', 'success');
    } else {
      // Create new
      await adminApi('/api/admin/providers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      showToast('Custom provider added', 'success');
    }
    els_prov.modal.style.display = 'none';
    loadProviders();
  } catch (e) {
    showToast(e.message, 'error');
  }
});

// Hook into existing init
const originalInit = window.onload;
window.onload = function() {
  if (originalInit) originalInit();
  
  // Navigation handling for Data Sources tab
  const navSources = document.getElementById('nav-datasources');
  if (navSources) {
    navSources.addEventListener('click', () => {
      document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
      navSources.classList.add('active');
      
      document.querySelectorAll('.page-section').forEach(el => el.classList.remove('active'));
      document.getElementById('page-datasources').classList.add('active');
      
      
      loadProviders();
    });
  }
  
  // Navigation handling for Formulas tab
  const navFormulas = document.getElementById('nav-formulas');
  if (navFormulas) {
    navFormulas.addEventListener('click', () => {
      document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
      navFormulas.classList.add('active');
      
      document.querySelectorAll('.page-section').forEach(el => el.classList.remove('active'));
      document.getElementById('page-formulas').classList.add('active');
      
      loadFormulas();
    });
  }
};

// ══════════════════════════════════════════════════════════════════════════════
// THEME HANDLING
// ══════════════════════════════════════════════════════════════════════════════
function initTheme() {
  const savedTheme = localStorage.getItem("app_theme") || "dark";
  document.body.setAttribute("data-theme", savedTheme);
  
  const cards = document.querySelectorAll('.theme-card');
  cards.forEach(card => {
    if (card.dataset.themeValue === savedTheme) {
      card.style.borderColor = 'var(--accent-primary)';
      card.style.boxShadow = 'var(--glow-primary)';
    } else {
      card.style.borderColor = 'var(--border)';
      card.style.boxShadow = 'none';
    }

    card.addEventListener('click', () => {
      const newTheme = card.dataset.themeValue;
      document.body.setAttribute("data-theme", newTheme);
      localStorage.setItem("app_theme", newTheme);
      
      // Update visual selection
      cards.forEach(c => {
        c.style.borderColor = 'var(--border)';
        c.style.boxShadow = 'none';
      });
      card.style.borderColor = 'var(--accent-primary)';
      card.style.boxShadow = 'var(--glow-primary)';
    });
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// NAVIGATION
// ══════════════════════════════════════════════════════════════════════════════

// ══════════════════════════════════════════════════════════════════════════════
// MATH FORMULAS
// ══════════════════════════════════════════════════════════════════════════════

async function loadFormulas() {
  try {
    const res = await adminApi("/api/admin/formulas");
    if (!res.ok) throw new Error("Failed to load formulas");
    const formulas = await res.json();
    renderFormulas(formulas);
  } catch (err) {
    console.error(err);
    document.getElementById("formulas-container").innerHTML = `<div class="text-center text-danger">Error loading formulas</div>`;
  }
}

function renderFormulas(formulas) {
  const container = document.getElementById("formulas-container");
  if (!formulas || formulas.length === 0) {
    container.innerHTML = `<div class="text-center">No formulas found.</div>`;
    return;
  }
  
  let html = "";
  formulas.forEach(f => {
    html += `
      <div class="card object-card" style="margin-bottom: 1.5rem;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div>
            <h4 style="margin:0; color:var(--primary);">${escapeHtml(f.name)}</h4>
            <div style="font-size:0.85rem; color:var(--text-muted); margin-top:0.25rem;">${escapeHtml(f.description)}</div>
          </div>
          <button class="btn btn-primary btn-sm" onclick="saveFormula('${escapeHtml(f.key)}')">
            <span class="material-symbols-outlined" style="font-size:1.1rem;">save</span> Save
          </button>
        </div>
        
        <div style="margin-top: 1rem;">
          <label style="font-size: 0.8rem; font-weight: 600; color: var(--text-muted);">Available Variables:</label>
          <div style="margin-bottom: 0.5rem;">
            ${f.available_variables.map(v => `<span class="chip" style="background: rgba(33,150,243,0.1); color: var(--primary); font-family: monospace; font-size: 0.75rem;">${escapeHtml(v)}</span>`).join("")}
          </div>
          <textarea id="formula-input-${escapeHtml(f.key)}" class="input-field" style="font-family: monospace; font-size: 1rem; width: 100%; min-height: 80px;" rows="3">${escapeHtml(f.formula_string)}</textarea>
        </div>
      </div>
    `;
  });
  container.innerHTML = html;
}

async function saveFormula(key) {
  const input = document.getElementById(`formula-input-${key}`);
  const formula_string = input.value;
  
  try {
    const res = await adminApi(`/api/admin/formulas/${key}`, {
      method: "POST",
      body: JSON.stringify({ formula_string })
    });
    
    if (!res.ok) {
      const errData = await res.json();
      showToast(errData.detail || "Error saving formula", "danger");
      return;
    }
    
    showToast("Formula saved successfully!", "success");
    loadFormulas();
  } catch (err) {
    console.error(err);
    showToast("Network error saving formula", "danger");
  }
}
