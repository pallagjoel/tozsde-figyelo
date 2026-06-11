/**
 * app.js — Tőzsde Figyelő Investment Intelligence Platform
 * Frontend Application Logic
 */

'use strict';

// ── Config ────────────────────────────────────────────────────────────────────
const API_BASE = '';

async function authFetch(url, options = {}) {
  const token = localStorage.getItem('quant_auth_token');
  const headers = { ...options.headers };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401) {
    localStorage.removeItem('quant_auth_token');
    document.getElementById('authOverlay').style.display = 'flex';
  }
  return res;
}
// ── Chart color palette ───────────────────────────────────────────────────────
const CHART_COLORS = [
  '#7c3aed', '#10b981', '#3b82f6', '#f59e0b', '#ef4444',
  '#8b5cf6', '#06b6d4', '#ec4899', '#84cc16', '#f97316',
];

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  stocks:          [],
  compareTickersList: [],
  modalTicker:     null,
  modalChart:      null,
  analysisChart:   null,
  rsiChart:        null,
  macdChart:       null,
  compareChart:    null,
  frontierChart:   null,
  analysisTicker:  null,
  analysisPeriod:  '6mo',
};

// ── DOM Refs ──────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const els = {
  stockInput:          $('stockTickerInput'),
  periodSelect:        $('historyPeriodSelect'),
  addBtn:              $('addStockBtn'),
  refreshAllBtn:       $('refreshAllBtn'),
  stocksContainer:     $('stocksContainer'),
  emptyState:          $('emptyState'),
  statCount:           $('statCount'),
  statGainers:         $('statGainers'),
  statLosers:          $('statLosers'),
  statBest:            $('statBest'),
  statBestChange:      $('statBestChange'),
  toastContainer:      $('toastContainer'),
  headerTime:          $('headerTime'),
  // Modal
  modal:               $('stockModal'),
  modalTitle:          $('modalTitle'),
  modalSubtitle:       $('modalSubtitle'),
  modalPrice:          $('modalPrice'),
  modalChange:         $('modalChange'),
  modalMetrics:        $('modalMetrics'),
  closeModalBtn:       $('closeModalBtn'),
  // Analysis
  analysisTickerInput: $('analysisTickerInput'),
  analysisPeriodSelect:$('analysisPeriodSelect'),
  analyzeBtn:          $('analyzeBtn'),
  analysisContent:     $('analysisContent'),
  overallSignal:       $('overallSignal'),
  rsiValue:            $('rsiValue'),
  strategySignals:     $('strategySignals'),
  fundamentalsGrid:    $('fundamentalsGrid'),
  // Compare
  compareTickerInput:  $('compareTickerInput'),
  addCompareTickerBtn: $('addCompareTickerBtn'),
  comparePeriodSelect: $('comparePeriodSelect'),
  runCompareBtn:       $('runCompareBtn'),
  compareTickersList:  $('compareTickersList'),
  compareChartArea:    $('compareChartArea'),
  compareStats:        $('compareStats'),
  // Portfolio
  portfolioTickersInput: $('portfolioTickersInput'),
  portfolioPeriodSelect: $('portfolioPeriodSelect'),
  optimizeBtn:           $('optimizeBtn'),
  portfolioResults:      $('portfolioResults'),
  portfolioMetrics:      $('portfolioMetrics'),
  sharpeWeights:         $('sharpeWeights'),
  minvolWeights:         $('minvolWeights'),
  // Strategies
  loadAllSignalsBtn:  $('loadAllSignalsBtn'),
  strategiesContent:  $('strategiesContent'),
};


// ── Utilities ─────────────────────────────────────────────────────────────────

