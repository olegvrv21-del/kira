// experiments.js - render ~/notebook/experiments.tsv as a sortable table.
//
// Backend: GET /agent/experiments returns
//   {ok:true, rows:[{ts,tag,idea,pr,status,ci,tests_after,notes}], count, ...}
// or {ok:false, reason:"missing"|"empty"|...}
//
// Pure DOM, no framework. Loaded by app.js when the Experiments tab is opened.

const REPO_URL = 'https://github.com/olegvrv21-del/kira/pull/';
const COLS = [
  { key: 'ts',          label: 'ts',     sortable: true,  width: '170px' },
  { key: 'tag',         label: 'tag',    sortable: true,  width: '110px' },
  { key: 'idea',        label: 'idea',   sortable: true,  cls: 'exp-idea' },
  { key: 'pr',          label: 'pr',     sortable: true,  width: '70px' },
  { key: 'status',      label: 'status', sortable: true,  width: '90px' },
  { key: 'ci',          label: 'ci',     sortable: true,  width: '80px' },
  { key: 'tests_after', label: 'tests',  sortable: true,  width: '70px' },
  { key: 'notes',       label: 'notes',  sortable: false, cls: 'exp-notes' },
];

export function initExperiments() {
  let cache = [];        // last loaded rows
  let sortKey = 'ts';
  let sortDesc = true;   // newest first by default

  const tableEl   = document.getElementById('experiments-table');
  const summaryEl = document.getElementById('experiments-summary');
  const emptyEl   = document.getElementById('experiments-empty');
  const filterEl  = document.getElementById('exp-filter');
  const statusEl  = document.getElementById('exp-status');
  const refreshEl = document.getElementById('exp-refresh');

  function pill(value) {
    if (!value) return '';
    const v = String(value).toLowerCase();
    return `<span class="exp-pill ${v}">${escapeHtml(value)}</span>`;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function renderHeader() {
    const thead = document.createElement('thead');
    const tr = document.createElement('tr');
    COLS.forEach(c => {
      const th = document.createElement('th');
      if (c.width) th.style.width = c.width;
      let label = c.label;
      if (c.sortable && c.key === sortKey) label += sortDesc ? ' ↓' : ' ↑';
      th.textContent = label;
      if (c.sortable) {
        th.addEventListener('click', () => {
          if (sortKey === c.key) {
            sortDesc = !sortDesc;
          } else {
            sortKey = c.key;
            sortDesc = c.key === 'ts' || c.key === 'pr';  // numeric/time desc by default
          }
          render();
        });
      } else {
        th.style.cursor = 'default';
      }
      tr.appendChild(th);
    });
    thead.appendChild(tr);
    return thead;
  }

  function renderRow(row) {
    const tr = document.createElement('tr');
    COLS.forEach(c => {
      const td = document.createElement('td');
      if (c.cls) td.className = c.cls;
      const v = row[c.key] || '';
      if (c.key === 'status' || c.key === 'ci') {
        td.innerHTML = pill(v);
      } else if (c.key === 'pr' && v) {
        const n = String(v).trim();
        if (/^\d+$/.test(n)) {
          td.innerHTML = `<a class="exp-pr-link" href="${REPO_URL}${n}" target="_blank" rel="noopener">#${n}</a>`;
        } else {
          td.textContent = v;
        }
      } else if (c.key === 'ts') {
        // Render compact timestamp; keep full ISO in title
        td.textContent = formatTs(v);
        td.title = v;
      } else {
        td.textContent = v;
      }
      tr.appendChild(td);
    });
    return tr;
  }

  function formatTs(s) {
    // Input is ISO-ish (YYYY-MM-DDTHH:MM:SSZ). Show "05-14 14:20".
    const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
    return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}` : s;
  }

  function applyFilters(rows) {
    const q = filterEl.value.trim().toLowerCase();
    const st = statusEl.value;
    return rows.filter(r => {
      if (st && (r.status || '').toLowerCase() !== st) return false;
      if (!q) return true;
      return [r.tag, r.idea, r.notes, r.ci, r.status]
        .some(v => (v || '').toLowerCase().includes(q));
    });
  }

  function sortRows(rows) {
    const k = sortKey;
    const dir = sortDesc ? -1 : 1;
    return rows.slice().sort((a, b) => {
      const av = a[k] || '';
      const bv = b[k] || '';
      // numeric for pr/tests_after
      if (k === 'pr' || k === 'tests_after') {
        const an = parseInt(av, 10);
        const bn = parseInt(bv, 10);
        const anOk = !Number.isNaN(an);
        const bnOk = !Number.isNaN(bn);
        if (anOk && bnOk) return (an - bn) * dir;
        if (anOk) return -1 * dir;
        if (bnOk) return 1 * dir;
      }
      return av.localeCompare(bv) * dir;
    });
  }

  function summarize(rows) {
    const counts = { green: 0, red: 0, opened: 0, timeout: 0, other: 0 };
    rows.forEach(r => {
      const s = (r.status || '').toLowerCase();
      if (counts[s] !== undefined) counts[s]++;
      else if (s) counts.other++;
    });
    const parts = [`всего: ${rows.length}`];
    if (counts.green)   parts.push(`green: ${counts.green}`);
    if (counts.red)     parts.push(`red: ${counts.red}`);
    if (counts.opened)  parts.push(`opened: ${counts.opened}`);
    if (counts.timeout) parts.push(`timeout: ${counts.timeout}`);
    if (counts.other)   parts.push(`other: ${counts.other}`);
    return parts.join(' · ');
  }

  function render() {
    const filtered = applyFilters(cache);
    const sorted = sortRows(filtered);
    summaryEl.textContent = summarize(filtered);
    tableEl.innerHTML = '';
    tableEl.appendChild(renderHeader());
    const tbody = document.createElement('tbody');
    sorted.forEach(r => tbody.appendChild(renderRow(r)));
    tableEl.appendChild(tbody);
    emptyEl.style.display = sorted.length ? 'none' : 'block';
  }

  async function load() {
    summaryEl.textContent = 'загрузка…';
    try {
      const r = await fetch('/agent/experiments');
      const j = await r.json();
      if (!j.ok) {
        cache = [];
        const reasons = {
          missing:  'experiments.tsv не найден — создастся при первом запуске autoresearch',
          empty:    'experiments.tsv пуст',
          too_large:'experiments.tsv слишком большой (>1 MiB) — пора архивировать',
        };
        summaryEl.textContent = reasons[j.reason] || `не удалось загрузить: ${j.reason}`;
        tableEl.innerHTML = '';
        emptyEl.style.display = 'block';
        return;
      }
      cache = j.rows || [];
      if (j.truncated) {
        summaryEl.textContent = `показано ${cache.length} строк (TSV обрезан до лимита)`;
      }
      render();
    } catch (err) {
      cache = [];
      summaryEl.textContent = `ошибка сети: ${err}`;
      tableEl.innerHTML = '';
    }
  }

  if (filterEl)  filterEl.addEventListener('input', render);
  if (statusEl)  statusEl.addEventListener('change', render);
  if (refreshEl) refreshEl.addEventListener('click', load);

  return { load };
}
