// Dashboard / metrics / coverage / actions / usage / models view.
//
// All functions are pure renderers that take a context bag with the bits of
// app state they need (`t`, current model, ...). No globals.
//
// Usage:
//   import { initDashboard } from './dashboard.js';
//   const dash = initDashboard({ t, pct, getCurrentModel, getModels,
//                                applyModel, onModelChange });
//   document.getElementById('metrics-refresh').addEventListener('click', dash.loadMetrics);
//   dash.loadUsage();

export function initDashboard(ctx) {
  const { t, pct, getCurrentModel, getModels, applyModel, onModelChange } = ctx;

  // ---- metrics + coverage ----

  async function loadMetrics() {
    const body = document.getElementById('metrics-body');
    body.innerHTML = '<div style="color:var(--muted)">…</div>';
    const w = document.getElementById('metrics-window').value;
    const url = '/agent/metrics' + (w ? `?window=${w}` : '');
    try {
      const r = await fetch(url);
      const d = await r.json();
      const cards = [
        { lbl: 'Total actions', val: d.total, sub: `${d.ok} ok / ${d.fail} fail` },
        { lbl: 'Success rate', val: pct(d.success_rate), sub: '' },
        { lbl: 'Sessions', val: d.sessions, sub: '' },
        { lbl: 'fs_write → verify', val: pct(d.verify_ratio),
          sub: `${d.writes_verified} / ${d.writes} writes` },
        { lbl: 'Rollbacks', val: d.rollbacks, sub: '' },
        { lbl: 'Hook denies', val: d.hook_denies, sub: '' },
      ];
      let html = '<div class="metric-grid">' + cards.map(c =>
        `<div class="metric-card"><div class="lbl">${c.lbl}</div>` +
        `<div class="val">${c.val ?? '—'}</div>` +
        `<div class="sub">${c.sub}</div></div>`).join('') + '</div>';
      html += '<h3 style="margin:24px 0 8px;font-size:14px;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px">По инструментам</h3>';
      html += '<table class="metric-table"><thead><tr><th>Tool</th><th>Calls</th><th>OK</th><th>Success</th><th></th></tr></thead><tbody>';
      for (const tt of (d.by_tool || [])) {
        const sr = tt.success_rate;
        const w = sr != null ? Math.round(sr * 100) : 0;
        html += `<tr><td><code>${tt.tool}</code></td><td>${tt.count}</td><td>${tt.ok}</td><td>${pct(sr)}</td><td style="width:120px"><div class="metric-bar"><div style="width:${w}%"></div></div></td></tr>`;
      }
      html += '</tbody></table>';
      if (d.top_errors && d.top_errors.length) {
        html += '<h3 style="margin:24px 0 8px;font-size:14px;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px">Топ ошибок</h3>';
        html += '<table class="metric-table"><thead><tr><th>Tool</th><th>Errors</th></tr></thead><tbody>';
        for (const e of d.top_errors) {
          html += `<tr><td><code>${e.tool}</code></td><td>${e.count}</td></tr>`;
        }
        html += '</tbody></table>';
      }
      body.innerHTML = html;
      loadCoverage();
    } catch (e) {
      body.innerHTML = `<div style="color:#e57373">${e.message || e}</div>`;
    }
  }

  async function loadCoverage() {
    const body = document.getElementById('metrics-body');
    try {
      const r = await fetch('/agent/coverage');
      const d = await r.json();
      let html = '<h3 style="margin:24px 0 8px;font-size:14px;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;display:flex;align-items:center;gap:10px">Покрытие тестами <button id="cov-refresh" class="sq-btn" style="padding:2px 8px;font-size:11px;text-transform:none;letter-spacing:0">прогнать pytest --cov</button></h3>';
      if (!d.ok) {
        html += `<div style="color:#e57373">${d.error || 'нет данных'}</div>`;
        body.insertAdjacentHTML('beforeend', html);
        document.getElementById('cov-refresh')?.addEventListener('click', runCoverage);
        return;
      }
      const age = d.age_seconds < 60 ? `${d.age_seconds}s` : d.age_seconds < 3600 ? `${Math.floor(d.age_seconds/60)}m` : `${Math.floor(d.age_seconds/3600)}h`;
      const totalColor = d.total_percent >= 70 ? '#4caf50' : d.total_percent >= 40 ? '#ff9800' : '#e57373';
      html += `<div style="display:flex;gap:18px;align-items:baseline;margin-bottom:12px">`
        + `<div><span style="font-size:28px;font-weight:600;color:${totalColor}">${d.total_percent}%</span> <span style="color:var(--muted);font-size:12px">общее покрытие</span></div>`
        + `<div style="color:var(--muted);font-size:12px">${d.total_covered} / ${d.total_statements} строк • обновлено ${age} назад</div>`
        + `</div>`;
      html += '<table class="metric-table"><thead><tr><th>Файл</th><th>Строк</th><th>Не покрыто</th><th>%</th><th></th></tr></thead><tbody>';
      for (const f of d.files) {
        const c = f.percent >= 70 ? '#4caf50' : f.percent >= 40 ? '#ff9800' : '#e57373';
        const w = Math.round(f.percent);
        html += `<tr><td><code>${f.path}</code></td><td>${f.statements}</td><td>${f.missing}</td><td style="color:${c};font-weight:600">${f.percent}%</td><td style="width:120px"><div class="metric-bar"><div style="width:${w}%;background:${c}"></div></div></td></tr>`;
      }
      html += '</tbody></table>';
      body.insertAdjacentHTML('beforeend', html);
      document.getElementById('cov-refresh')?.addEventListener('click', runCoverage);
    } catch (e) {
      body.insertAdjacentHTML('beforeend', `<div style="color:#e57373">coverage: ${e.message || e}</div>`);
    }
  }

  async function runCoverage() {
    const btn = document.getElementById('cov-refresh');
    if (btn) { btn.disabled = true; btn.textContent = 'запускаю pytest …'; }
    try {
      const r = await fetch('/agent/coverage/run', { method: 'POST' });
      const d = await r.json();
      if (!d.ok) {
        alert('coverage run failed: ' + (d.error || d.returncode));
      }
      await loadMetrics();
    } catch (e) {
      alert('coverage run error: ' + e.message);
    }
  }

  // ---- actions log ----

  async function loadActions() {
    const list = document.getElementById('actions-list');
    list.innerHTML = '<div style="color:var(--muted)">…</div>';
    const failOnly = document.getElementById('act-failed-only').checked;
    try {
      const r = await fetch('/agent/actions?limit=300');
      const d = await r.json();
      let acts = d.actions || [];
      if (failOnly) acts = acts.filter(a => !a.ok);
      if (!acts.length) {
        list.innerHTML = `<div style="color:var(--muted)">${t('actions_empty')}</div>`;
        return;
      }
      list.innerHTML = '';
      for (const a of acts) {
        const row = document.createElement('div');
        row.className = 'act-row ' + (a.ok ? 'ok' : 'fail');
        const ts = new Date(a.ts * 1000).toLocaleString();
        let summary = a.file || '';
        if (!summary) {
          try { const j = JSON.parse(a.args); summary = j.command || j.path || j.pattern || (j.name||''); } catch {}
        }
        if (a.error) summary = a.error;
        const canRollback = !!a.backup && !!a.file;
        row.innerHTML = `
          <span class="act-ts">${ts}</span>
          <span class="act-tool">${a.tool}</span>
          <span class="act-summary" title="${(summary||'').replace(/"/g,'&quot;')}">${summary || ''}</span>
          <button ${canRollback?'':'disabled'} data-id="${a.id}">${t('actions_rollback')}</button>`;
        row.querySelector('button').addEventListener('click', async () => {
          if (!confirm(`Rollback ${a.file} ← ${a.backup}?`)) return;
          const rr = await fetch(`/agent/actions/${a.id}/rollback`, { method: 'POST' });
          const dd = await rr.json();
          alert(rr.ok ? `OK: ${dd.restored}` : `FAIL: ${dd.detail||JSON.stringify(dd)}`);
          loadActions();
        });
        list.appendChild(row);
      }
    } catch (e) {
      list.innerHTML = `<div style="color:#ef4444">${e}</div>`;
    }
  }

  // ---- models grid ----

  function renderModelsView() {
    const grid = document.getElementById('models-grid');
    if (!grid) return;
    const cur = getCurrentModel();
    grid.innerHTML = '';
    for (const m of getModels()) {
      const tier = m.tier || 'sonnet';
      const card = document.createElement('div');
      card.className = 'model-card tier-' + tier;
      const tags = (m.strengths || []).map(s => `<span class="mc-tag">${s}</span>`).join('');
      const mult = (m.multiplier != null) ? `×${m.multiplier}` : '';
      const isActive = m.id === cur;
      const btnLabel = isActive ? (t('model_active') || 'Активна') : (t('model_choose') || 'Выбрать');
      card.innerHTML = `
        <div class="mc-head">
          <div>
            <div class="mc-title">${m.label}</div>
            <div class="mc-provider">${m.provider || ''}</div>
          </div>
          <span class="mc-mult">${mult}</span>
        </div>
        <div class="mc-desc">${m.description || ''}</div>
        <div class="mc-tags">${tags}</div>
        <div class="mc-actions"><button class="${isActive?'active':''}" ${isActive?'disabled':''}>${btnLabel}</button></div>`;
      card.querySelector('button').addEventListener('click', () => {
        if (m.id === getCurrentModel()) return;
        applyModel(m.id);
        if (onModelChange) onModelChange(m.id);
        renderModelsView();
      });
      grid.appendChild(card);
    }
  }

  // ---- usage / plan card ----

  function fmtDate(ts, lang) {
    if (!ts) return '—';
    const d = new Date(ts * 1000);
    const days = Math.max(0, Math.ceil((d - Date.now()) / 86400000));
    const dateStr = d.toLocaleDateString(lang === 'ru' ? 'ru-RU' : 'en-US', { day: '2-digit', month: 'short' });
    return `${dateStr} · ${t('dash_in_days').replace('{n}', days)}`;
  }

  async function loadUsage(lang = 'ru') {
    const used = document.getElementById('u-used');
    const limit = document.getElementById('u-limit');
    const rem = document.getElementById('u-remaining');
    const bar = document.getElementById('u-bar');
    const pill = document.getElementById('u-plan-pill');
    const ptype = document.getElementById('u-plan-type');
    const reset = document.getElementById('u-reset');
    const overage = document.getElementById('u-overage');
    const orate = document.getElementById('u-overage-rate');
    rem.textContent = t('dash_loading');
    try {
      const r = await fetch('/usage');
      const d = await r.json();
      if (!r.ok || d.error) {
        rem.textContent = (d.error || ('HTTP ' + r.status)).slice(0, 200);
        return;
      }
      const u = Number(d.used) || 0;
      const lim = Number(d.limit) || 0;
      used.textContent = u.toFixed(2);
      limit.textContent = lim.toFixed(0) + ' ' + (d.unit || '');
      const left = Math.max(0, lim - u);
      rem.textContent = `${t('dash_remaining')}: ${left.toFixed(2)}`;
      bar.style.width = lim > 0 ? Math.min(100, (u / lim) * 100).toFixed(1) + '%' : '0';
      const isPro = /PRO/i.test(d.plan || '') || /PRO/i.test(d.plan_type || '');
      pill.textContent = d.plan || '—';
      pill.className = 'pill ' + (isPro ? 'pro' : 'free');
      ptype.textContent = d.plan_type || '—';
      reset.textContent = fmtDate(d.reset_at, lang);
      const ov = Number(d.overage) || 0;
      const ovCap = Number(d.overage_cap) || 0;
      const ovStatus = (d.overage_status || '').toUpperCase();
      overage.textContent = ovStatus === 'DISABLED' ? 'off' : `${ov.toFixed(2)} / ${ovCap.toFixed(0)}`;
      const rate = Number(d.overage_rate) || 0;
      orate.textContent = rate > 0 ? `$${rate.toFixed(2)} / credit` : '—';
    } catch (e) {
      rem.textContent = String(e).slice(0, 200);
    }
  }

  return { loadMetrics, loadCoverage, runCoverage, loadActions, renderModelsView, loadUsage };
}