function formatPrice(price, currency = 'USD') {
  if (price == null) return '—';
  const symbol = currency === 'HUF' ? 'Ft' : currency === 'EUR' ? '€' : '$';
  return symbol + Number(price).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatChange(change, pct) {
  if (change == null || pct == null) return '';
  const sign  = change >= 0 ? '+' : '';
  const arrow = change >= 0 ? '▲' : '▼';
  return `${arrow} ${sign}${Number(change).toFixed(2)} (${sign}${Number(pct).toFixed(2)}%)`;
}

function formatMarketCap(cap) {
  if (cap == null) return '—';
  if (cap >= 1e12) return `$${(cap / 1e12).toFixed(2)}T`;
  if (cap >= 1e9)  return `$${(cap / 1e9).toFixed(2)}B`;
  if (cap >= 1e6)  return `$${(cap / 1e6).toFixed(2)}M`;
  return `$${cap.toLocaleString()}`;
}

function formatVolume(vol) {
  if (vol == null) return '—';
  if (vol >= 1e9)  return `${(vol / 1e9).toFixed(2)}B`;
  if (vol >= 1e6)  return `${(vol / 1e6).toFixed(2)}M`;
  if (vol >= 1e3)  return `${(vol / 1e3).toFixed(1)}K`;
  return vol.toLocaleString();
}

function signalClass(signal) {
  if (!signal) return 'neutral';
  const s = signal.toUpperCase();
  if (s.includes('STRONG BUY') || s === 'BUY') return 'buy';
  if (s.includes('STRONG SELL') || s === 'SELL') return 'sell';
  if (s.includes('BUY')) return 'buy';
  if (s.includes('SELL')) return 'sell';
  return 'neutral';
}

function overallSignalClass(signal) {
  if (!signal) return 'neutral';
  const s = signal.toUpperCase();
  if (s === 'STRONG BUY') return 'buy-strong';
  if (s === 'BUY') return 'buy';
  if (s === 'STRONG SELL') return 'sell-strong';
  if (s === 'SELL') return 'sell';
  return 'neutral';
}

// ── Toast Notifications ───────────────────────────────────────────────────────

function showToast(message, type = 'info', duration = 4000) {
  const icons = { success: 'fa-check-circle', error: 'fa-times-circle', info: 'fa-info-circle' };
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<i class="fas ${icons[type] || icons.info} toast-icon"></i><span>${message}</span>`;
  els.toastContainer.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('removing');
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// ── API Helper ────────────────────────────────────────────────────────────────

async function api(path, options = {}) {
  try {
    const token = localStorage.getItem('quant_auth_token');
    const headers = { 'Content-Type': 'application/json', ...options.headers };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const res = await fetch(API_BASE + path, {
      headers,
      ...options,
    });
    
    if (res.status === 401) {
      localStorage.removeItem('quant_auth_token');
      document.getElementById('authOverlay').style.display = 'flex';
      const err = await res.json().catch(() => ({ detail: 'Unauthorized' }));
      throw new Error(err.detail || 'Unauthorized');
    }
    
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return await res.json();
  } catch (err) {
    if (err.name === 'TypeError' && err.message.includes('fetch')) {
      throw new Error('Cannot connect to API server. Make sure the backend is running on port 8000.');
    }
    throw err;
  }
}

// ── Navigation ────────────────────────────────────────────────────────────────

function setActivePage(pageId) {
  document.querySelectorAll('.page-section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const section = $(`page-${pageId}`);
  const navItem = $(`nav-${pageId}`);
  if (section) section.classList.add('active');
  if (navItem) navItem.classList.add('active');
}

document.querySelectorAll('.nav-item[data-page]').forEach(item => {
  item.addEventListener('click', e => {
    e.preventDefault();
    setActivePage(item.dataset.page);
  });
});

// ── Clock ─────────────────────────────────────────────────────────────────────

function updateClock() {
  const now = new Date();
  els.headerTime.textContent = now.toLocaleTimeString('hu-HU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
setInterval(updateClock, 1000);
updateClock();

// ── Chart Defaults ────────────────────────────────────────────────────────────

Chart.defaults.color = 'hsl(220, 15%, 65%)';
Chart.defaults.borderColor = 'hsl(228, 20%, 22%)';
Chart.defaults.font.family = 'Outfit, sans-serif';

function destroyChart(chartRef) {
  if (chartRef) { try { chartRef.destroy(); } catch (_) {} }
}

// ── Dashboard — Load Stocks ───────────────────────────────────────────────────

async function loadStocks() {
  try {
    const data = await api('/api/stocks');
    state.stocks = data.stocks || [];
    renderStocksGrid(state.stocks);
    renderSummaryStats(state.stocks);
  } catch (e) {
    showToast(e.message, 'error');
  }
}

function renderSummaryStats(stocks) {
  const withChange = stocks.filter(s => s.change_percent != null);
  const gainers = withChange.filter(s => s.change_percent >= 0);
  const losers  = withChange.filter(s => s.change_percent < 0);
  els.statCount.textContent   = stocks.length;
  els.statGainers.textContent = gainers.length;
  els.statLosers.textContent  = losers.length;

  if (withChange.length > 0) {
    const best = withChange.reduce((a, b) => b.change_percent > a.change_percent ? b : a);
    els.statBest.textContent        = best.ticker;
    const sign = best.change_percent >= 0 ? '+' : '';
    els.statBestChange.textContent  = `${sign}${best.change_percent.toFixed(2)}%`;
    els.statBestChange.className    = `stat-change ${best.change_percent >= 0 ? 'positive' : 'negative'}`;
  } else {
    els.statBest.textContent    = '—';
    els.statBestChange.textContent = '';
  }
}

function renderStocksGrid(stocks) {
  const grid = document.createElement('div');
  grid.className = 'stocks-grid';

  if (stocks.length === 0) {
    els.emptyState.style.display = 'flex';
    els.stocksContainer.innerHTML = '';
    els.stocksContainer.appendChild(els.emptyState);
    return;
  }

  els.emptyState.style.display = 'none';

  stocks.forEach(stock => {
    const card = document.createElement('div');
    card.className = `stock-card ${stock.change_percent != null ? (stock.change_percent >= 0 ? 'positive-card' : 'negative-card') : ''}`;
    card.dataset.ticker = stock.ticker;

    const price     = stock.current_price;
    const change    = stock.change;
    const changePct = stock.change_percent;
    const currency  = stock.currency || 'USD';
    const isPos     = changePct != null && changePct >= 0;

    card.innerHTML = `
      <div class="stock-card-header">
        <div class="stock-ticker-badge">${stock.ticker}</div>
        <div class="stock-card-actions">
          <button class="btn btn-icon btn-secondary btn-sm" title="Analyze" onclick="quickAnalyze('${stock.ticker}', event)">
            <i class="fas fa-microscope"></i>
          </button>
          <button class="btn btn-icon btn-danger btn-sm" title="Remove" onclick="removeStock('${stock.ticker}', event)">
            <i class="fas fa-trash"></i>
          </button>
        </div>
      </div>
      <div class="stock-name">${stock.name || stock.ticker}</div>
      <div class="stock-sector">${[stock.sector, stock.exchange].filter(Boolean).join(' · ')}</div>
      <div class="stock-price-row">
        <div class="stock-price">${formatPrice(price, currency)}</div>
        ${changePct != null ? `
          <div class="stock-change-badge ${isPos ? 'badge-positive' : 'badge-negative'}">
            ${isPos ? '▲' : '▼'} ${Math.abs(changePct).toFixed(2)}%
          </div>
        ` : ''}
      </div>
      <div class="stock-meta-row">
        <div class="stock-meta-item">
          <span class="stock-meta-label">High</span>
          <span class="stock-meta-value">${formatPrice(stock.day_high, currency)}</span>
        </div>
        <div class="stock-meta-item">
          <span class="stock-meta-label">Low</span>
          <span class="stock-meta-value">${formatPrice(stock.day_low, currency)}</span>
        </div>
        <div class="stock-meta-item">
          <span class="stock-meta-label">Volume</span>
          <span class="stock-meta-value">${formatVolume(stock.volume)}</span>
        </div>
        <div class="stock-meta-item">
          <span class="stock-meta-label">Mkt Cap</span>
          <span class="stock-meta-value">${formatMarketCap(stock.market_cap)}</span>
        </div>
      </div>
    `;

    card.addEventListener('click', () => openStockModal(stock.ticker));
    grid.appendChild(card);
  });

  els.stocksContainer.innerHTML = '';
  els.stocksContainer.appendChild(grid);
}

// ── Add Stock ─────────────────────────────────────────────────────────────────

async function addStock() {
  const ticker = els.stockInput.value.trim().toUpperCase();
  if (!ticker) { showToast('Please enter a ticker symbol.', 'error'); return; }

  els.addBtn.disabled = true;
  els.addBtn.innerHTML = '<span class="loading-spinner"></span> Adding...';

  try {
    const period = els.periodSelect.value;
    const data   = await api('/api/stocks', {
      method: 'POST',
      body:   JSON.stringify({ ticker, period }),
    });
    showToast(`✅ ${data.name} (${ticker}) added successfully!`, 'success');
    els.stockInput.value = '';
    await loadStocks();
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    els.addBtn.disabled = false;
    els.addBtn.innerHTML = '<i class="fas fa-plus"></i> Add Stock';
  }
}

els.addBtn.addEventListener('click', addStock);
els.stockInput.addEventListener('keydown', e => { if (e.key === 'Enter') addStock(); });

// ── Remove Stock ──────────────────────────────────────────────────────────────

async function removeStock(ticker, event) {
  if (event) event.stopPropagation();
  try {
    await api(`/api/stocks/${ticker}`, { method: 'DELETE' });
    showToast(`${ticker} removed from watchlist.`, 'info');
    await loadStocks();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

// ── Refresh All ───────────────────────────────────────────────────────────────

els.refreshAllBtn.addEventListener('click', async () => {
  els.refreshAllBtn.disabled = true;
  els.refreshAllBtn.innerHTML = '<span class="loading-spinner"></span>';
  try {
    const data = await api('/api/stocks/refresh', { method: 'POST' });
    showToast(`Refreshed ${data.updated.length} stocks.`, 'success');
    await loadStocks();
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    els.refreshAllBtn.disabled = false;
    els.refreshAllBtn.innerHTML = '<i class="fas fa-sync-alt"></i> Refresh';
  }
});

// ── Stock Detail Modal ────────────────────────────────────────────────────────

async function openStockModal(ticker) {
  state.modalTicker = ticker;
  els.modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';

  // Quick fill from cached state
  let stock = state.stocks.find(s => s.ticker === ticker) || {};
  let customFields = {};
  
  try {
    els.modalMetrics.innerHTML = '<div style="grid-column: 1 / -1; text-align: center;"><i class="fas fa-spinner fa-spin"></i> Loading...</div>';
    const res = await authFetch(`${API_BASE}/api/records?search=${ticker}&limit=1`);
    if (res.ok) {
      const data = await res.json();
      if (data.records && data.records.length > 0) {
        // Find exact match (search might return AAPL for AAPLL)
        const exact = data.records.find(r => r.ticker === ticker);
        if (exact) {
          stock = { ...stock, ...exact };
          customFields = exact.custom_fields || {};
        }
      }
    }
  } catch (e) {
    console.error("Failed to load full record data", e);
  }

  const currency = stock.currency || 'USD';

  els.modalTitle.textContent    = `${stock.name || ticker} (${ticker})`;
  els.modalSubtitle.textContent = [stock.sector, stock.exchange, stock.country].filter(Boolean).join(' · ');
  els.modalPrice.textContent    = formatPrice(stock.current_price || stock.market_price, currency);

  const chg = stock.change_percent;
  if (chg != null) {
    const isPos = chg >= 0;
    els.modalChange.textContent  = formatChange(stock.change, chg);
    els.modalChange.className    = isPos ? 'positive' : 'negative';
  } else {
    els.modalChange.textContent = '';
  }

  // Render key metrics
  const metricsList = [
    { label: 'P/E Ratio',   value: stock.pe_ratio    != null ? stock.pe_ratio.toFixed(2)  : '—' },
    { label: 'EPS',         value: stock.eps          != null ? `$${stock.eps.toFixed(2)}`  : '—' },
    { label: 'Div. Yield',  value: stock.dividend_yield != null ? `${(stock.dividend_yield * 100).toFixed(2)}%` : '—' },
    { label: 'Beta',        value: stock.beta         != null ? stock.beta.toFixed(2)       : '—' },
    { label: '52W High',    value: formatPrice(stock.fifty_two_week_high, currency) },
    { label: '52W Low',     value: formatPrice(stock.fifty_two_week_low,  currency) },
    { label: 'Market Cap',  value: formatMarketCap(stock.market_cap) },
    { label: 'Volume',      value: formatVolume(stock.volume) },
    { label: 'DCF Value',   value: stock.intrinsic_value_dcf != null ? `$${stock.intrinsic_value_dcf.toFixed(2)}` : '—' },
    { label: 'Z-Score',     value: stock.altman_z_score != null ? stock.altman_z_score.toFixed(2) : '—' },
    { label: 'MoS',         value: stock.margin_of_safety_pct != null ? `${stock.margin_of_safety_pct.toFixed(2)}%` : '—' },
    { label: 'WACC',        value: stock.wacc != null ? `${(stock.wacc * 100).toFixed(2)}%` : '—' },
    { label: 'Signal',      value: stock.signal || '—' },
  ];
  
  // We won't append customFields to metricsList anymore because we will build a massive grid below instead.

  els.modalMetrics.innerHTML = metricsList.map(m => `
    <div class="info-item">
      <div class="info-label">${m.label}</div>
      <div class="info-value">${m.value}</div>
    </div>
  `).join('');

  // Company description
  const descEl = $('modalDescription');
  const descText = $('modalDescriptionText');
  if (stock.description) {
    descText.textContent = stock.description;
    descEl.style.display = 'block';
    $('readMoreBtn').style.display = 'inline-flex';
    descText.style.maxHeight = '120px';
  } else {
    descEl.style.display = 'none';
  }

  // --- Inject Comprehensive Fields Grid Below ---
  const extraGridId = 'modalExtraFieldsGrid';
  let extraGridEl = document.getElementById(extraGridId);
  if (!extraGridEl) {
    extraGridEl = document.createElement('div');
    extraGridEl.id = extraGridId;
    extraGridEl.style.marginTop = '24px';
    extraGridEl.style.paddingTop = '24px';
    extraGridEl.style.borderTop = '1px solid var(--glass-border)';
    els.modal.querySelector('.modal-body').appendChild(extraGridEl);
  }

  let gridHtml = '<h3 class="section-title" style="font-size:1rem; margin-bottom:12px;"><i class="fas fa-list"></i> All Data Fields</h3>';
  gridHtml += '<div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(200px, 1fr)); gap:16px;">';
  
  if (p3State && p3State.activeFields) {
    p3State.activeFields.forEach(cf => {
      let val;
      if (!cf.is_standard) val = (stock.custom_fields && stock.custom_fields[cf.name] !== undefined) ? stock.custom_fields[cf.name] : null;
      else val = stock[cf.name];
      
      let displayVal = '—';
      if (val !== null && val !== undefined) {
        if (cf.field_type === 'currency') displayVal = typeof val === 'number' ? `$${val.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})}` : val;
        else if (cf.field_type === 'percent') displayVal = typeof val === 'number' ? `${val.toFixed(1)}%` : val;
        else if (cf.field_type === 'number') displayVal = typeof val === 'number' ? val.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2}) : val;
        else displayVal = val;
      }
      gridHtml += `
        <div style="background:var(--bg-secondary); padding:12px; border-radius:8px; border:1px solid var(--glass-border);">
          <div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px;">${cf.label || cf.name}</div>
          <div style="font-size:1rem; font-weight:600;">${displayVal}</div>
        </div>
      `;
    });
  }
  gridHtml += '</div>';
  extraGridEl.innerHTML = gridHtml;
  // --- End Inject ---

  // Load chart with default period
  const defaultTab = document.querySelector('#modalTimeframeTabs .timeframe-tab.active') || 
                     document.querySelector('#modalTimeframeTabs .timeframe-tab');
  const period = defaultTab ? defaultTab.dataset.period : '3mo';
  await loadModalChart(ticker, period, currency);
}

async function loadModalChart(ticker, period, currency) {
  try {
    const data = await api(`/api/stocks/${ticker}/history?period=${period}`);
    const history = data.history || [];

    const labels = history.map(h => h.date);
    const closes = history.map(h => h.close);
    const isPositive = closes.length >= 2 && closes[closes.length - 1] >= closes[0];
    const color = isPositive ? '#10b981' : '#ef4444';

    destroyChart(state.modalChart);

    const ctx = $('modalPriceChart').getContext('2d');
    const gradient = ctx.createLinearGradient(0, 0, 0, 280);
    gradient.addColorStop(0,   isPositive ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.3)');
    gradient.addColorStop(1,   'rgba(10,14,26,0)');

    state.modalChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: `${ticker} Close`,
          data:  closes,
          borderColor: color,
          backgroundColor: gradient,
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          fill: true,
          tension: 0.3,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            mode: 'index',
            intersect: false,
            callbacks: {
              label: ctx => `${formatPrice(ctx.raw, currency)}`,
            },
          },
        },
        scales: {
          x: {
            ticks: { maxTicksLimit: 8, maxRotation: 0 },
            grid: { display: false },
          },
          y: {
            position: 'right',
            ticks: { callback: v => formatPrice(v, currency) },
          },
        },
        interaction: { mode: 'index', intersect: false },
      },
    });
  } catch (e) {
    showToast(`Chart error: ${e.message}`, 'error');
  }
}

// Modal timeframe tabs
document.querySelector('#modalTimeframeTabs').addEventListener('click', async e => {
  const tab = e.target.closest('.timeframe-tab');
  if (!tab || !state.modalTicker) return;
  document.querySelectorAll('#modalTimeframeTabs .timeframe-tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  const stock = state.stocks.find(s => s.ticker === state.modalTicker) || {};
  await loadModalChart(state.modalTicker, tab.dataset.period, stock.currency || 'USD');
});

// Close modal
els.closeModalBtn.addEventListener('click', closeModal);
els.modal.addEventListener('click', e => { if (e.target === els.modal) closeModal(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

function closeModal() {
  els.modal.style.display = 'none';
  document.body.style.overflow = '';
  destroyChart(state.modalChart);
  state.modalChart  = null;
  state.modalTicker = null;
}

// ── Quick Analyze from card ───────────────────────────────────────────────────

function quickAnalyze(ticker, event) {
  if (event) event.stopPropagation();
  setActivePage('analysis');
  els.analysisTickerInput.value = ticker;
  runAnalysis();
}

// ── Analysis Page ─────────────────────────────────────────────────────────────

els.analyzeBtn.addEventListener('click', runAnalysis);
els.analysisTickerInput.addEventListener('keydown', e => { if (e.key === 'Enter') runAnalysis(); });

async function runAnalysis() {
  const ticker = els.analysisTickerInput.value.trim().toUpperCase();
  if (!ticker) { showToast('Enter a ticker to analyze.', 'error'); return; }

  state.analysisTicker = ticker;
  state.analysisPeriod = els.analysisPeriodSelect.value;

  els.analyzeBtn.disabled = true;
  els.analyzeBtn.innerHTML = '<span class="loading-spinner"></span> Analyzing...';

  try {
    const data = await api(`/api/analysis/${ticker}?period=${state.analysisPeriod}`);
    renderAnalysis(data, ticker);
    els.analysisContent.style.display = 'flex';
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    els.analyzeBtn.disabled = false;
    els.analyzeBtn.innerHTML = '<i class="fas fa-chart-bar"></i> Analyze';
  }
}

function renderAnalysis(data, ticker) {
  const { indicators, signals } = data;
  const stock = state.stocks.find(s => s.ticker === ticker) || {};
  const currency = stock.currency || 'USD';

  // Overall signal
  els.overallSignal.textContent = signals.overall_signal || '—';
  els.overallSignal.className   = `overall-signal-value ${overallSignalClass(signals.overall_signal)}`;
  els.rsiValue.textContent      = signals.rsi != null ? signals.rsi.toFixed(1) : '—';

  // Strategy signals
  els.strategySignals.innerHTML = '';
  const strategies = signals.strategies || {};
  const stratNames = {
    moving_average_cross: 'MA Cross',
    rsi:                  'RSI',
    bollinger_bands:      'Bollinger Bands',
    macd:                 'MACD',
  };

  Object.entries(strategies).forEach(([key, val]) => {
    const cls = signalClass(val.signal);
    const card = document.createElement('div');
    card.className = 'signal-card';
    card.innerHTML = `
      <div class="signal-card-header">
        <div class="signal-strategy-name">${stratNames[key] || key}</div>
        <div class="signal-badge ${cls}">${val.signal}</div>
      </div>
      <div class="signal-reason">${val.reason}</div>
    `;
    els.strategySignals.appendChild(card);
  });

  // Fundamentals
  els.fundamentalsGrid.innerHTML = [
    { label: '52W High',       value: formatPrice(signals.week52_high, currency) },
    { label: '52W Low',        value: formatPrice(signals.week52_low,  currency) },
    { label: 'From 52W High',  value: signals.pct_from_52w_high != null ? `${signals.pct_from_52w_high.toFixed(2)}%` : '—' },
    { label: 'Buy Signals',    value: signals.buy_signals || 0 },
    { label: 'Sell Signals',   value: signals.sell_signals || 0 },
    { label: 'P/E Ratio',      value: stock.pe_ratio    != null ? stock.pe_ratio.toFixed(2)  : '—' },
    { label: 'EPS',            value: stock.eps          != null ? `$${stock.eps.toFixed(2)}`  : '—' },
    { label: 'Beta',           value: stock.beta         != null ? stock.beta.toFixed(2)       : '—' },
    { label: 'Div. Yield',     value: stock.dividend_yield != null ? `${(stock.dividend_yield*100).toFixed(2)}%` : '—' },
    { label: 'Market Cap',     value: formatMarketCap(stock.market_cap) },
    { label: 'Sector',         value: stock.sector || '—' },
    { label: 'Country',        value: stock.country || '—' },
  ].map(m => `
    <div class="info-item">
      <div class="info-label">${m.label}</div>
      <div class="info-value">${m.value}</div>
    </div>
  `).join('');

  // Render charts
  renderIndicatorChart(indicators, currency);
  renderRsiChart(indicators);
  renderMacdChart(indicators);

  // Set active period tab
  document.querySelectorAll('#analysisTimeframeTabs .timeframe-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.period === state.analysisPeriod);
  });
}

function renderIndicatorChart(ind, currency) {
  destroyChart(state.analysisChart);
  const ctx = $('analysisChart').getContext('2d');

  const datasets = [
    { label: 'Close', data: ind.dates.map((_, i) => ({ x: ind.dates[i], y: null })), borderColor: '#7c3aed', borderWidth: 2, pointRadius: 0, tension: 0.2, order: 0 },
  ];

  // We need the actual close prices — fetch from history data embedded in indicators
  // The indicators object has dates, sma20, sma50, etc. but not close. We'll use sma values as reference.
  // For this chart we'll show SMA20, SMA50, Bollinger Bands
  const labels = ind.dates || [];

  state.analysisChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'SMA 20',
          data:  ind.sma20,
          borderColor: '#f59e0b',
          borderWidth: 1.5,
          borderDash: [4, 3],
          pointRadius: 0,
          fill: false,
          tension: 0.3,
        },
        {
          label: 'SMA 50',
          data:  ind.sma50,
          borderColor: '#3b82f6',
          borderWidth: 1.5,
          borderDash: [6, 4],
          pointRadius: 0,
          fill: false,
          tension: 0.3,
        },
        {
          label: 'BB Upper',
          data:  ind.bb_upper,
          borderColor: 'rgba(124,58,237,0.4)',
          borderWidth: 1,
          pointRadius: 0,
          fill: false,
          tension: 0.3,
        },
        {
          label: 'BB Lower',
          data:  ind.bb_lower,
          borderColor: 'rgba(124,58,237,0.4)',
          borderWidth: 1,
          pointRadius: 0,
          backgroundColor: 'rgba(124,58,237,0.05)',
          fill: '-1',
          tension: 0.3,
        },
        {
          label: 'EMA 20',
          data:  ind.ema20,
          borderColor: '#10b981',
          borderWidth: 1.5,
          pointRadius: 0,
          fill: false,
          tension: 0.3,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { mode: 'index', intersect: false },
      },
      scales: {
        x: { ticks: { maxTicksLimit: 10, maxRotation: 0 }, grid: { display: false } },
        y: { position: 'right', ticks: { callback: v => formatPrice(v, currency) } },
      },
    },
  });
}

function renderRsiChart(ind) {
  destroyChart(state.rsiChart);
  const ctx = $('rsiChart').getContext('2d');

  state.rsiChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: ind.dates,
      datasets: [{
        label: 'RSI (14)',
        data:  ind.rsi,
        borderColor: '#8b5cf6',
        borderWidth: 2,
        pointRadius: 0,
        fill: false,
        tension: 0.3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          mode: 'index',
          intersect: false,
          callbacks: { label: ctx => `RSI: ${ctx.raw?.toFixed(1) ?? '—'}` },
        },
        annotation: {},
      },
      scales: {
        x: { ticks: { maxTicksLimit: 8, maxRotation: 0 }, grid: { display: false } },
        y: {
          min: 0, max: 100,
          position: 'right',
          ticks: { callback: v => v },
          // Overbought/oversold zones via grid
        },
      },
    },
    plugins: [{
      id: 'rsi-zones',
      beforeDraw(chart) {
        const { ctx, chartArea: { left, right, top, bottom }, scales: { y } } = chart;
        if (!y) return;
        ctx.save();
        const y70 = y.getPixelForValue(70);
        const y30 = y.getPixelForValue(30);
        // Overbought zone
        ctx.fillStyle = 'rgba(239,68,68,0.08)';
        ctx.fillRect(left, top, right - left, y70 - top);
        // Oversold zone
        ctx.fillStyle = 'rgba(16,185,129,0.08)';
        ctx.fillRect(left, y30, right - left, bottom - y30);
        // Lines
        ctx.strokeStyle = 'rgba(239,68,68,0.4)';
        ctx.setLineDash([4, 4]);
        ctx.beginPath(); ctx.moveTo(left, y70); ctx.lineTo(right, y70); ctx.stroke();
        ctx.strokeStyle = 'rgba(16,185,129,0.4)';
        ctx.beginPath(); ctx.moveTo(left, y30); ctx.lineTo(right, y30); ctx.stroke();
        ctx.restore();
      },
    }],
  });
}

function renderMacdChart(ind) {
  destroyChart(state.macdChart);
  const ctx = $('macdChart').getContext('2d');

  const histColors = ind.macd_hist.map(v => (v != null && v >= 0) ? 'rgba(16,185,129,0.7)' : 'rgba(239,68,68,0.7)');

  state.macdChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: ind.dates,
      datasets: [
        {
          type:  'line',
          label: 'MACD',
          data:  ind.macd_line,
          borderColor: '#7c3aed',
          borderWidth: 2,
          pointRadius: 0,
          fill: false,
          order: 1,
          tension: 0.3,
        },
        {
          type:  'line',
          label: 'Signal',
          data:  ind.macd_signal,
          borderColor: '#f59e0b',
          borderWidth: 1.5,
          pointRadius: 0,
          fill: false,
          order: 2,
          tension: 0.3,
        },
        {
          type:  'bar',
          label: 'Histogram',
          data:  ind.macd_hist,
          backgroundColor: histColors,
          borderRadius: 2,
          order: 3,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { mode: 'index', intersect: false },
      },
      scales: {
        x: { ticks: { maxTicksLimit: 8, maxRotation: 0 }, grid: { display: false } },
        y: { position: 'right' },
      },
    },
  });
}

// Analysis timeframe tabs
$('analysisTimeframeTabs').addEventListener('click', async e => {
  const tab = e.target.closest('.timeframe-tab');
  if (!tab || !state.analysisTicker) return;
  document.querySelectorAll('#analysisTimeframeTabs .timeframe-tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  state.analysisPeriod = tab.dataset.period;
  els.analysisPeriodSelect.value = tab.dataset.period;
  await runAnalysis();
});

// ── Compare Page ──────────────────────────────────────────────────────────────

function addCompareTicker() {
  const ticker = els.compareTickerInput.value.trim().toUpperCase();
  if (!ticker) return;
  if (state.compareTickersList.includes(ticker)) {
    showToast(`${ticker} already in comparison list.`, 'info');
    return;
  }
  state.compareTickersList.push(ticker);
  renderCompareChips();
  els.compareTickerInput.value = '';
}

function renderCompareChips() {
  els.compareTickersList.innerHTML = '';
  state.compareTickersList.forEach((t, i) => {
    const chip = document.createElement('div');
    chip.className = 'compare-ticker-chip';
    chip.innerHTML = `${t} <button onclick="removeCompareTicker(${i})"><i class="fas fa-times"></i></button>`;
    els.compareTickersList.appendChild(chip);
  });
}

window.removeCompareTicker = function(idx) {
  state.compareTickersList.splice(idx, 1);
  renderCompareChips();
};

els.addCompareTickerBtn.addEventListener('click', addCompareTicker);
els.compareTickerInput.addEventListener('keydown', e => { if (e.key === 'Enter') addCompareTicker(); });

els.runCompareBtn.addEventListener('click', async () => {
  if (state.compareTickersList.length < 2) {
    showToast('Add at least 2 tickers to compare.', 'error');
    return;
  }

  els.runCompareBtn.disabled = true;
  els.runCompareBtn.innerHTML = '<span class="loading-spinner"></span> Loading...';

  try {
    const tickers = state.compareTickersList.join(',');
    const period  = els.comparePeriodSelect.value;
    const data    = await api(`/api/compare?tickers=${tickers}&period=${period}`);
    renderCompareChart(data);
    els.compareChartArea.style.display = 'flex';
    els.compareChartArea.style.flexDirection = 'column';
    els.compareChartArea.style.gap = '20px';
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    els.runCompareBtn.disabled = false;
    els.runCompareBtn.innerHTML = '<i class="fas fa-play"></i> Compare';
  }
});

function renderCompareChart(data) {
  destroyChart(state.compareChart);
  const comp = data.comparison;
  const tickers = Object.keys(comp).filter(t => !comp[t].error);

  const datasets = tickers.map((ticker, i) => ({
    label: ticker,
    data:  comp[ticker].normalized,
    labels: comp[ticker].dates,
    borderColor: CHART_COLORS[i % CHART_COLORS.length],
    borderWidth: 2,
    pointRadius: 0,
    fill: false,
    tension: 0.3,
  }));

  // Use the longest date set for x-axis labels
  const longestTicker = tickers.reduce((a, b) => (comp[a].dates || []).length > (comp[b].dates || []).length ? a : b, tickers[0]);
  const labels = comp[longestTicker]?.dates || [];

  const ctx = $('compareChart').getContext('2d');
  state.compareChart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: {
          mode: 'index',
          intersect: false,
          callbacks: {
            label: ctx => `${ctx.dataset.label}: ${ctx.raw >= 0 ? '+' : ''}${ctx.raw?.toFixed(2)}%`,
          },
        },
      },
      scales: {
        x: { ticks: { maxTicksLimit: 10, maxRotation: 0 }, grid: { display: false } },
        y: {
          position: 'right',
          ticks: { callback: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%` },
        },
      },
    },
  });

  // Summary stats
  els.compareStats.innerHTML = tickers.map((ticker, i) => {
    const ret = comp[ticker].total_return;
    const isPos = ret != null && ret >= 0;
    return `
      <div class="stat-card">
        <div class="stat-label" style="color:${CHART_COLORS[i % CHART_COLORS.length]}">${ticker}</div>
        <div class="stat-value" style="font-size:1.2rem;">${ret != null ? `${ret >= 0 ? '+' : ''}${ret.toFixed(2)}%` : '—'}</div>
        <div class="stat-change ${isPos ? 'positive' : 'negative'}">${isPos ? 'Gain' : 'Loss'} over period</div>
      </div>
    `;
  }).join('') + (Object.keys(comp).filter(t => comp[t].error).map(t => `
    <div class="stat-card">
      <div class="stat-label" style="color:var(--accent-danger)">${t}</div>
      <div class="stat-value text-danger" style="font-size:0.9rem;">Error</div>
      <div class="stat-change text-muted">${comp[t].error}</div>
    </div>
  `).join(''));
}

