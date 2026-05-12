// Agent-session list + budget + restore.
//
// Phase 4b split out of app.js. Owns the *server-side* agent sessions
// (GET /agent/sessions) — distinct from the local "chat" list still in
// app.js. The module keeps `agentSessions` as closure state.
//
// ctx shape:
//   t                  i18n translator                 (function)
//   getLang            () => 'ru' | 'en'               (function)
//   getSearchQuery     () => string lowercased         (function)
//   dom: {
//     chatListEl       sidebar list container         (HTMLElement)
//     messagesEl       transcript container           (HTMLElement)
//     modelBtn         the model button (pin class)   (HTMLElement)
//     budgetEl         <div id="agent-budget">        (HTMLElement)
//   }
//   state: {
//     getAgentMode        () => bool                  (function)
//     getAgentSessionId   () => str | null            (function)
//     setAgentSessionId   (sid) => void               (function)
//     getModels           () => allModels[]           (function)
//   }
//   fns: {
//     applyModel(id, opts)
//     renderPlan(plan)
//     clearPlan()
//     addMsg(role, text, files, opts?)
//     addAgentToolCard(toolUseId, name)
//     attachDiff(card, diff, lines, actionId, filePath)
//     attachRollbackOnly(card, actionId)
//     fetchActionDiff(actionId)
//   }
//
// Returns: { loadAgentSessions, refreshAgentBudget, renderAgentSessionList,
//            loadAgentSession, newAgentSession }
//
// Side effect: every list render also calls refreshAgentBudget so the
// header stays in sync with the active sid.

