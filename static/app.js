// Kira webchat entry module. Pulls in side-effect-free helpers from focused
// modules and keeps the (still large) state machine here. Future passes will
// extract more: sessions persistence, plan rendering, SSE plumbing.
import { installFetchInterceptor } from './auth.js';
import { I18N } from './i18n.js';
import {
  pct,
  fmtSize,
  makeId,
  fileToDataUrl,
  copyToClipboard,
  downloadFile,
  safeFilename,
} from './utils.js';
import { renderMarkdown } from './markdown.js';
import { renderPlan as _renderPlan, clearPlan as _clearPlan } from './plan.js';
import { initSkills } from './skills.js';
import { initDashboard } from './dashboard.js';
import { createAgentRunner } from './agent_sse.js';
import { createToolCards } from './tool_cards.js';
import { createAgentSessions } from './sessions.js';
import { createComposer } from './composer.js';
import { createExporters } from './exporters.js';
import { isNetworkError, waitForConnection } from './net_status.js';

installFetchInterceptor();

    const LS_LANG = 'kira_lang';
    let lang = localStorage.getItem(LS_LANG) || (navigator.language?.startsWith('en') ? 'en' : 'ru');
    const t = (k) => (I18N[lang] && I18N[lang][k]) || I18N.ru[k] || k;

    function applyI18n() {
      document.documentElement.lang = lang;
      document.querySelectorAll('[data-i18n]').forEach(el => {
        el.textContent = t(el.getAttribute('data-i18n'));
      });
      input.placeholder = t('placeholder');
      document.getElementById('attach').title = t('attach_title');
      document.getElementById('send').title = t('send_title');
      document.getElementById('menu-btn').title = t('menu_title');
      document.getElementById('model-btn').title = t('model_title');
      // keep current chat title in sync if it's the placeholder
      const c = chats.find(x => x.id === activeChatId);
      if (c && (!c.history || !c.history.length)) chatTitleEl.textContent = t('new_chat');
      // re-render dynamic empty states
      if (!history.length) {
        const empty = messagesEl.querySelector('.empty');
        if (empty) empty.textContent = t('empty_chat');
      }
      if (!chats.length) {
        const empty = chatListEl.querySelector('.chat-empty');
        if (empty) empty.textContent = t('empty_history');
      }
      // model label keeps model name; only fallback localized
      if (!currentModel) modelLabel.textContent = t('model_label');
      // active state on toggle
      document.querySelectorAll('#lang-toggle button').forEach(b =>
        b.classList.toggle('active', b.dataset.lang === lang));
    }

    /* refs */
    const messagesEl = document.getElementById('messages');
    const input = document.getElementById('input');
    const form = document.getElementById('form');
    const sendBtn = document.getElementById('send');
    const attachBtn = document.getElementById('attach');
    const fileInput = document.getElementById('file');
    const attachmentsEl = document.getElementById('attachments');
    const modelBtn = document.getElementById('model-btn');
    const modelLabel = document.getElementById('model-label');
    const menu = document.getElementById('menu');
    const menuBtn = document.getElementById('menu-btn');
    const newChatBtn = document.getElementById('new-chat');
    const chatListEl = document.getElementById('chat-list');
    const chatTitleEl = document.getElementById('chat-title');

    /* state */
    const LS_MODEL = 'kira_model';
    const LS_CHATS = 'kira_chats_v1';
    const LS_DRAWER = 'kira_drawer_open';
    let history = [];
    let streaming = false;
    let currentModel = null;
    let allModels = [];
    let chats = [];
    let activeChatId = null;

    /* drawer push */
    function openDrawer() { document.body.classList.add('drawer-open'); localStorage.setItem(LS_DRAWER, '1'); }
    function closeDrawer() { document.body.classList.remove('drawer-open'); localStorage.setItem(LS_DRAWER, '0'); }
    function toggleDrawer() { document.body.classList.contains('drawer-open') ? closeDrawer() : openDrawer(); }
    menuBtn.addEventListener('click', toggleDrawer);
    // Close drawer when tapping the dim overlay (mobile only)
    document.addEventListener('click', (e) => {
      if (!document.body.classList.contains('drawer-open')) return;
      if (window.innerWidth > 720) return;
      const drawer = document.getElementById('drawer');
      if (drawer.contains(e.target) || e.target === menuBtn) return;
      closeDrawer();
    });
    if (localStorage.getItem(LS_DRAWER) !== '0' && window.innerWidth > 720) openDrawer();
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });
    // Auto-close drawer on mobile after selecting any nav item / chat / action
    document.getElementById('drawer').addEventListener('click', (e) => {
      if (window.innerWidth > 720) return;
      if (!document.body.classList.contains('drawer-open')) return;
      const t = e.target.closest('[data-nav], .chat-item, .session-item, #new-chat');
      if (!t) return;
      // small delay so the original click handler runs first
      setTimeout(closeDrawer, 0);
    });

    /* lang toggle */
    document.querySelectorAll('#lang-toggle button').forEach(b => {
      b.addEventListener('click', () => {
        lang = b.dataset.lang;
        localStorage.setItem(LS_LANG, lang);
        applyI18n();
        if (document.getElementById('dashboard').style.display === 'block') loadUsage();
      });
    });

    /* models */
    function applyModel(id, opts = {}) {
      const m = allModels.find(x => x.id === id);
      if (!m) return;
      currentModel = id;
      modelLabel.textContent = m.label;
      if (opts.persistGlobal !== false) localStorage.setItem(LS_MODEL, id);
      modelBtn.classList.toggle('pinned', !!opts.pinned);
      renderMenu();
    }
    function renderMenu() {
      menu.innerHTML = '';
      for (const m of allModels) {
        const item = document.createElement('div');
        item.className = 'menu-item' + (m.id === currentModel ? ' active' : '');
        item.innerHTML = `<svg class="check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg><span>${m.label}</span>`;
        item.addEventListener('click', () => {
          // If a response is streaming, abort it before switching model.
          if (streaming) {
            if (agentMode && agentSessionId) {
              fetch('/agent/stop/' + agentSessionId, { method: 'POST' }).catch(() => {});
            }
            if (agentAbort) { try { agentAbort.abort(); } catch {} }
          }
          applyModel(m.id, { pinned: false });
          // pin to active session (chat or agent)
          if (agentMode) {
            // agent: pinned model survives reload via SQLite (updated on next /agent call)
            modelBtn.classList.add('pinned');
          } else if (activeChatId) {
            const c = chats.find(x => x.id === activeChatId);
            if (c) { c.model = m.id; saveChats(); modelBtn.classList.add('pinned'); }
          }
          menu.classList.remove('open');
        });
        menu.appendChild(item);
      }
    }
    fetch('/models').then(r => r.json()).then(d => {
      allModels = d.models;
      const saved = localStorage.getItem(LS_MODEL);
      currentModel = (saved && allModels.some(m => m.id === saved)) ? saved : d.default;
      const cur = allModels.find(m => m.id === currentModel) || allModels[0];
      modelLabel.textContent = cur.label;
      renderMenu();
    });
    modelBtn.addEventListener('click', (e) => { e.stopPropagation(); menu.classList.toggle('open'); });
    document.addEventListener('click', (e) => {
      if (!menu.contains(e.target) && e.target !== modelBtn) menu.classList.remove('open');
    });

    /* chats */
    function loadChats() { try { chats = JSON.parse(localStorage.getItem(LS_CHATS) || '[]') || []; } catch { chats = []; } }
    function saveChats() { localStorage.setItem(LS_CHATS, JSON.stringify(chats)); }
    function deriveTitle(h) {
      const u = h.find(m => m.role === 'user');
      if (!u) return t('new_chat');
      const txt = typeof u.content === 'string' ? u.content : (u.content.find(p => p.type === 'text')?.text || '');
      return txt.trim().slice(0, 40) || t('new_chat');
    }
    function renderChatList() {
      chatListEl.innerHTML = '';
      let list = [...chats].sort((a,b) => b.ts - a.ts);
      if (chatSearchQuery) {
        list = list.filter(c => ((c.title || '')).toLowerCase().includes(chatSearchQuery));
      }
      if (!list.length) { chatListEl.innerHTML = `<div class="chat-empty">${t('empty_history')}</div>`; return; }
      for (const c of list) {
        const it = document.createElement('div');
        it.className = 'chat-item' + (c.id === activeChatId ? ' active' : '');
        it.textContent = c.title || t('new_chat');
        it.title = c.title;
        it.addEventListener('click', () => switchChat(c.id));
        chatListEl.appendChild(it);
      }
    }
    function persistActive() {
      if (!activeChatId) return;
      const c = chats.find(x => x.id === activeChatId); if (!c) return;
      c.history = history; c.ts = Date.now(); c.title = deriveTitle(history);
      saveChats();
    }
    function renderHistory() {
      messagesEl.innerHTML = '';
      if (!history.length) {
        messagesEl.innerHTML = `<div class="empty">${t('empty_chat')}</div>`;
        return;
      }
      for (const m of history) {
        if (m.role === 'user') {
          const txt = typeof m.content === 'string' ? m.content : (m.content.find(p => p.type === 'text')?.text || t('files_label'));
          addMsg('user', txt, null);
        } else if (m.role === 'assistant') {
          addMsg('assistant', m.content || '', null);
        }
      }
    }
    function newChat() {
      activeChatId = makeId();
      history = [];
      chats.push({ id: activeChatId, title: t('new_chat'), history: [], ts: Date.now() });
      saveChats();
      chatTitleEl.textContent = t('new_chat');
      renderHistory(); renderChatList();
      modelBtn.classList.remove('pinned');
    }
    function switchChat(id) {
      const c = chats.find(x => x.id === id); if (!c) return;
      activeChatId = id;
      history = Array.isArray(c.history) ? c.history.slice() : [];
      chatTitleEl.textContent = c.title || t('new_chat');
      renderHistory(); renderChatList();
      // restore pinned model if any
      if (c.model && allModels.some(m => m.id === c.model)) {
        applyModel(c.model, { pinned: true, persistGlobal: false });
      } else {
        modelBtn.classList.remove('pinned');
      }
    }
    newChatBtn.addEventListener('click', () => {
      if (agentMode) { newAgentSession(); }
      else { setNav('chat'); newChat(); }
    });

    /* agent sessions (server-side persistent) — phase 4b lives in
       static/sessions.js. We instantiate after the deps below are declared
       (forward-declare the binding so the search-input handler can reach it). */
    let chatSearchQuery = '';
    document.getElementById('chat-search').addEventListener('input', (e) => {
      chatSearchQuery = (e.target.value || '').toLowerCase().trim();
      if (agentMode) _sessions.renderAgentSessionList();
      else renderChatList();
    });
    let _sessions;  // assigned at the bottom of the IIFE, after fns exist.
    const loadAgentSessions      = (...a) => _sessions.loadAgentSessions(...a);
    const refreshAgentBudget     = (...a) => _sessions.refreshAgentBudget(...a);
    const renderAgentSessionList = (...a) => _sessions.renderAgentSessionList(...a);
    const loadAgentSession       = (...a) => _sessions.loadAgentSession(...a);
    const newAgentSession        = (...a) => _sessions.newAgentSession(...a);

    /* nav */
    const navItems = document.querySelectorAll('.nav-item');
    const dashboardEl = document.getElementById('dashboard');
    const composerWrap = document.querySelector('.composer-wrap');
    let agentMode = false;
    let agentSessionId = null;
    function setNav(name) {
      navItems.forEach(b => b.classList.toggle('active', b.dataset.nav === name));
      const isDash = name === 'dashboard';
      const isAgent = name === 'agents';
      const isModels = name === 'models';
      const isActions = name === 'actions';
      const isMetrics = name === 'metrics';
      dashboardEl.style.display = isDash ? 'block' : 'none';
      const modelsView = document.getElementById('models-view');
      if (modelsView) modelsView.style.display = isModels ? 'block' : 'none';
      const actionsView = document.getElementById('actions-view');
      if (actionsView) actionsView.style.display = isActions ? 'block' : 'none';
      const metricsView = document.getElementById('metrics-view');
      if (metricsView) metricsView.style.display = isMetrics ? 'block' : 'none';
      const hideMain = isDash || isModels || isActions || isMetrics;
      messagesEl.style.display = hideMain ? 'none' : '';
      composerWrap.style.display = hideMain ? 'none' : '';
      if (isModels) renderModelsView();
      if (isActions) loadActions();
      if (isMetrics) loadMetrics();
      const wasAgent = agentMode;
      agentMode = isAgent;
      document.body.classList.toggle('agent-mode', isAgent);
      if (isAgent) {
        if (!wasAgent) {
          messagesEl.innerHTML = '';
          const hint = document.createElement('div');
          hint.className = 'empty';
          hint.textContent = t('agent_hint');
          messagesEl.appendChild(hint);
          agentSessionId = null;
        }
        input.placeholder = t('agent_placeholder');
        loadAgentSessions();
        refreshAgentBudget();
      } else {
        input.placeholder = t('placeholder');
        document.getElementById('agent-budget').style.display = 'none';
        if (wasAgent) renderChatList();
      }
      if (isDash) loadUsage();
    }
    /* plan panel */
    const planPanel = document.getElementById('plan-panel');
    const planList = document.getElementById('plan-list');
    const planCounter = document.getElementById('plan-counter');
    document.getElementById('plan-toggle').addEventListener('click', () => {
      planPanel.classList.toggle('collapsed');
      document.getElementById('plan-toggle').textContent =
        planPanel.classList.contains('collapsed') ? '+' : '–';
    });
    const _planEls = () => ({ panel: planPanel, list: planList, counter: planCounter });
    const renderPlan = (plan) => _renderPlan(plan, _planEls());
    const clearPlan  = () => _clearPlan(_planEls());


    // === dashboard / actions wiring (was inline; now in dashboard.js) ===
    const _dash = initDashboard({
      t,
      pct,
      getCurrentModel: () => currentModel,
      getModels: () => allModels,
      applyModel: (id) => {
        applyModel(id);
        const c = chats.find(x => x.id === activeChatId);
        if (c) { c.model = id; saveChats(); }
      },
      onModelChange: () => {},
    });
    const loadMetrics  = _dash.loadMetrics;
    const loadCoverage = _dash.loadCoverage;
    const runCoverage  = _dash.runCoverage;
    const loadActions  = _dash.loadActions;
    document.getElementById('metrics-refresh')?.addEventListener('click', loadMetrics);
    document.getElementById('metrics-window')?.addEventListener('change', loadMetrics);
    document.addEventListener('DOMContentLoaded', () => {
      const r=document.getElementById('act-refresh'); if (r) r.addEventListener('click', loadActions);
      const f=document.getElementById('act-failed-only'); if (f) f.addEventListener('change', loadActions);
    });

    const renderModelsView = _dash.renderModelsView;

    navItems.forEach(btn => {
      btn.addEventListener('click', () => {
        const name = btn.dataset.nav;
        if (name === 'chat' || name === 'dashboard' || name === 'agents' || name === 'models' || name === 'actions') {
          setNav(name);
          return;
        }
        if (name === 'skills') {
          openSkillsModal();
          return;
        }
        const labels = { models: t('nav_models'), skills: t('nav_skills') };
        alert(`${labels[name]}${t('section_dev')}`);
      });
    });

    /* skills modal (delegated to skills.js) */
    const skillsModal = document.getElementById('skills-modal');
    const _skills = initSkills({
      modal: skillsModal,
      body: document.getElementById('skills-body'),
      title: document.getElementById('skills-modal-title'),
      back: document.getElementById('skills-back'),
      t,
    });
    const openSkillsModal = _skills.open;
    document.getElementById('skills-close').addEventListener('click', () => skillsModal.classList.remove('show'));
    skillsModal.addEventListener('click', (e) => { if (e.target === skillsModal) skillsModal.classList.remove('show'); });
    document.getElementById('skills-back').addEventListener('click', () => _skills.open());

    /* dashboard data (delegated to dashboard.js) */
    const loadUsage = () => _dash.loadUsage(lang);
    document.getElementById('u-refresh').addEventListener('click', loadUsage);

    /* files */
    // Composer (phase 4c): renderAttachments / ingestFiles / buildUserContent
    // / attachCopyAction / editUserMessage / addMsg live in composer.js.
    // pendingFiles is owned by the module; we read it via getPendingFiles()
    // and reset it via clearPending().
    const _composer = createComposer({
      t: (k) => t(k),
      getLang: () => lang,
      utils: { fmtSize, fileToDataUrl, copyToClipboard },
      renderMarkdown,
      dom: { attachmentsEl, messagesEl, input },
      state: {
        getStreaming: () => streaming,
        getAgentMode: () => agentMode,
        getHistory:   () => history,
        setHistory:   (v) => { history = v; },
      },
      fns: { persistActive, renderChatList },
    });
    const { renderAttachments, ingestFiles, buildUserContent,
            attachCopyAction, editUserMessage, addMsg,
            getPendingFiles, clearPending } = _composer;
    attachBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', async (e) => { await ingestFiles(e.target.files); fileInput.value = ''; });
    input.addEventListener('paste', async (e) => {
      const items = e.clipboardData?.items || [];
      const files = []; for (const it of items) if (it.kind === 'file') { const f = it.getAsFile(); if (f) files.push(f); }
      if (files.length) { e.preventDefault(); await ingestFiles(files); }
    });


    input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 240) + 'px'; });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) { e.preventDefault(); form.requestSubmit(); }
      else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); form.requestSubmit(); }
    });
    // Global hotkeys
    document.addEventListener('keydown', (e) => {
      // Esc closes drawer first (existing handler), but if streaming → stop
      if (e.key === 'Escape' && streaming) {
        e.preventDefault(); e.stopPropagation();
        form.requestSubmit();
      }
    }, true);

    // Tool-card UI builders (addAgentToolCard / attachDiff / attachRollbackOnly
    // / fetchActionDiff / addAgentStats) live in static/tool_cards.js — see
    // phase 4 split.
    const { addAgentToolCard, attachDiff, attachRollbackOnly, fetchActionDiff,
            addAgentStats } = createToolCards({ t, messagesEl });

    // Phase 4b: server-side agent sessions live in static/sessions.js.
    // We assign now that the tool-card builders + applyModel + renderPlan/
    // clearPlan + addMsg all exist. Getters keep the bindings live.
    _sessions = createAgentSessions({
      t: (k) => t(k),
      getLang: () => lang,
      getSearchQuery: () => chatSearchQuery,
      dom: {
        chatListEl, messagesEl, modelBtn,
        budgetEl: document.getElementById('agent-budget'),
      },
      state: {
        getAgentMode:      () => agentMode,
        getAgentSessionId: () => agentSessionId,
        setAgentSessionId: (v) => { agentSessionId = v; },
        getModels:         () => allModels,
      },
      fns: {
        applyModel, renderPlan, clearPlan, addMsg,
        addAgentToolCard, attachDiff, attachRollbackOnly, fetchActionDiff,
      },
    });

    let agentAbort = null;
    let chatAbort = null;
    function setSendBtnMode(stop) {
      // swap the icon between paper-plane and square
      sendBtn.innerHTML = stop
        ? '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>';
      sendBtn.title = stop ? (lang === 'ru' ? 'Остановить' : 'Stop') : '';
    }

    // Reactive state bag — pure getter/setter proxy over the IIFE-scoped lets,
    // so modules can mutate them without us having to refactor the rest of
    // the file to use object property access everywhere.
    const _agentState = {
      get streaming()      { return streaming; },
      set streaming(v)     { streaming = v; },
      get agentAbort()     { return agentAbort; },
      set agentAbort(v)    { agentAbort = v; },
      get agentSessionId() { return agentSessionId; },
      set agentSessionId(v){ agentSessionId = v; },
      get currentModel()   { return currentModel; },
      get agentMode()      { return agentMode; },
    };
    const sendAgent = createAgentRunner({
      state: _agentState,
      t: (k) => t(k),
      lang: () => lang,
      dom: { messagesEl, input },
      fns: {
        addMsg, addAgentToolCard, addAgentStats,
        attachDiff, attachRollbackOnly,
        setSendBtnMode, renderPlan, renderMarkdown,
        loadAgentSessions, refreshAgentBudget,
      },
    });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (streaming) {
        if (agentMode && agentSessionId) {
          fetch('/agent/stop/' + agentSessionId, { method: 'POST' }).catch(() => {});
        }
        if (agentAbort) { agentAbort.abort(); }
        if (chatAbort) { try { chatAbort.abort(); } catch (_) {} }
        return;
      }
      const text = input.value.trim();
      if (!text && getPendingFiles().length === 0) return;
      if (text.startsWith('/') && handleSlashCommand(text)) return;
      if (agentMode) {
        const imgs = getPendingFiles().filter(f => f.dataUrl).map(f => {
          const m = f.dataUrl.match(/^data:image\/(\w+);base64,(.+)$/);
          return m ? { format: m[1], data_base64: m[2] } : null;
        }).filter(Boolean);
        const filesSnapshot = getPendingFiles().slice();
        clearPending();
        // Text files: append their content to the prompt for the agent.
        let agentText = text;
        for (const f of filesSnapshot) if (f.text != null) agentText += `\n\n[file: ${f.name}]\n${f.text}`;
        return sendAgent(agentText, imgs);
      }
      streaming = true; setSendBtnMode(true); chatAbort = new AbortController();
      const filesSnapshot = getPendingFiles().slice();
      const apiContent = buildUserContent(text, filesSnapshot);
      history.push({ role: 'user', content: apiContent });
      // First user message in a chat pins the model for that chat.
      if (activeChatId) {
        const c = chats.find(x => x.id === activeChatId);
        if (c && !c.model) { c.model = currentModel; modelBtn.classList.add('pinned'); }
      }
      const display = text || (filesSnapshot.length ? t('files_label') : '');
      addMsg('user', display, filesSnapshot);
      persistActive();
      input.value = ''; input.style.height = 'auto';
      clearPending();
      const asst = addMsg('assistant', '', null);
      asst.wrap.classList.add('typing');
      let acc = '';
      try {
        const r = await fetch('/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ messages: history, model: currentModel }),
          signal: chatAbort.signal,
        });
        if (!r.ok) {
          const err = await r.text();
          asst.txt.textContent = `${t('error_prefix')} ${r.status}: ${err}`;
          asst.wrap.classList.add('error');
          throw new Error(err);
        }
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          const lines = buf.split('\n'); buf = lines.pop();
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const data = line.slice(6); if (data === '[DONE]') continue;
            try {
              const j = JSON.parse(data);
              if (j.error) {
                let msg = (typeof j.error === 'string' ? j.error : JSON.stringify(j.error));
                // Upstream 4xx/5xx bodies (e.g. Q ValidationException) ride on
                // `j.body` — surface them verbatim so failures stop being blind.
                if (j.body) msg += '\n\n' + (typeof j.body === 'string' ? j.body : JSON.stringify(j.body, null, 2));
                asst.txt.textContent = `${t('error_prefix')}: ` + msg;
                asst.wrap.classList.add('error');
                continue;
              }
              const d = j.choices?.[0]?.delta?.content;
              if (d) { acc += d; asst.txt.textContent = acc; asst.txt.dataset.raw = acc; messagesEl.parentElement.scrollTop = messagesEl.parentElement.scrollHeight; }
            } catch {}
          }
        }
        if (acc && !asst.wrap.classList.contains('error')) renderMarkdown(asst.txt, acc);
        history.push({ role: 'assistant', content: acc });
        persistActive();
        renderChatList();
        chatTitleEl.textContent = chats.find(c => c.id === activeChatId)?.title || t('new_chat');
      } catch (err) {
        if (err.name === 'AbortError') {
          // user pressed Stop; do nothing
        } else if (isNetworkError(err)) {
          // Wi-Fi blip / proxy reset / VM restart mid-stream. Show a single
          // retry banner that auto-clears on /healthz 200.
          asst.wrap.classList.remove('typing');
          await waitForConnection(messagesEl, { lang });
        } else if (!asst.txt.textContent) {
          asst.txt.textContent = `${t('error_prefix')}: ` + err.message;
          asst.wrap.classList.add('error');
        }
      } finally {
        asst.wrap.classList.remove('typing');
        streaming = false; setSendBtnMode(false); chatAbort = null; sendBtn.disabled = false; input.focus();
      }
    });

    /* export current session to .md */
    // Exporters (phase 4c) — live in static/exporters.js.
    const _exporters = createExporters({
      t: (k) => t(k),
      downloadFile, safeFilename,
      getChats:            () => chats,
      getActiveChatId:     () => activeChatId,
      getCurrentModel:     () => currentModel,
      getAgentSessionId:   () => agentSessionId,
    });
    const { exportChatToMd, exportAgentToMd } = _exporters;
    document.getElementById('export-btn').addEventListener('click', () => {
      if (agentMode) exportAgentToMd();
      else exportChatToMd();
    });

    /* settings modal */
    const settingsModal = document.getElementById('settings-modal');
    const settingsTokenInput = document.getElementById('settings-token-input');
    const settingsNameInput = document.getElementById('settings-name-input');
    const settingsTokenStatus = document.getElementById('settings-token-status');
    function refreshSettingsStatus() {
      const tok = (window.kiraAuth && window.kiraAuth.get()) || '';
      settingsTokenStatus.textContent = tok
        ? t('settings_token_set') + ' — ' + tok.slice(0, 8) + '…' + tok.slice(-4)
        : t('settings_token_empty');
    }
    function openSettings(opts = {}) {
      settingsTokenInput.value = (window.kiraAuth && window.kiraAuth.get()) || '';
      settingsNameInput.value = localStorage.getItem('kira_user_name') || '';
      refreshSettingsStatus();
      if (opts.required) {
        settingsTokenStatus.textContent = t('settings_token_required');
        settingsTokenStatus.style.color = '#e07b5b';
      } else {
        settingsTokenStatus.style.color = '';
      }
      settingsModal.classList.add('show');
      setTimeout(() => settingsTokenInput.focus(), 50);
    }
    function closeSettings() {
      settingsModal.classList.remove('show');
      // If 401-waiter is pending and user closed without saving, resolve with empty
      if (window.kiraAuth && window.kiraAuth.resolveAuthWait) {
        window.kiraAuth.resolveAuthWait('');
      }
    }
    document.getElementById('settings-btn').addEventListener('click', () => openSettings());
    document.getElementById('settings-close').addEventListener('click', closeSettings);
    settingsModal.addEventListener('click', (e) => {
      if (e.target === settingsModal) closeSettings();
    });
    document.getElementById('settings-save').addEventListener('click', () => {
      const tok = settingsTokenInput.value.trim();
      const name = settingsNameInput.value.trim();
      if (window.kiraAuth) window.kiraAuth.set(tok);
      if (name) {
        localStorage.setItem('kira_user_name', name);
        const pn = document.getElementById('profile-name');
        const av = document.getElementById('avatar');
        if (pn) pn.textContent = name;
        if (av) av.textContent = name.charAt(0).toUpperCase();
      }
      refreshSettingsStatus();
      // Wake any 401-waiter so the failed request can retry.
      if (window.kiraAuth && window.kiraAuth.resolveAuthWait) {
        window.kiraAuth.resolveAuthWait(tok);
      }
      settingsModal.classList.remove('show');
    });
    document.getElementById('settings-clear').addEventListener('click', () => {
      if (window.kiraAuth) window.kiraAuth.clear();
      settingsTokenInput.value = '';
      refreshSettingsStatus();
    });
    // Auto-open settings when auth.js signals a 401.
    window.addEventListener('kira:auth-required', () => openSettings({ required: true }));
    document.getElementById('profile-btn').addEventListener('click', () => {
      const cur = localStorage.getItem('kira_user_name') || 'Олег';
      const name = prompt('Имя:', cur);
      if (name && name.trim()) {
        localStorage.setItem('kira_user_name', name.trim());
        document.getElementById('profile-name').textContent = name.trim();
        document.getElementById('avatar').textContent = name.trim().charAt(0).toUpperCase();
      }
    });
    {
      const savedName = localStorage.getItem('kira_user_name');
      if (savedName) {
        document.getElementById('profile-name').textContent = savedName;
        document.getElementById('avatar').textContent = savedName.charAt(0).toUpperCase();
      }
    }

    /* drag & drop files into agent workspace */
    {
      const overlay = document.getElementById('drop-overlay');
      let depth = 0;
      document.addEventListener('dragenter', (e) => {
        if (!e.dataTransfer || ![...(e.dataTransfer.types||[])].includes('Files')) return;
        depth++;
        if (agentMode) overlay.classList.add('show');
      });
      document.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (depth === 0) overlay.classList.remove('show'); });
      document.addEventListener('dragover', (e) => { if (e.dataTransfer && [...(e.dataTransfer.types||[])].includes('Files')) e.preventDefault(); });
      document.addEventListener('drop', async (e) => {
        if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
        e.preventDefault();
        depth = 0; overlay.classList.remove('show');
        const files = [...e.dataTransfer.files];
        if (agentMode) {
          if (!agentSessionId) {
            // create a session id locally; first /agent call will use it
            agentSessionId = makeId() + makeId().slice(0,4);
          }
          const fd = new FormData();
          for (const f of files) fd.append('files', f, f.name);
          try {
            const r = await fetch('/agent/upload/' + agentSessionId, { method: 'POST', body: fd });
            const d = await r.json();
            if (d.ok) {
              const names = (d.saved || []).map(s => s.name).join(', ');
              const div = document.createElement('div');
              div.className = 'agent-stats';
              div.textContent = '📎 ' + t('upload_ok') + ': ' + names + ' → /workspace/';
              messagesEl.appendChild(div);
              messagesEl.parentElement.scrollTop = messagesEl.parentElement.scrollHeight;
            } else { alert(t('upload_fail')); }
          } catch { alert(t('upload_fail')); }
        } else {
          // chat mode: route to existing attach pipeline
          ingestFiles(files);
        }
      });
    }

    /* slash commands: /clear, /model <q>, /rename <title> */
    function handleSlashCommand(text) {
      const m = text.match(/^\/(\w+)(?:\s+(.+))?$/);
      if (!m) return false;
      const cmd = m[1].toLowerCase(); const arg = (m[2] || '').trim();
      if (cmd === 'clear') {
        if (agentMode) {
          if (agentSessionId) {
            fetch('/agent/sessions/' + agentSessionId, { method: 'DELETE' }).catch(() => {});
          }
          newAgentSession(); loadAgentSessions();
        } else {
          const c = chats.find(x => x.id === activeChatId);
          if (c) { c.history = []; history = []; saveChats(); renderHistory(); }
        }
        input.value = ''; input.style.height = 'auto';
        return true;
      }
      if (cmd === 'model') {
        if (!arg) { menu.classList.add('open'); input.value = ''; input.style.height = 'auto'; return true; }
        const q = arg.toLowerCase();
        const found = allModels.find(x => x.id.toLowerCase().includes(q) || x.label.toLowerCase().includes(q));
        if (found) {
          applyModel(found.id, { pinned: !!activeChatId || agentMode });
          if (!agentMode && activeChatId) {
            const c = chats.find(x => x.id === activeChatId);
            if (c) { c.model = found.id; saveChats(); modelBtn.classList.add('pinned'); }
          }
        }
        input.value = ''; input.style.height = 'auto';
        return true;
      }
      if (cmd === 'rename') {
        const title = arg.slice(0, 80);
        if (!title) return true;
        if (agentMode) {
          if (agentSessionId) {
            fetch('/agent/sessions/' + agentSessionId + '/rename', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ title }),
            }).then(() => loadAgentSessions());
          }
        } else {
          const c = chats.find(x => x.id === activeChatId);
          if (c) { c.title = title; saveChats(); chatTitleEl.textContent = title; renderChatList(); }
        }
        input.value = ''; input.style.height = 'auto';
        return true;
      }
      if (cmd === 'help' || cmd === '?') {
        alert('/clear  —  ' + (lang==='ru'?'очистить чат':'clear chat') + '\n' +
              '/model <q>  —  ' + (lang==='ru'?'выбрать модель':'pick model') + '\n' +
              '/rename <title>  —  ' + (lang==='ru'?'переименовать':'rename'));
        input.value = ''; input.style.height = 'auto';
        return true;
      }
      return false;
    }

    /* theme toggle */
    const LS_THEME = 'kira_theme';
    function applyTheme(name) {
      document.body.classList.toggle('theme-light', name === 'light');
      localStorage.setItem(LS_THEME, name);
      const hl = document.getElementById('hljs-theme');
      if (hl) hl.href = 'https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/styles/github-' + (name === 'light' ? 'light' : 'dark') + '.min.css';
      const icon = document.getElementById('theme-icon');
      if (icon) {
        // sun for light mode, moon for dark mode
        if (name === 'light') {
          icon.innerHTML = '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>';
        } else {
          icon.innerHTML = '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>';
        }
      }
    }
    applyTheme(localStorage.getItem(LS_THEME) || 'dark');
    document.getElementById('theme-toggle').addEventListener('click', () => {
      applyTheme(document.body.classList.contains('theme-light') ? 'dark' : 'light');
    });

    /* boot */
    loadChats();
    if (chats.length) { const last = [...chats].sort((a,b) => b.ts - a.ts)[0]; switchChat(last.id); } else { newChat(); }
    applyI18n();