// ── Portfolio Optimization ────────────────────────────────────────────────────

els.optimizeBtn.addEventListener('click', async () => {
  const tickersRaw = els.portfolioTickersInput.value.trim();
  if (!tickersRaw) { showToast('Enter at least 2 tickers.', 'error'); return; }

  els.optimizeBtn.disabled = true;
  els.optimizeBtn.innerHTML = '<span class="loading-spinner"></span> Optimizing (3000 simulations)...';

  try {
    const tickers = tickersRaw;
    const period  = els.portfolioPeriodSelect.value;
    const data    = await api(`/api/portfolio/optimize?tickers=${encodeURIComponent(tickers)}&period=${period}`);
    renderPortfolioResults(data);
    els.portfolioResults.style.display = 'flex';
    els.portfolioResults.style.flexDirection = 'column';
    els.portfolioResults.style.gap = '20px';
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    els.optimizeBtn.disabled = false;
    els.optimizeBtn.innerHTML = '<i class="fas fa-calculator"></i> Optimize';
  }
});

function renderPortfolioResults(data) {
  const sharpe  = data.optimal_sharpe;
  const minvol  = data.min_volatility;

  // Metrics
  els.portfolioMetrics.innerHTML = `
    <div class="stat-card">
      <div class="stat-label">Optimal Return (Ann.)</div>
      <div class="stat-value text-success">${(sharpe.return * 100).toFixed(2)}%</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Optimal Sharpe Ratio</div>
      <div class="stat-value" style="color:var(--accent-primary)">${sharpe.sharpe.toFixed(3)}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Optimal Volatility (Ann.)</div>
      <div class="stat-value">${(sharpe.volatility * 100).toFixed(2)}%</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Min-Vol Return (Ann.)</div>
      <div class="stat-value">${(minvol.return * 100).toFixed(2)}%</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Min-Vol Volatility (Ann.)</div>
      <div class="stat-value text-success">${(minvol.volatility * 100).toFixed(2)}%</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Simulations Run</div>
      <div class="stat-value">3,000</div>
    </div>
  `;

  // Weights
  function renderWeights(containerEl, weights) {
    const sorted = Object.entries(weights).sort((a, b) => b[1] - a[1]);
    containerEl.innerHTML = sorted.map(([ticker, w]) => `
      <div class="weight-row">
        <div class="weight-ticker">${ticker}</div>
        <div class="weight-bar-bg">
          <div class="weight-bar-fill" style="width:${(w * 100).toFixed(1)}%"></div>
        </div>
        <div class="weight-pct">${(w * 100).toFixed(1)}%</div>
      </div>
    `).join('');
  }
  renderWeights(els.sharpeWeights, sharpe.weights);
  renderWeights(els.minvolWeights, minvol.weights);

  // Efficient frontier chart
  renderFrontierChart(data);
}