export function createAgentSessions(ctx) {
  const { t, getLang, getSearchQuery, dom, state, fns } = ctx;
  const { chatListEl, messagesEl, modelBtn, budgetEl } = dom;
  const { getAgentMode, getAgentSessionId, setAgentSessionId, getModels } = state;
  const { applyModel, renderPlan, clearPlan, addMsg,
          addAgentToolCard, attachDiff, attachRollbackOnly, fetchActionDiff } = fns;

  let agentSessions = [];

  async function loadAgentSessions() {
    try {
      const r = await fetch('/agent/sessions');
      const d = await r.json();
      agentSessions = d.sessions || [];
    } catch { agentSessions = []; }
    renderAgentSessionList();
  }

  async function refreshAgentBudget() {
    const el = budgetEl;
    if (!getAgentMode()) { el.style.display = 'none'; return; }
    try {
      const sid = getAgentSessionId();
      const url = '/agent/limits' + (sid ? ('?session_id=' + sid) : '');
      const r = await fetch(url); const d = await r.json();
      const sLim = d.session_limit > 0 ? '/' + d.session_limit.toFixed(0) : '';
      const dLim = d.day_limit > 0 ? '/' + d.day_limit.toFixed(0) : '';
      const mLim = d.month_limit > 0 ? '/' + d.month_limit.toFixed(0) : '';
      const sess = d.session_credits.toFixed(2);
      const day = d.day_credits.toFixed(2);
      const month = (d.month_credits || 0).toFixed(2);
      const ru = (getLang() === 'ru');
      el.textContent = ru
        ? `· сессия ${sess}${sLim} · день ${day}${dLim} · месяц ${month}${mLim}`
        : `· sess ${sess}${sLim} · day ${day}${dLim} · mo ${month}${mLim}`;
      const sPct = d.session_limit > 0 ? d.session_credits / d.session_limit : 0;
      const dPct = d.day_limit > 0 ? d.day_credits / d.day_limit : 0;
      const mPct = d.month_limit > 0 ? d.month_credits / d.month_limit : 0;
      const max = Math.max(sPct, dPct, mPct);
      el.style.color = max > 0.9 ? '#e74c3c' : (max > 0.7 ? '#e6a23c' : '#888');
      el.style.display = '';
    } catch { el.style.display = 'none'; }
  }

  function renderAgentSessionList() {
    chatListEl.innerHTML = '';
    const q = getSearchQuery();
    let list = agentSessions;
    if (q) {
      list = list.filter(s => (s.title || s.sid).toLowerCase().includes(q));
    }
    if (!list.length) {
      chatListEl.innerHTML = `<div class="chat-empty">${t('empty_history')}</div>`;
      return;
    }
    const activeSid = getAgentSessionId();
    const lang = getLang();
    for (const s of list) {
      const it = document.createElement('div');
      it.className = 'chat-item' + (s.sid === activeSid ? ' active' : '');
      const title = s.title || s.sid.slice(0, 8);
      const left = document.createElement('span');
      left.style.flex = '1'; left.style.overflow = 'hidden';
      left.style.textOverflow = 'ellipsis'; left.style.whiteSpace = 'nowrap';
      left.textContent = title;
      it.title = title + '  ·  ' + s.sid;
      const del = document.createElement('button');
      del.type = 'button';
      del.textContent = '×';
      del.style.cssText = 'background:none;border:0;color:#888;cursor:pointer;font-size:18px;line-height:1;padding:0 4px;margin-left:4px;';
      del.title = lang === 'ru' ? 'Удалить' : 'Delete';
      del.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm((lang === 'ru' ? 'Удалить сессию ' : 'Delete session ') + title + '?')) return;
        await fetch('/agent/sessions/' + s.sid, { method: 'DELETE' });
        if (getAgentSessionId() === s.sid) {
          setAgentSessionId(null);
          messagesEl.innerHTML = `<div class="empty">${t('agent_hint')}</div>`;
        }
        loadAgentSessions();
      });
      it.style.display = 'flex'; it.style.alignItems = 'center';
      it.appendChild(left); it.appendChild(del);
      left.addEventListener('click', () => loadAgentSession(s.sid));
      left.addEventListener('dblclick', async (e) => {
        e.stopPropagation();
        const cur = s.title || s.sid;
        const nt = prompt(lang === 'ru' ? 'Новый заголовок:' : 'New title:', cur);
        if (!nt || nt === cur) return;
        await fetch('/agent/sessions/' + s.sid + '/rename', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: nt }),
        });
        loadAgentSessions();
      });
      left.style.cursor = 'pointer';
      left.title = (lang === 'ru' ? '2× клик — переименовать' : 'dblclick to rename') + ' · ' + s.sid;
      chatListEl.appendChild(it);
    }
    refreshAgentBudget();
  }

  async function loadAgentSession(sid) {
    try {
      const r = await fetch('/agent/sessions/' + sid);
      if (!r.ok) return;
      const d = await r.json();
      setAgentSessionId(sid);
      const allModels = getModels();
      if (d.model && allModels.some(m => m.id.replace(/^q\//,'') === d.model || m.id === d.model)) {
        // saved model is bare id like 'claude-opus-4.7'; map back to 'q/...'
        const full = allModels.find(m => m.id.replace(/^q\//,'') === d.model || m.id === d.model);
        if (full) applyModel(full.id, { pinned: true, persistGlobal: false });
      } else {
        modelBtn.classList.remove('pinned');
      }
      messagesEl.innerHTML = '';
      for (const m of (d.transcript || [])) {
        if (m.role === 'tool') {
          const card = addAgentToolCard(m.id || ('h' + Math.random()), m.name || '?');
          card.querySelector('.tool-input').textContent = JSON.stringify(m.input || {}, null, 2);
          let sum = (m.input?.path || m.input?.command || m.input?.pattern || '').toString();
          if (m.name === 'use_subagent') {
            const subs = m.input?.content?.subagents || [];
            sum = m.input?.command === 'ListAgents' ? 'ListAgents' : `${subs.length} ✕ ${t('agent_tool')}`;
          }
          card.querySelector('.tool-summary').textContent = sum.slice(0, 80);
          const st = m.status === 'error' ? 'error' : 'success';
          card.classList.remove('running');
          card.classList.add(st);
          card.querySelector('.tool-status').textContent = st === 'success' ? t('agent_success') : t('agent_error');
          if (m.output != null) {
            card.querySelector('.tool-output').textContent = (m.output || '').slice(0, 8000);
            card.querySelector('.out-wrap').style.display = '';
          }
          // Lazy-load diff for fs_write actions on session restore.
          if (m.action_id && m.name === 'fs_write') {
            (async () => {
              const a = await fetchActionDiff(m.action_id);
              if (a && a.diff) {
                attachDiff(card, a.diff, (a.diff.match(/^[+-][^+-]/gm) || []).length,
                           a.id, a.file || (m.input && m.input.path) || '');
              } else if (a && a.backup) {
                attachRollbackOnly(card, a.id);
              }
            })();
          }
          if (m.name === 'use_subagent' && Array.isArray(m.subagents) && m.subagents.length) {
            const list = document.createElement('div');
            list.className = 'subagent-list';
            list.innerHTML = m.subagents.map(sa => `
              <div class="subagent-item ${sa.status}">
                <span class="sa-spinner">${sa.status === 'success' ? '●' : '✕'}</span>
                <span class="sa-query"></span>
                <span class="sa-preview" style="color:var(--muted);font-size:11px"></span>
              </div>`).join('');
            const nodes = list.querySelectorAll('.subagent-item');
            m.subagents.forEach((sa, i) => {
              nodes[i].querySelector('.sa-query').textContent = (sa.query || '').slice(0, 120);
              nodes[i].querySelector('.sa-preview').textContent = ' – ' + (sa.preview || '').slice(0, 160);
            });
            card.appendChild(list);
          }
        } else {
          addMsg(m.role, m.text, null);
        }
      }
      if (!d.transcript || !d.transcript.length) {
        messagesEl.innerHTML = `<div class="empty">${t('agent_hint')}</div>`;
      }
      renderPlan(d.plan || {items:[]});
      renderAgentSessionList();
    } catch (e) { console.error(e); }
  }

  function newAgentSession() {
    setAgentSessionId(null);
    modelBtn.classList.remove('pinned');
    messagesEl.innerHTML = `<div class="empty">${t('agent_hint')}</div>`;
    clearPlan();
    renderAgentSessionList();
    refreshAgentBudget();
  }

  return { loadAgentSessions, refreshAgentBudget, renderAgentSessionList,
           loadAgentSession, newAgentSession };
}