function renderFrontierChart(data) {
  destroyChart(state.frontierChart);
  const frontier = data.frontier;
  const sharpe   = data.optimal_sharpe;
  const minvol   = data.min_volatility;

  const ctx = $('frontierChart').getContext('2d');

  // Sample to max 500 points for performance
  const step = Math.ceil(frontier.volatility.length / 500);
  const sampledVol = frontier.volatility.filter((_, i) => i % step === 0);
  const sampledRet = frontier.returns.filter((_, i) => i % step === 0);
  const sampledSharpe = frontier.sharpe.filter((_, i) => i % step === 0);
  const maxSharpe = Math.max(...frontier.sharpe);
  const minSharpeVal = Math.min(...frontier.sharpe);

  state.frontierChart = new Chart(ctx, {
    type: 'scatter',
    data: {
      datasets: [
        {
          label: 'Portfolios',
          data:  sampledVol.map((v, i) => ({ x: v * 100, y: sampledRet[i] * 100, sharpe: sampledSharpe[i] })),
          backgroundColor: sampledSharpe.map(s => {
            const t = (s - minSharpeVal) / (maxSharpe - minSharpeVal);
            const r = Math.round(124 + (16 - 124) * t);
            const g = Math.round(58  + (185 - 58) * t);
            const b = Math.round(237 + (129 - 237) * t);
            return `rgba(${r},${g},${b},0.6)`;
          }),
          pointRadius: 3,
          pointHoverRadius: 5,
        },
        {
          label: 'Max Sharpe',
          data:  [{ x: sharpe.volatility * 100, y: sharpe.return * 100 }],
          backgroundColor: '#fbbf24',
          borderColor:     '#f59e0b',
          borderWidth: 2,
          pointRadius: 10,
          pointStyle: 'star',
        },
        {
          label: 'Min Volatility',
          data:  [{ x: minvol.volatility * 100, y: minvol.return * 100 }],
          backgroundColor: '#10b981',
          borderColor:     '#059669',
          borderWidth: 2,
          pointRadius: 10,
          pointStyle: 'triangle',
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: ctx => {
              if (ctx.dataset.label === 'Portfolios') {
                return `Vol: ${ctx.raw.x.toFixed(2)}% | Ret: ${ctx.raw.y.toFixed(2)}% | Sharpe: ${ctx.raw.sharpe?.toFixed(3)}`;
              }
              return `${ctx.dataset.label}: Vol ${ctx.raw.x.toFixed(2)}% | Ret ${ctx.raw.y.toFixed(2)}%`;
            },
          },
        },
      },
      scales: {
        x: { title: { display: true, text: 'Annual Volatility (%)' } },
        y: { title: { display: true, text: 'Annual Return (%)'    } },
      },
    },
  });
}

// ── Strategies Page ───────────────────────────────────────────────────────────

els.loadAllSignalsBtn.addEventListener('click', loadAllSignals);

async function loadAllSignals() {
  if (state.stocks.length === 0) {
    showToast('Add stocks to your watchlist first.', 'error');
    return;
  }

  els.loadAllSignalsBtn.disabled = true;
  els.loadAllSignalsBtn.innerHTML = '<span class="loading-spinner"></span> Loading signals...';
  els.strategiesContent.innerHTML = '<div class="glass-card" style="text-align:center; padding:40px; color:var(--text-muted);">Generating signals for all tracked stocks…</div>';

  const results = [];
  for (const stock of state.stocks) {
    try {
      const data = await api(`/api/analysis/${stock.ticker}?period=1yr`);
      results.push({ stock, analysis: data });
    } catch (e) {
      results.push({ stock, error: e.message });
    }
  }

  renderStrategiesTable(results);

  els.loadAllSignalsBtn.disabled = false;
  els.loadAllSignalsBtn.innerHTML = '<i class="fas fa-bolt"></i> Load All Signals';
}

function renderStrategiesTable(results) {
  if (results.length === 0) { return; }

  const container = document.createElement('div');
  container.style.cssText = 'display:flex; flex-direction:column; gap:12px;';

  results.forEach(({ stock, analysis, error }) => {
    const card = document.createElement('div');
    card.className = 'glass-card';

    if (error) {
      card.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div>
            <div class="stock-ticker-badge" style="display:inline-block; margin-bottom:4px;">${stock.ticker}</div>
            <div class="stock-name">${stock.name || stock.ticker}</div>
          </div>
          <div class="signal-badge sell">ERROR</div>
          <div class="text-muted" style="font-size:0.8rem;">${error}</div>
        </div>
      `;
    } else {
      const signals   = analysis.signals;
      const overall   = signals.overall_signal;
      const cls       = signalClass(overall);
      const strategies = signals.strategies || {};

      card.innerHTML = `
        <div style="display:grid; grid-template-columns:200px 1fr; gap:20px; align-items:center; flex-wrap:wrap;">
          <div>
            <div class="stock-ticker-badge" style="display:inline-block; margin-bottom:6px;">${stock.ticker}</div>
            <div class="stock-name">${stock.name || stock.ticker}</div>
            <div class="stock-sector">${stock.sector || ''}</div>
            <div style="margin-top:8px; font-size:0.8rem; color:var(--text-muted);">
              RSI: <strong style="color:var(--text-primary)">${signals.rsi?.toFixed(1) ?? '—'}</strong>
              &nbsp;|&nbsp;
              Price: <strong style="color:var(--text-primary)">${formatPrice(signals.current_price, stock.currency)}</strong>
            </div>
          </div>
          <div style="display:flex; flex-direction:column; gap:8px;">
            <div style="display:flex; align-items:center; justify-content:space-between;">
              <div style="font-size:0.75rem; color:var(--text-muted);">COMPOSITE SIGNAL</div>
              <div class="signal-badge ${cls}" style="font-size:0.8rem; padding:4px 14px;">${overall}</div>
            </div>
            <div style="display:flex; gap:6px; flex-wrap:wrap;">
              ${Object.entries(strategies).map(([key, val]) => {
                const sc = signalClass(val.signal);
                const names = { moving_average_cross: 'MA', rsi: 'RSI', bollinger_bands: 'BB', macd: 'MACD' };
                return `<div class="signal-badge ${sc}" style="font-size:0.7rem;">${names[key]}: ${val.signal}</div>`;
              }).join('')}
            </div>
          </div>
        </div>
      `;
      card.style.cursor = 'pointer';
      card.addEventListener('click', () => quickAnalyze(stock.ticker, null));
    }

    container.appendChild(card);
  });

  els.strategiesContent.innerHTML = '';
  els.strategiesContent.appendChild(container);
}

// ── Auto-refresh price every 5 minutes ───────────────────────────────────────
setInterval(async () => {
  if (state.stocks.length > 0) {
    try {
      await api('/api/stocks/refresh', { method: 'POST' });
      await loadStocks();
    } catch (_) {}
  }
}, 5 * 60 * 1000);

// ── Initial Load ──────────────────────────────────────────────────────────────

(async function init() {
  try {
    await api('/api/health');
    await loadStocks();
  } catch (e) {
    showToast('⚠️ Cannot connect to API server. Run: python -m uvicorn main:app --reload --port 8000', 'error', 8000);
  }
})();

// ══════════════════════════════════════════════════════════════════════════════
// VALUATIONS PAGE
// ══════════════════════════════════════════════════════════════════════════════

async function loadValuationsPage() {
  // Load macro rates
  try {
    const macroRes = await authFetch(`${API_BASE}/api/macro`);
    const macroData = await macroRes.json();
    if (macroData.macro) {
      const m = macroData.macro;
      document.getElementById('macroRf').textContent = m.risk_free_rate != null ? m.risk_free_rate.toFixed(2) + '%' : '—';
      document.getElementById('macroRm').textContent = m.market_return != null ? m.market_return.toFixed(1) + '%' : '—';
      document.getElementById('macroInflation').textContent = m.inflation_rate != null ? m.inflation_rate.toFixed(2) + '%' : '—';
      document.getElementById('macroErp').textContent = m.equity_risk_premium != null ? m.equity_risk_premium.toFixed(2) + '%' : '—';
    }
  } catch (e) { console.warn('Macro rates not loaded:', e); }

  // Load valuations table
  try {
    const res = await authFetch(`${API_BASE}/api/valuations`);
    const data = await res.json();
    renderValuationTable(data.valuations || []);
  } catch (e) { console.warn('Valuations not loaded:', e); }
}

function renderValuationTable(valuations) {
  const tbody = document.getElementById('valuationTableBody');
  if (!valuations.length) {
    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center; padding:40px; color:var(--text-muted);">No valuations computed. Run ETL then Valuations to populate.</td></tr>';
    return;
  }

  tbody.innerHTML = valuations.map(v => {
    const val = v.valuation;
    if (!val) return `<tr><td>${v.ticker}</td><td>${v.name || '—'}</td><td colspan="8" style="color:var(--text-muted);">No valuation data</td></tr>`;

    const mosClass = val.margin_of_safety_pct > 20 ? 'positive' : val.margin_of_safety_pct < 0 ? 'negative' : 'neutral';
    const zBadgeClass = val.z_score_zone === 'SAFE' ? 'safe' : val.z_score_zone === 'GREY' ? 'grey' : 'distress';
    const signalCls = getValuationSignalClass(val.signal);

    return `<tr>
      <td><strong>${v.ticker}</strong></td>
      <td style="max-width:180px; overflow:hidden; text-overflow:ellipsis;">${v.name || '—'}</td>
      <td style="color:var(--text-muted);">${v.sector || '—'}</td>
      <td>$${val.market_price != null ? val.market_price.toFixed(2) : '—'}</td>
      <td style="color:var(--accent-primary); font-weight:600;">$${val.intrinsic_value_dcf != null ? val.intrinsic_value_dcf.toFixed(2) : '—'}</td>
      <td><span class="mos-value ${mosClass}">${val.margin_of_safety_pct != null ? val.margin_of_safety_pct.toFixed(1) + '%' : '—'}</span></td>
      <td>${val.altman_z_score != null ? `<span class="z-badge ${zBadgeClass}">${val.altman_z_score.toFixed(2)} ${val.z_score_zone}</span>` : '—'}</td>
      <td>${val.wacc != null ? (val.wacc * 100).toFixed(1) + '%' : '—'}</td>
      <td><span class="signal-badge ${signalCls}">${formatValuationSignal(val.signal)}</span></td>
      <td><button class="dcf-btn" onclick="openDcfModal('${v.ticker}')"><i class="fas fa-expand"></i> Detail</button></td>
    </tr>`;
  }).join('');
}

function getValuationSignalClass(signal) {
  if (!signal) return 'insufficient';
  const map = { 'STRONG_BUY': 'strong-buy', 'BUY': 'buy', 'HOLD': 'hold', 'OVERVALUED': 'overvalued', 'VALUE_TRAP': 'value-trap', 'INSUFFICIENT_DATA': 'insufficient' };
  return map[signal] || 'insufficient';
}

function formatValuationSignal(signal) {
  if (!signal) return 'N/A';
  return signal.replace(/_/g, ' ');
}

// ── ETL & Valuations Buttons ──
document.getElementById('runEtlBtn')?.addEventListener('click', async () => {
  const statusCard = document.getElementById('etlStatusCard');
  const statusText = document.getElementById('etlStatusText');
  statusCard.style.display = 'block';
  statusText.textContent = 'Running ETL pipeline (FRED → FMP → yfinance)... This may take a few minutes.';

  try {
    const res = await authFetch(`${API_BASE}/api/etl/run`, { method: 'POST' });
    const data = await res.json();
    statusText.innerHTML = `<i class="fas fa-check" style="color:#00e676;"></i> ETL Complete: ${data.results?.companies?.length || 0} companies processed.`;
    showToast('ETL pipeline completed successfully!', 'success');
    setTimeout(() => { statusCard.style.display = 'none'; }, 5000);
  } catch (e) {
    statusText.innerHTML = `<i class="fas fa-exclamation-triangle" style="color:#ef5350;"></i> ETL Error: ${e.message}`;
    showToast('ETL pipeline failed.', 'error');
  }
});

document.getElementById('runValuationsBtn')?.addEventListener('click', async () => {
  const statusCard = document.getElementById('etlStatusCard');
  const statusText = document.getElementById('etlStatusText');
  statusCard.style.display = 'block';
  statusText.textContent = 'Running valuation engine (DCF + CAPM + Z-Score)...';

  try {
    const res = await authFetch(`${API_BASE}/api/valuations/run`, { method: 'POST' });
    const data = await res.json();
    statusText.innerHTML = `<i class="fas fa-check" style="color:#00e676;"></i> Valuations complete: ${data.results?.computed?.length || 0} stocks valued.`;
    showToast('Valuation engine completed!', 'success');
    loadValuationsPage();
    setTimeout(() => { statusCard.style.display = 'none'; }, 5000);
  } catch (e) {
    statusText.innerHTML = `<i class="fas fa-exclamation-triangle" style="color:#ef5350;"></i> Valuation Error: ${e.message}`;
    showToast('Valuation engine failed.', 'error');
  }
});

// ── DCF Detail Modal ──
async function openDcfModal(ticker) {
  const modal = document.getElementById('dcfModal');
  modal.style.display = 'flex';

  try {
    const res = await authFetch(`${API_BASE}/api/valuations/${ticker}/dcf-breakdown`);
    const d = await res.json();

    document.getElementById('dcfModalTitle').textContent = `${ticker} — ${d.name}`;
    document.getElementById('dcfModalSubtitle').textContent = `Computed: ${d.computed_at ? new Date(d.computed_at).toLocaleString() : 'N/A'} | Data: ${d.data_quality}`;

    // Signal banner
    const banner = document.getElementById('dcfSignalBanner');
    banner.className = `dcf-signal-banner ${getValuationSignalClass(d.signal)}`;
    banner.textContent = formatValuationSignal(d.signal);

    // Key metrics
    document.getElementById('dcfKeyMetrics').innerHTML = [
      dcfStatCard('Intrinsic Value', d.dcf.intrinsic_value_per_share != null ? '$' + d.dcf.intrinsic_value_per_share.toFixed(2) : '—'),
      dcfStatCard('Market Price', d.dcf.market_price != null ? '$' + d.dcf.market_price.toFixed(2) : '—'),
      dcfStatCard('Margin of Safety', d.dcf.margin_of_safety_pct != null ? d.dcf.margin_of_safety_pct.toFixed(1) + '%' : '—'),
      dcfStatCard('Z-Score', d.altman_z.z_score != null ? d.altman_z.z_score.toFixed(2) + ' (' + d.altman_z.zone + ')' : '—'),
    ].join('');

    // DCF components
    document.getElementById('dcfComponents').innerHTML = [
      dcfInfoItem('FCF Growth Rate', dcfPct(d.dcf.fcf_growth_rate)),
      dcfInfoItem('Terminal Growth', dcfPct(d.dcf.terminal_growth_rate)),
      dcfInfoItem('WACC', dcfPct(d.dcf.wacc)),
      dcfInfoItem('PV of FCFs', dcfDollar(d.dcf.pv_of_projected_fcfs)),
      dcfInfoItem('Terminal Value', dcfDollar(d.dcf.terminal_value)),
      dcfInfoItem('PV of Terminal', dcfDollar(d.dcf.pv_of_terminal_value)),
      dcfInfoItem('Enterprise Value', dcfDollar(d.dcf.enterprise_value)),
      dcfInfoItem('Net Debt', dcfDollar(d.dcf.net_debt)),
      dcfInfoItem('Equity Value', dcfDollar(d.dcf.equity_value)),
    ].join('');

    // CAPM
    document.getElementById('dcfCapm').innerHTML = [
      dcfInfoItem('Risk-Free Rate', dcfPct(d.capm.risk_free_rate)),
      dcfInfoItem('Equity Risk Premium', dcfPct(d.capm.equity_risk_premium)),
      dcfInfoItem('Beta', d.capm.beta != null ? d.capm.beta.toFixed(2) : '—'),
      dcfInfoItem('CAPM Expected Return', dcfPct(d.capm.expected_return)),
      dcfInfoItem('Cost of Equity', dcfPct(d.capm.cost_of_equity)),
      dcfInfoItem('Cost of Debt', dcfPct(d.capm.cost_of_debt)),
    ].join('');

    // Z-Score
    document.getElementById('dcfZScore').innerHTML = [
      dcfInfoItem('Z-Score', d.altman_z.z_score != null ? d.altman_z.z_score.toFixed(4) : '—'),
      dcfInfoItem('Zone', d.altman_z.zone || '—'),
      dcfInfoItem('X1 (WC/TA)', d.altman_z.x1_wc_ta != null ? d.altman_z.x1_wc_ta.toFixed(4) : '—'),
      dcfInfoItem('X2 (RE/TA)', d.altman_z.x2_re_ta != null ? d.altman_z.x2_re_ta.toFixed(4) : '—'),
      dcfInfoItem('X3 (EBIT/TA)', d.altman_z.x3_ebit_ta != null ? d.altman_z.x3_ebit_ta.toFixed(4) : '—'),
      dcfInfoItem('X4 (MCap/TL)', d.altman_z.x4_mcap_tl != null ? d.altman_z.x4_mcap_tl.toFixed(4) : '—'),
      dcfInfoItem('X5 (Rev/TA)', d.altman_z.x5_rev_ta != null ? d.altman_z.x5_rev_ta.toFixed(4) : '—'),
    ].join('');

    // Signal reason
    document.getElementById('dcfSignalReason').textContent = d.signal_reason || 'No signal reasoning available.';

  } catch (e) {
    document.getElementById('dcfKeyMetrics').innerHTML = `<div style="color:#ef5350;">Error loading DCF data: ${e.message}</div>`;
  }
}

function dcfStatCard(label, value) {
  return `<div class="stat-card"><div class="stat-label">${label}</div><div class="stat-value" style="font-size:1.1rem;">${value}</div></div>`;
}

function dcfInfoItem(label, value) {
  return `<div class="info-item"><div class="info-label">${label}</div><div class="info-value">${value}</div></div>`;
}

function dcfPct(v) { return v != null ? (v * 100).toFixed(2) + '%' : '—'; }
function dcfDollar(v) { return v != null ? '$' + Number(v).toLocaleString(undefined, {maximumFractionDigits: 0}) : '—'; }

document.getElementById('closeDcfModalBtn')?.addEventListener('click', () => {
  document.getElementById('dcfModal').style.display = 'none';
});

document.getElementById('dcfModal')?.addEventListener('click', (e) => {
  if (e.target.id === 'dcfModal') document.getElementById('dcfModal').style.display = 'none';
});



// ── Hook into page navigation ──
document.querySelectorAll('.nav-item[data-page]').forEach(item => {
  item.addEventListener('click', () => {
    // Only intercept if it's NOT a dynamic custom object or records override
    // Our custom binding logic handles records clicks
    if (item.dataset.page === 'records' && item.hasAttribute('data-object-id')) return;
    
    const page = item.dataset.page;
    if (page === 'records') loadRecordsPage();
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// PHASE 3: RECORDS, BULK IMPORT, ADMIN PLATFORM
// ══════════════════════════════════════════════════════════════════════════════

// ── DOM Refs (Phase 3) ──
const p3 = {
  // Records
  recordSearch: $('recordSearch'),
  recordsTableHeader: $('recordsTableHeader'),
  recordsTableBody: $('recordsTableBody'),
  recordsPrevBtn: $('recordsPrevBtn'),
  recordsNextBtn: $('recordsNextBtn'),
  recordsPaginationInfo: $('recordsPaginationInfo'),
};

// ── State (Phase 3) ──
const p3State = {
  recordsLimit: 50,
  recordsOffset: 0,
  recordsTotal: 0,
  currentObjectId: -1,
  currentObjectName: "Stocks",
  customObjects: [],
  activeFields: [],
  currentRecords: [],
  activeLayouts: [],
};

// Fetch Custom Objects on load
async function loadCustomObjectsNav() {
  try {
    const res = await authFetch(`${API_BASE}/api/admin/objects`);
    if (!res.ok) return;
    const data = await res.json();
    p3State.customObjects = data.objects || [];
    
    const navSection = document.getElementById('dynamicNavSection');
    if (!navSection) return;
    
    let html = '';
    p3State.customObjects.forEach(obj => {
      html += `
        <a class="nav-item" data-page="records" data-object-id="${obj.id}" data-object-name="${obj.name}" data-object-label="${obj.plural_label || obj.label}">
          <i class="fas fa-cube"></i> ${obj.plural_label || obj.label}
        </a>
      `;
    });
    navSection.innerHTML = html;
    
    // Bind click events to the new nav items
    navSection.querySelectorAll('.nav-item').forEach(item => {
      item.addEventListener('click', (e) => {
        // Remove active class from all
        document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
        item.classList.add('active');
        
        p3State.currentObjectId = parseInt(item.dataset.objectId);
        p3State.currentObjectName = item.dataset.objectName;
        document.getElementById('recordsPageTitle').innerHTML = `<i class="fas fa-cube"></i> ${item.dataset.objectLabel}`;
        document.getElementById('recordsPageSubtitle').textContent = `Full database view of all tracked ${item.dataset.objectLabel}`;
        
        p3State.recordsOffset = 0;
        setActivePage('records');
        loadRecordsPage();
      });
    });
    
    // Bind the original Stocks nav item
    const navRecords = document.getElementById('nav-records');
    if (navRecords) {
      navRecords.addEventListener('click', (e) => {
        document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
        navRecords.classList.add('active');
        
        p3State.currentObjectId = -1;
        p3State.currentObjectName = "Stocks";
        document.getElementById('recordsPageTitle').innerHTML = `<i class="fas fa-database"></i> Stocks`;
        document.getElementById('recordsPageSubtitle').textContent = `Full database view of all tracked equities and computed metrics`;
        
        p3State.recordsOffset = 0;
        setActivePage('records');
        loadRecordsPage();
      });
    }
  } catch (err) {
    console.error("Failed to load custom objects", err);
  }
}
// Execute on load
document.addEventListener('DOMContentLoaded', loadCustomObjectsNav);

// ── 1. Records Page ──

async function loadRecordsPage() {
  try {
    p3.recordsTableBody.innerHTML = '<tr><td colspan="100" style="text-align:center; padding:40px;"><i class="fas fa-spinner fa-spin"></i> Loading records...</td></tr>';
    
    // 1. Fetch Fields for current object
    const fieldsRes = await authFetch(`${API_BASE}/api/admin/fields?object_id=${p3State.currentObjectId}`);
    if (!fieldsRes.ok) throw new Error('Failed to fetch field definitions');
    const fieldsData = await fieldsRes.json();
    p3State.activeFields = (fieldsData.fields || []).filter(f => f.is_active);
    
    // 1b. Fetch Layouts for current object
    const layoutsRes = await authFetch(`${API_BASE}/api/admin/layouts?object_id=${p3State.currentObjectId}`);
    if (layoutsRes.ok) {
        const layoutsData = await layoutsRes.json();
        p3State.activeLayouts = (layoutsData.layouts || []).filter(l => l.is_active);
    }
    
    // 2. Fetch Records
    const search = p3.recordSearch?.value || '';
    const query = new URLSearchParams({
      limit: p3State.recordsLimit,
      offset: p3State.recordsOffset,
    });
    if (search) query.append('search', search);

    let url = `${API_BASE}/api/records?${query.toString()}`;
    if (p3State.currentObjectId !== -1) {
      url = `${API_BASE}/api/objects/${p3State.currentObjectName}/records?${query.toString()}`;
    }

    const res = await authFetch(url);
    if (!res.ok) throw new Error('Failed to fetch records');
    const data = await res.json();
    
    p3State.recordsTotal = data.total;
    p3State.currentRecords = data.records;
    
    // Toggle New Record button visibility
    const newRecordBtn = document.getElementById('newRecordBtn');
    if (newRecordBtn) {
        newRecordBtn.style.display = p3State.currentObjectId !== -1 ? 'block' : 'none';
    }
    
    // Apply column filters from localStorage
    const savedColsStr = localStorage.getItem(`list_view_cols_${p3State.currentObjectId}`);
    let displayFields = p3State.activeFields;
    if (savedColsStr) {
      const savedCols = JSON.parse(savedColsStr);
      displayFields = p3State.activeFields.filter(f => savedCols.includes(f.name));
    } else if (p3State.currentObjectId === -1) {
      // Default standard view for stocks if no preference saved
      const defaults = ['ticker', 'name', 'sector', 'market_price', 'market_cap', 'intrinsic_value_dcf', 'margin_of_safety_pct', 'altman_z_score', 'wacc', 'signal'];
      displayFields = p3State.activeFields.filter(f => defaults.includes(f.name) || f.field_type === 'formula');
    }
    
    renderRecordsTable(p3State.currentRecords, displayFields);
    
    // Pagination UI
    const end = Math.min(p3State.recordsOffset + p3State.recordsLimit, p3State.recordsTotal);
    p3.recordsPaginationInfo.textContent = `Showing ${p3State.recordsOffset + (data.records.length ? 1 : 0)} to ${end} of ${p3State.recordsTotal}`;
    p3.recordsPrevBtn.disabled = p3State.recordsOffset === 0;
    p3.recordsNextBtn.disabled = end >= p3State.recordsTotal;
    
  } catch (err) {
    showToast('error', err.message);
  }
}

function renderRecordsTable(records, activeFields) {
  if (!records.length) {
    p3.recordsTableHeader.innerHTML = '';
    p3.recordsTableBody.innerHTML = '<tr><td colspan="100" style="text-align:center; padding:40px; color:var(--text-muted);">No records found.</td></tr>';
    return;
  }
  
  // Create columns strictly from activeFields (defined in Setup)
  const cols = activeFields.map(cf => ({
    key: cf.name,
    label: cf.label || cf.name,
    type: cf.field_type,
    isStandard: !!cf.is_standard
  }));

  // Render Header
  p3.recordsTableHeader.innerHTML = '<tr>' + cols.map(c => `<th>${c.label}${!c.isStandard ? ' <i class="fas fa-calculator" style="color:var(--accent-primary); font-size:0.7em;"></i>' : ''}</th>`).join('') + '</tr>';
  
  // Render Body
  p3.recordsTableBody.innerHTML = records.map((r, index) => {
    let onClickAttr = `style="cursor:pointer;" onclick="openRecordModal(${index})"`;
    
    return `<tr ${onClickAttr}>` + cols.map(c => {
      let val;
      // Resolve value from record payload
      if (p3State.currentObjectId === -1) {
        // Stocks API splits it into r[key] or r.custom_fields[key]
        if (!c.isStandard) {
          val = (r.custom_fields && r.custom_fields[c.key] !== undefined) ? r.custom_fields[c.key] : null;
        } else {
          val = r[c.key];
        }
      } else {
        // Custom Object API puts data inside r.data JSON field
        val = (r.data && r.data[c.key] !== undefined) ? r.data[c.key] : null;
      }
      
      let displayVal = '—';
      if (val !== null && val !== undefined) {
        if (c.type === 'currency') displayVal = typeof val === 'number' ? `$${val.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})}` : val;
        else if (c.type === 'percent') displayVal = typeof val === 'number' ? `${val.toFixed(1)}%` : val;
        else if (c.type === 'number') displayVal = typeof val === 'number' ? val.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2}) : val;
        else displayVal = val;
      }
      
      return `<td>${displayVal}</td>`;
    }).join('') + '</tr>';
  }).join('');
}

// Bind Records Events
if (p3.recordSearch) {
  let searchTimeout;
  p3.recordSearch.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      p3State.recordsOffset = 0;
      loadRecordsPage();
    }, 500);
  });
}
if (p3.recordsPrevBtn) p3.recordsPrevBtn.addEventListener('click', () => {
  p3State.recordsOffset = Math.max(0, p3State.recordsOffset - p3State.recordsLimit);
  loadRecordsPage();
});
if (p3.recordsNextBtn) p3.recordsNextBtn.addEventListener('click', () => {
  p3State.recordsOffset += p3State.recordsLimit;
  loadRecordsPage();
});

// ── Columns Modal Logic ──
const manageColumnsBtn = document.getElementById('manageColumnsBtn');
const columnsModal = document.getElementById('columnsModal');
const closeColumnsModalBtn = document.getElementById('closeColumnsModalBtn');
const saveColumnsBtn = document.getElementById('saveColumnsBtn');
const columnsCheckboxList = document.getElementById('columnsCheckboxList');

manageColumnsBtn?.addEventListener('click', () => {
  if (!p3State.activeFields.length) return;
  
  // Load saved preferences
  const savedColsStr = localStorage.getItem(`list_view_cols_${p3State.currentObjectId}`);
  let savedCols = [];
  if (savedColsStr) {
    savedCols = JSON.parse(savedColsStr);
  } else if (p3State.currentObjectId === -1) {
    savedCols = ['ticker', 'name', 'sector', 'market_price', 'market_cap', 'intrinsic_value_dcf', 'margin_of_safety_pct', 'altman_z_score', 'wacc', 'signal'];
    p3State.activeFields.filter(f => f.field_type === 'formula').forEach(f => savedCols.push(f.name));
  } else {
    savedCols = p3State.activeFields.map(f => f.name);
  }

  columnsCheckboxList.innerHTML = p3State.activeFields.map(f => `
    <label style="display:flex; align-items:center; gap:8px; cursor:pointer;">
      <input type="checkbox" class="col-checkbox" value="${f.name}" ${savedCols.includes(f.name) ? 'checked' : ''}>
      ${f.label || f.name} ${!f.is_standard ? '<i class="fas fa-calculator" style="color:var(--accent-primary); font-size:0.7em;"></i>' : ''}
    </label>
  `).join('');
  
  columnsModal.style.display = 'flex';
});

closeColumnsModalBtn?.addEventListener('click', () => {
  columnsModal.style.display = 'none';
});

saveColumnsBtn?.addEventListener('click', () => {
  const selected = Array.from(columnsCheckboxList.querySelectorAll('.col-checkbox:checked')).map(cb => cb.value);
  localStorage.setItem(`list_view_cols_${p3State.currentObjectId}`, JSON.stringify(selected));
  columnsModal.style.display = 'none';
  
  // Re-render table with new columns
  const displayFields = p3State.activeFields.filter(f => selected.includes(f.name));
  renderRecordsTable(p3State.currentRecords, displayFields);
});

// ── Generic Record Detail Modal ──
const recordDetailModal = document.getElementById('recordDetailModal');
document.getElementById('closeRecordDetailModalBtn')?.addEventListener('click', () => {
  recordDetailModal.style.display = 'none';
});

window.openRecordModal = function(index) {
  const r = p3State.currentRecords[index];
  if (!r) return;
  
  if (p3State.currentObjectId === -1) {
    // Route stocks back to the beautiful custom modal (or layout if we wanted to later)
    // Actually user requested layout editor for standard stock pages too, so we'll route it to layout.
    // wait, if we want stock pages to use the layout builder, we should do it here!
  }
  
  const title = p3State.currentObjectId === -1 ? (r.name || r.ticker) : (r.data?.name || `Record #${r.id}`);
  const subtitle = p3State.currentObjectId === -1 ? r.ticker : p3State.currentObjectName;
  
  document.getElementById('recordDetailPageTitle').textContent = title;
  document.getElementById('recordDetailPageSubtitle').textContent = subtitle;
  
  const canvas = document.getElementById('recordDetailCanvas');
  const layout = p3State.activeLayouts && p3State.activeLayouts.length > 0 ? p3State.activeLayouts[0] : null;
  
  if (layout && layout.layout_data && layout.layout_data.sections) {
    let html = '';
    layout.layout_data.sections.forEach(sec => {
        html += `<div style="display:grid; grid-template-columns: repeat(${sec.columns}, 1fr); gap:16px; margin-bottom: 24px;">`;
        sec.items.forEach(item => {
            const cf = p3State.activeFields.find(f => f.name === item.name);
            if (!cf) return;
            const displayVal = formatFieldValue(cf, r);
            html += `
              <div class="glass-card" style="padding:16px;">
                <div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:4px;">${escapeHtml(cf.label || cf.name)}</div>
                <div style="font-size:1.1rem; font-weight:600;">${displayVal}</div>
              </div>
            `;
        });
        html += `</div>`;
    });
    canvas.innerHTML = html;
  } else {
    // Fallback Grid
    let html = '<div style="display:grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap:16px;">';
    p3State.activeFields.forEach(cf => {
      const displayVal = formatFieldValue(cf, r);
      html += `
        <div class="glass-card" style="padding:16px;">
          <div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:4px;">${escapeHtml(cf.label || cf.name)}</div>
          <div style="font-size:1.1rem; font-weight:600;">${displayVal}</div>
        </div>
      `;
    });
    html += '</div>';
    canvas.innerHTML = html;
  }
  
  setActivePage('record-detail');
};

function formatFieldValue(cf, r) {
    let val;
    if (p3State.currentObjectId === -1) {
      if (!cf.is_standard) val = (r.custom_fields && r.custom_fields[cf.name] !== undefined) ? r.custom_fields[cf.name] : null;
      else val = r[cf.name];
    } else {
      val = (r.data && r.data[cf.name] !== undefined) ? r.data[cf.name] : null;
    }
    
    let displayVal = '—';
    if (val !== null && val !== undefined) {
      if (cf.field_type === 'currency') displayVal = typeof val === 'number' ? `$${val.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})}` : val;
      else if (cf.field_type === 'percent') displayVal = typeof val === 'number' ? `${val.toFixed(2)}%` : val;
      else if (cf.field_type === 'number') displayVal = typeof val === 'number' ? val.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2}) : val;
      else displayVal = val;
    }
    return displayVal;
}

document.getElementById('backToRecordsBtn')?.addEventListener('click', () => {
    setActivePage('records');
});

// ── Custom Record Editor Logic ──
const newRecordBtn = document.getElementById('newRecordBtn');
const recordEditorModal = document.getElementById('recordEditorModal');
const closeRecordEditorBtn = document.getElementById('closeRecordEditorBtn');
const cancelRecordEditorBtn = document.getElementById('cancelRecordEditorBtn');
const saveRecordBtn = document.getElementById('saveRecordBtn');
const recordEditorForm = document.getElementById('recordEditorForm');

function closeRecordEditor() {
    recordEditorModal.style.display = 'none';
    recordEditorForm.innerHTML = '';
}

[closeRecordEditorBtn, cancelRecordEditorBtn].forEach(btn => {
    if (btn) btn.addEventListener('click', closeRecordEditor);
});

if (newRecordBtn) {
    newRecordBtn.addEventListener('click', () => {
        if (p3State.currentObjectId === -1) return; // Cannot create base stocks here

        document.getElementById('recordEditorTitle').textContent = `New ${p3State.currentObjectName} Record`;
        
        let formHtml = `
            <div class="input-wrapper" style="margin-bottom: 16px;">
                <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:6px;">Record Name (Required)</label>
                <input type="text" class="stock-input" id="recordEditorName" placeholder="e.g. My Portfolio">
            </div>
            <hr style="border:0; border-top:1px solid var(--border); margin-bottom:16px;">
        `;
        
        p3State.activeFields.forEach(cf => {
            if (cf.field_type === 'formula') return; // Formulas are computed, not inputted
            
            let inputType = 'text';
            let stepAttr = '';
            if (cf.field_type === 'number' || cf.field_type === 'currency' || cf.field_type === 'percent') {
                inputType = 'number';
                stepAttr = 'step="any"';
            }
            
            formHtml += `
                <div class="input-wrapper" style="margin-bottom: 8px;">
                    <label style="display:block; font-size:0.8rem; color:var(--text-muted); margin-bottom:6px;">${cf.label || cf.name}</label>
                    <input type="${inputType}" ${stepAttr} class="stock-input" data-field="${cf.name}" placeholder="Enter ${cf.label || cf.name}">
                </div>
            `;
        });
        
        recordEditorForm.innerHTML = formHtml;
        recordEditorModal.style.display = 'flex';
    });
}

if (saveRecordBtn) {
    saveRecordBtn.addEventListener('click', async () => {
        const nameInput = document.getElementById('recordEditorName');
        const recordName = nameInput ? nameInput.value.trim() : '';
        if (!recordName) {
            showToast('Record Name is required', 'error');
            return;
        }

        const payload = { name: recordName, data: {} };
        const inputs = recordEditorForm.querySelectorAll('input[data-field]');
        
        inputs.forEach(input => {
            const fieldName = input.getAttribute('data-field');
            let val = input.value;
            if (input.type === 'number') {
                val = val ? parseFloat(val) : null;
            }
            payload.data[fieldName] = val;
        });

        try {
            saveRecordBtn.disabled = true;
            saveRecordBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
            
            const res = await authFetch(`${API_BASE}/api/objects/${p3State.currentObjectName}/records`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            
            if (!res.ok) throw new Error('Failed to create record');
            
            showToast('Record created successfully!', 'success');
            closeRecordEditor();
            loadRecordsPage(); // Reload the table
        } catch (err) {
            showToast(err.message, 'error');
        } finally {
            saveRecordBtn.disabled = false;
            saveRecordBtn.innerHTML = '<i class="fas fa-save"></i> Save';
        }
    });
}



// ══════════════ AUTHENTICATION LOGIC ══════════════

const authOverlay = document.getElementById('authOverlay');
const authEmailInput = document.getElementById('authEmail');
const authPasswordInput = document.getElementById('authPassword');
const authMfaCodeInput = document.getElementById('authMfaCode');
const authErrorMsg = document.getElementById('authErrorMsg');

const loginFormContainer = document.getElementById('loginFormContainer');
const mfaFormContainer = document.getElementById('mfaFormContainer');
const mfaSetupContainer = document.getElementById('mfaSetupContainer');

function showAuthError(msg) {
  authErrorMsg.textContent = msg;
}

document.getElementById('loginBtn')?.addEventListener('click', async () => {
  const email = authEmailInput.value.trim();
  const password = authPasswordInput.value;
  if (!email || !password) return showAuthError('Please enter email and password');
  
  try {
    const res = await fetch(API_BASE + '/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Login failed' }));
      return showAuthError(err.detail || 'Login failed');
    }
    
    const data = await res.json();
    if (data.requires_mfa) {
      // Switch to MFA view
      loginFormContainer.style.display = 'none';
      mfaFormContainer.style.display = 'block';
      authErrorMsg.textContent = '';
      return;
    }
    
    // Success, save token
    localStorage.setItem('quant_auth_token', data.access_token);
    authOverlay.style.display = 'none';
    showToast('Logged in successfully', 'success');
    loadInitialData(); // reload everything
  } catch (err) {
    showAuthError('Connection error');
  }
});

document.getElementById('registerBtn')?.addEventListener('click', async () => {
  const email = authEmailInput.value.trim();
  const password = authPasswordInput.value;
  if (!email || !password) return showAuthError('Please enter email and password');
  
  try {
    const res = await fetch(API_BASE + '/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Registration failed' }));
      return showAuthError(err.detail || 'Registration failed');
    }
    
    const data = await res.json();
    localStorage.setItem('quant_auth_token', data.access_token);
    
    // Switch to MFA setup view automatically after registration
    loginFormContainer.style.display = 'none';
    authErrorMsg.textContent = '';
    
    // Fetch MFA setup QR
    const setupRes = await fetch(API_BASE + '/api/auth/mfa/setup', {
      headers: { 'Authorization': Bearer  }
    });
    if (setupRes.ok) {
      const setupData = await setupRes.json();
      document.getElementById('mfaQrCode').innerHTML = setupData.qr_svg;
      document.getElementById('mfaSecretText').textContent = setupData.secret;
      mfaSetupContainer.style.display = 'block';
    } else {
      authOverlay.style.display = 'none';
      loadInitialData();
    }
  } catch (err) {
    showAuthError('Connection error');
  }
});

document.getElementById('verifyMfaBtn')?.addEventListener('click', async () => {
  const email = authEmailInput.value.trim();
  const code = authMfaCodeInput.value.trim();
  if (!code) return showAuthError('Enter MFA code');
  
  try {
    const res = await fetch(API_BASE + '/api/auth/verify-mfa', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, code })
    });
    
    if (!res.ok) {
      return showAuthError('Invalid MFA code');
    }
    
    const data = await res.json();
    localStorage.setItem('quant_auth_token', data.access_token);
    authOverlay.style.display = 'none';
    showToast('Logged in successfully', 'success');
    loadInitialData();
  } catch (err) {
    showAuthError('Connection error');
  }
});

document.getElementById('cancelMfaBtn')?.addEventListener('click', () => {
  mfaFormContainer.style.display = 'none';
  loginFormContainer.style.display = 'block';
  authMfaCodeInput.value = '';
});

document.getElementById('mfaSetupDoneBtn')?.addEventListener('click', () => {
  mfaSetupContainer.style.display = 'none';
  authOverlay.style.display = 'none';
  showToast('Account created and MFA enabled', 'success');
  loadInitialData();
});

// Check auth on load
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  setupEventListeners();
  const token = localStorage.getItem('quant_auth_token');
  if (!token) {
    authOverlay.style.display = 'flex';
  }
});


// ══════════════ ACCOUNT & LOGOUT LOGIC ══════════════

const accountModal = document.getElementById('accountModal');
const navAccount = document.getElementById('nav-account');
const closeAccountModalBtn = document.getElementById('closeAccountModalBtn');
const logoutBtn = document.getElementById('logoutBtn');

navAccount?.addEventListener('click', async () => {
  accountModal.style.display = 'flex';
  
  // Fetch user details
  try {
    const data = await api('/api/auth/me');
    document.getElementById('accountEmailDisplay').textContent = data.email;
    
    const mfaText = document.getElementById('mfaStatusText');
    const mfaIcon = document.getElementById('mfaStatusIcon');
    
    if (data.mfa_enabled) {
      mfaText.textContent = 'MFA Enabled';
      mfaText.style.color = 'var(--success)';
      mfaIcon.style.color = 'var(--success)';
      mfaIcon.className = 'fas fa-shield-check';
    } else {
      mfaText.textContent = 'MFA Disabled';
      mfaText.style.color = 'var(--warning)';
      mfaIcon.style.color = 'var(--warning)';
      mfaIcon.className = 'fas fa-exclamation-triangle';
    }
  } catch (err) {
    document.getElementById('accountEmailDisplay').textContent = 'Error loading profile';
  }
});

closeAccountModalBtn?.addEventListener('click', () => {
  accountModal.style.display = 'none';
});

logoutBtn?.addEventListener('click', () => {
  localStorage.removeItem('quant_auth_token');
  location.reload();
});

// ══════════════════════════════════════════════════════════════════════════════
// THEME HANDLING
// ══════════════════════════════════════════════════════════════════════════════
function initTheme() {
  const savedTheme = localStorage.getItem("app_theme") || "dark";
  document.body.setAttribute("data-theme", savedTheme);
}

// ── Helpers ──
window.escapeHtml = function(str) {
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
};
