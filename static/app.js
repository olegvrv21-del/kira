    /* ---- auth token: install fetch wrapper so all requests carry Bearer ---- */
    (function installAuthFetch(){
      const KEY = 'kira_auth_token';
      const getTok = () => { try { return localStorage.getItem(KEY) || ''; } catch (_) { return ''; } };
      // Expose so a console / settings UI can update.
      window.kiraAuth = {
        get: getTok,
        set: (t) => { try { localStorage.setItem(KEY, t || ''); } catch(_){} },
        clear: () => { try { localStorage.removeItem(KEY); } catch(_){} },
        prompt: () => {
          const cur = getTok();
          const v = window.prompt('Kira auth token (empty to clear):', cur);
          if (v === null) return cur;
          window.kiraAuth.set(v.trim());
          return v.trim();
        },
      };
      const origFetch = window.fetch.bind(window);
      window.fetch = function(input, init) {
        const tok = getTok();
        if (!tok) return origFetch(input, init);
        // Only inject for same-origin (relative URLs or matching host).
        let url = input;
        try { url = (typeof input === 'string') ? input : (input && input.url) || ''; } catch(_) {}
        const sameOrigin = (typeof url === 'string') && (url.startsWith('/') ||
          (url.startsWith(location.origin)) || (!url.includes('://')));
        if (!sameOrigin) return origFetch(input, init);
        const opts = Object.assign({}, init || {});
        const headers = new Headers(opts.headers || (typeof input === 'object' && input ? input.headers : undefined) || {});
        if (!headers.has('authorization')) headers.set('authorization', 'Bearer ' + tok);
        opts.headers = headers;
        return origFetch(input, opts).then((r) => {
          if (r && r.status === 401) {
            console.warn('[kira] 401: token rejected for', url);
          }
          return r;
        });
      };
      // EventSource doesn't support custom headers; append token as query param.
      // Server can be extended later to accept ?token=. For now, /chat & SSE use
      // fetch streaming, so we're fine.
    })();

    /* i18n */
    const I18N = {
      ru: {
        new_session: 'Новая сессия',
        nav_chat: 'Чат',
        nav_dashboard: 'Панель управления',
        nav_agents: 'Агенты',
        nav_models: 'Модели',
        nav_skills: 'Навыки',
        history: 'История',
        empty_history: 'Пока пусто',
        empty_chat: 'Напиши что-нибудь.',
        new_chat: 'Новый чат',
        placeholder: 'Сообщение…  (Shift+Enter — перенос, /help — команды)',
        attach_title: 'Прикрепить файл',
        send_title: 'Отправить',
        model_title: 'Выбор модели',
        model_label: 'Модель',
        menu_title: 'Меню',
        files_label: '(файлы)',
        too_big: ': больше 5 МБ',
        only_text_image: ': поддерживаю только текстовые и изображения',
        section_dev: ': раздел в разработке.',
        error_prefix: 'Ошибка',
        settings: 'Настройки',
        export: 'Экспорт',
        export_empty: 'Пусто — нечего экспортировать.',
        drop_hint: 'Отпусти для загрузки в workspace агента',
        upload_ok: 'Загружено',
        upload_fail: 'Ошибка загрузки',
        theme_light: 'Светлая',
        theme_dark: 'Тёмная',
        back: 'Назад',
        skills_empty: 'Навыки не найдены.',
        copy_label: 'Копировать',
        copied_label: 'Скопировано',
        online: 'Онлайн',
        dash_title: 'Панель управления',
        dash_credits: 'Кредиты',
        dash_plan: 'Тариф',
        dash_plan_id: 'Тип',
        dash_reset: 'Сброс',
        dash_overage: 'Перерасход',
        dash_overage_rate: 'Ставка',
        dash_models: 'Множители стоимости',
        models_title: 'Модели',
        model_active: 'Активна',
        model_choose: 'Выбрать',
        nav_actions: 'Действия',
        actions_title: 'Действия Киры',
        plan_title: 'План',
        diff_title: 'DIFF',
        diff_lines: 'строк',
        diff_rollback: 'Откатить',
        diff_rollback_confirm: 'Откатить изменения?',
        actions_failed_only: 'только ошибки',
        actions_rollback: 'Откатить',
        actions_empty: 'Пока ничего нет.',
        dash_refresh: 'Обновить',
        dash_loading: 'Загружаю…',
        dash_remaining: 'Осталось',
        dash_no_key: 'Ключ Kiro не настроен',
        dash_in_days: 'через {n} дн.',
        agent_hint: 'Агент-режим. Я выполняю команды и редактирую файлы в изолированной папке. Опиши задачу.',
        agent_placeholder: 'Задача для агента…',
        agent_session: 'сессия',
        agent_tool: 'инструмент',
        agent_running: 'выполняется…',
        agent_success: 'успех',
        agent_error: 'ошибка',
        agent_input: 'вход',
        agent_output: 'результат',
        agent_done: 'готово',
        agent_credits: 'кредитов',
        agent_context: 'контекст',
        agent_turns: 'ходов',
      },
      en: {
        new_session: 'New session',
        nav_chat: 'Chat',
        nav_dashboard: 'Dashboard',
        nav_agents: 'Agents',
        nav_models: 'Models',
        nav_skills: 'Skills',
        history: 'History',
        empty_history: 'Nothing yet',
        empty_chat: 'Type something.',
        new_chat: 'New chat',
        placeholder: 'Message…  (Shift+Enter — newline, /help — commands)',
        attach_title: 'Attach file',
        send_title: 'Send',
        model_title: 'Pick model',
        model_label: 'Model',
        menu_title: 'Menu',
        files_label: '(files)',
        too_big: ': larger than 5 MB',
        only_text_image: ': only text and images supported',
        section_dev: ': section under development.',
        error_prefix: 'Error',
        settings: 'Settings',
        export: 'Export',
        export_empty: 'Nothing to export.',
        drop_hint: 'Drop files to upload into agent workspace',
        upload_ok: 'Uploaded',
        upload_fail: 'Upload failed',
        theme_light: 'Light',
        theme_dark: 'Dark',
        back: 'Back',
        skills_empty: 'No skills found.',
        copy_label: 'Copy',
        copied_label: 'Copied',
        online: 'Online',
        dash_title: 'Dashboard',
        dash_credits: 'Credits',
        dash_plan: 'Plan',
        dash_plan_id: 'Type',
        dash_reset: 'Reset',
        dash_overage: 'Overage',
        dash_overage_rate: 'Rate',
        dash_models: 'Cost multipliers',
        models_title: 'Models',
        model_active: 'Active',
        model_choose: 'Select',
        nav_actions: 'Actions',
        actions_title: 'Kira actions log',
        plan_title: 'Plan',
        diff_title: 'DIFF',
        diff_lines: 'lines',
        diff_rollback: 'Rollback',
        diff_rollback_confirm: 'Rollback this change?',
        actions_failed_only: 'failures only',
        actions_rollback: 'Rollback',
        actions_empty: 'No actions yet.',
        dash_refresh: 'Refresh',
        dash_loading: 'Loading…',
        dash_remaining: 'Remaining',
        dash_no_key: 'Kiro key not set',
        dash_in_days: 'in {n} days',
        agent_hint: 'Agent mode. I run commands and edit files in an isolated folder. Describe a task.',
        agent_placeholder: 'Task for the agent…',
        agent_session: 'session',
        agent_tool: 'tool',
        agent_running: 'running…',
        agent_success: 'success',
        agent_error: 'error',
        agent_input: 'input',
        agent_output: 'output',
        agent_done: 'done',
        agent_credits: 'credits',
        agent_context: 'context',
        agent_turns: 'turns',
      },
    };
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
    let pendingFiles = [];
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
    function makeId() {
      // Use cryptographically strong randomness (avoids Math.random predictability;
      // also satisfies CodeQL js/insecure-randomness for IDs used in session paths).
      const buf = new Uint8Array(6);
      (self.crypto || self.msCrypto).getRandomValues(buf);
      let s = '';
      for (const b of buf) s += b.toString(36).padStart(2, '0');
      return Date.now().toString(36) + s.slice(0, 8);
    }
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

    /* agent sessions (server-side persistent) */
    let agentSessions = [];
    let chatSearchQuery = '';
    document.getElementById('chat-search').addEventListener('input', (e) => {
      chatSearchQuery = (e.target.value || '').toLowerCase().trim();
      if (agentMode) renderAgentSessionList();
      else renderChatList();
    });
    async function loadAgentSessions() {
      try {
        const r = await fetch('/agent/sessions');
        const d = await r.json();
        agentSessions = d.sessions || [];
      } catch { agentSessions = []; }
      renderAgentSessionList();
    }
    async function refreshAgentBudget() {
      const el = document.getElementById('agent-budget');
      if (!agentMode) { el.style.display = 'none'; return; }
      try {
        const url = '/agent/limits' + (agentSessionId ? ('?session_id=' + agentSessionId) : '');
        const r = await fetch(url); const d = await r.json();
        const sLim = d.session_limit > 0 ? '/' + d.session_limit.toFixed(0) : '';
        const dLim = d.day_limit > 0 ? '/' + d.day_limit.toFixed(0) : '';
        const mLim = d.month_limit > 0 ? '/' + d.month_limit.toFixed(0) : '';
        const sess = d.session_credits.toFixed(2);
        const day = d.day_credits.toFixed(2);
        const month = (d.month_credits || 0).toFixed(2);
        const ru = (lang === 'ru');
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
      let list = agentSessions;
      if (chatSearchQuery) {
        list = list.filter(s => (s.title || s.sid).toLowerCase().includes(chatSearchQuery));
      }
      if (!list.length) {
        chatListEl.innerHTML = `<div class="chat-empty">${t('empty_history')}</div>`;
        return;
      }
      for (const s of list) {
        const it = document.createElement('div');
        it.className = 'chat-item' + (s.sid === agentSessionId ? ' active' : '');
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
          if (agentSessionId === s.sid) { agentSessionId = null; messagesEl.innerHTML = `<div class="empty">${t('agent_hint')}</div>`; }
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
        agentSessionId = sid;
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
      agentSessionId = null;
      modelBtn.classList.remove('pinned');
      messagesEl.innerHTML = `<div class="empty">${t('agent_hint')}</div>`;
      clearPlan();
      renderAgentSessionList();
      refreshAgentBudget();
    }

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
    function renderPlan(plan) {
      const items = (plan && plan.items) || [];
      if (!items.length) { planPanel.style.display = 'none'; planList.innerHTML = ''; return; }
      planPanel.style.display = '';
      planList.innerHTML = '';
      let done = 0;
      for (let i = 0; i < items.length; i++) {
        const it = items[i] || {};
        const li = document.createElement('li');
        li.className = 'plan-item ' + (it.status || 'pending');
        const mark = it.status === 'done' ? '✔' : it.status === 'in_progress' ? '▸' : it.status === 'skipped' ? '—' : '·';
        if (it.status === 'done') done++;
        li.innerHTML = `<span class="plan-mark">${mark}</span><span class="plan-text"></span>`;
        li.querySelector('.plan-text').textContent = it.text || '';
        planList.appendChild(li);
      }
      planCounter.textContent = `${done}/${items.length}`;
    }
    function clearPlan() { renderPlan({items:[]}); }

    function pct(x) { return x == null ? '—' : (x * 100).toFixed(1) + '%'; }

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
        // by tool table
        html += '<h3 style="margin:24px 0 8px;font-size:14px;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px">По инструментам</h3>';
        html += '<table class="metric-table"><thead><tr><th>Tool</th><th>Calls</th><th>OK</th><th>Success</th><th></th></tr></thead><tbody>';
        for (const t of (d.by_tool || [])) {
          const sr = t.success_rate;
          const w = sr != null ? Math.round(sr * 100) : 0;
          html += `<tr><td><code>${t.tool}</code></td><td>${t.count}</td><td>${t.ok}</td><td>${pct(sr)}</td><td style="width:120px"><div class="metric-bar"><div style="width:${w}%"></div></div></td></tr>`;
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
        loadCoverage();  // append coverage block below
      } catch (e) {
        body.innerHTML = `<div style="color:#e57373">${e.message || e}</div>`;
      }
    }
    document.getElementById('metrics-refresh')?.addEventListener('click', loadMetrics);
    document.getElementById('metrics-window')?.addEventListener('change', loadMetrics);

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

    async function loadActions() {
      const list = document.getElementById('actions-list');
      list.innerHTML = '<div style="color:var(--muted)">…</div>';
      const failOnly = document.getElementById('act-failed-only').checked;
      try {
        const r = await fetch('/agent/actions?limit=300');
        const d = await r.json();
        let acts = d.actions || [];
        if (failOnly) acts = acts.filter(a => !a.ok);
        if (!acts.length) { list.innerHTML = `<div style="color:var(--muted)">${t('actions_empty')}</div>`; return; }
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
            const rr = await fetch(`/agent/actions/${a.id}/rollback`, {method:'POST'});
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
    document.addEventListener('DOMContentLoaded', () => {
      const r=document.getElementById('act-refresh'); if (r) r.addEventListener('click', loadActions);
      const f=document.getElementById('act-failed-only'); if (f) f.addEventListener('change', loadActions);
    });

    function renderModelsView() {
      const grid = document.getElementById('models-grid');
      if (!grid) return;
      grid.innerHTML = '';
      for (const m of allModels) {
        const tier = m.tier || 'sonnet';
        const card = document.createElement('div');
        card.className = 'model-card tier-' + tier;
        const tags = (m.strengths || []).map(s => `<span class="mc-tag">${s}</span>`).join('');
        const mult = (m.multiplier != null) ? `×${m.multiplier}` : '';
        const isActive = m.id === currentModel;
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
          if (m.id === currentModel) return;
          applyModel(m.id);
          const c = chats.find(x => x.id === activeChatId);
          if (c) { c.model = m.id; saveChats(); }
          renderModelsView();
        });
        grid.appendChild(card);
      }
    }
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

    /* skills modal */
    const skillsModal = document.getElementById('skills-modal');
    const skillsBody = document.getElementById('skills-body');
    const skillsTitle = document.getElementById('skills-modal-title');
    const skillsBack = document.getElementById('skills-back');
    document.getElementById('skills-close').addEventListener('click', () => skillsModal.classList.remove('show'));
    skillsModal.addEventListener('click', (e) => { if (e.target === skillsModal) skillsModal.classList.remove('show'); });
    skillsBack.addEventListener('click', () => openSkillsModal());
    async function openSkillsModal() {
      skillsModal.classList.add('show');
      skillsTitle.textContent = t('nav_skills');
      skillsBack.style.display = 'none';
      skillsBody.innerHTML = '<div style="color:var(--muted)">…</div>';
      try {
        const r = await fetch('/skills');
        const d = await r.json();
        if (!d.skills || !d.skills.length) { skillsBody.innerHTML = `<div style="color:var(--muted)">${t('skills_empty')}</div>`; return; }
        skillsBody.innerHTML = '';
        for (const s of d.skills) {
          const row = document.createElement('div'); row.className = 'skill-row';
          row.innerHTML = `<div class="sk-name"></div><div class="sk-desc"></div>`;
          row.querySelector('.sk-name').textContent = s.name;
          row.querySelector('.sk-desc').textContent = s.description || '';
          row.addEventListener('click', () => openSkillDetail(s.name));
          skillsBody.appendChild(row);
        }
      } catch (e) { skillsBody.textContent = String(e); }
    }
    async function openSkillDetail(name) {
      skillsTitle.textContent = name;
      skillsBack.style.display = '';
      skillsBody.innerHTML = '<div style="color:var(--muted)">…</div>';
      try {
        const r = await fetch('/skills/' + encodeURIComponent(name));
        const d = await r.json();
        const pre = document.createElement('div'); pre.className = 'skill-body';
        pre.textContent = d.body || '';
        skillsBody.innerHTML = ''; skillsBody.appendChild(pre);
      } catch (e) { skillsBody.textContent = String(e); }
    }

    /* dashboard data */
    function fmtDate(ts) {
      if (!ts) return '—';
      const d = new Date(ts * 1000);
      const days = Math.max(0, Math.ceil((d - Date.now()) / 86400000));
      const dateStr = d.toLocaleDateString(lang === 'ru' ? 'ru-RU' : 'en-US', { day: '2-digit', month: 'short' });
      return `${dateStr} · ${t('dash_in_days').replace('{n}', days)}`;
    }
    async function loadUsage() {
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
        reset.textContent = fmtDate(d.reset_at);
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
    document.getElementById('u-refresh').addEventListener('click', loadUsage);

    /* files */
    function fmtSize(b) { if (b<1024) return b+' B'; if (b<1048576) return (b/1024).toFixed(1)+' KB'; return (b/1048576).toFixed(1)+' MB'; }
    function renderAttachments() {
      attachmentsEl.innerHTML = '';
      pendingFiles.forEach((f, i) => {
        const chip = document.createElement('div'); chip.className = 'chip';
        const label = document.createElement('span'); label.textContent = `${f.name} · ${fmtSize(f.size)}`;
        const x = document.createElement('button'); x.type='button'; x.textContent='×';
        x.addEventListener('click', () => { pendingFiles.splice(i,1); renderAttachments(); });
        chip.appendChild(label); chip.appendChild(x); attachmentsEl.appendChild(chip);
      });
    }
    function fileToDataUrl(f) { return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); }); }
    async function ingestFiles(list) {
      for (const f of list) {
        const isImage = f.type.startsWith('image/');
        const isText = f.type.startsWith('text/') || /\.(md|txt|json|ya?ml|csv|log|py|js|ts|tsx|jsx|html|css|sh|conf|toml|ini|xml|sql|go|rs|c|cpp|h|hpp|java|rb|php)$/i.test(f.name);
        if (f.size > 5*1024*1024) { alert(f.name + t('too_big')); continue; }
        const e = { name: f.name, type: f.type, size: f.size };
        if (isImage) e.dataUrl = await fileToDataUrl(f);
        else if (isText) e.text = await f.text();
        else { alert(f.name + t('only_text_image')); continue; }
        pendingFiles.push(e);
      }
      renderAttachments();
    }
    attachBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', async (e) => { await ingestFiles(e.target.files); fileInput.value = ''; });
    input.addEventListener('paste', async (e) => {
      const items = e.clipboardData?.items || [];
      const files = []; for (const it of items) if (it.kind === 'file') { const f = it.getAsFile(); if (f) files.push(f); }
      if (files.length) { e.preventDefault(); await ingestFiles(files); }
    });

    /* compose */
    function buildUserContent(text, files) {
      const hasImages = files.some(f => f.dataUrl);
      if (hasImages) {
        const parts = []; let combined = text || '';
        for (const f of files) if (f.text) combined += `\n\n[file: ${f.name}]\n${f.text}`;
        if (combined.trim()) parts.push({ type: 'text', text: combined });
        for (const f of files) if (f.dataUrl) parts.push({ type: 'image_url', image_url: { url: f.dataUrl } });
        return parts;
      }
      let combined = text || '';
      for (const f of files) if (f.text != null) combined += `\n\n[file: ${f.name}]\n${f.text}`;
      return combined;
    }
    function renderMarkdown(span, text) {
      try {
        const html = DOMPurify.sanitize(marked.parse(text || '', { gfm: true, breaks: true }));
        span.innerHTML = html;
        if (window.hljs) {
          span.querySelectorAll('pre code').forEach(el => {
            try { hljs.highlightElement(el); } catch {}
          });
        }
      } catch { span.textContent = text || ''; }
    }
    function copyToClipboard(text) {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text);
      }
      // fallback
      const ta = document.createElement('textarea'); ta.value = text;
      ta.style.position = 'fixed'; ta.style.left = '-9999px';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } finally { ta.remove(); }
      return Promise.resolve();
    }
    function attachCopyAction(wrap, getText, opts = {}) {
      const actions = document.createElement('div'); actions.className = 'msg-actions';
      const copyBtn = document.createElement('button'); copyBtn.type = 'button';
      copyBtn.textContent = lang === 'ru' ? 'Копировать' : 'Copy';
      copyBtn.title = copyBtn.textContent;
      copyBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        try {
          await copyToClipboard(getText());
          copyBtn.textContent = lang === 'ru' ? 'Готово' : 'Copied';
          copyBtn.classList.add('copied');
          setTimeout(() => {
            copyBtn.textContent = lang === 'ru' ? 'Копировать' : 'Copy';
            copyBtn.classList.remove('copied');
          }, 1400);
        } catch {}
      });
      actions.appendChild(copyBtn);
      if (opts.editable) {
        const editBtn = document.createElement('button'); editBtn.type = 'button';
        editBtn.textContent = lang === 'ru' ? 'Редактировать' : 'Edit';
        editBtn.title = editBtn.textContent;
        editBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          opts.onEdit && opts.onEdit();
        });
        actions.appendChild(editBtn);
      }
      wrap.appendChild(actions);
    }

    function editUserMessage(msgDiv) {
      if (streaming) return;
      // count which user msg this is (0-based among user msgs in DOM)
      const userMsgs = [...messagesEl.querySelectorAll('.msg.user')];
      const idx = userMsgs.indexOf(msgDiv);
      if (idx < 0) return;
      // find same-index user msg in history
      let hi = -1, count = -1;
      for (let i = 0; i < history.length; i++) {
        if (history[i].role === 'user') { count++; if (count === idx) { hi = i; break; } }
      }
      if (hi < 0) return;
      const m = history[hi];
      const raw = typeof m.content === 'string'
        ? m.content
        : (Array.isArray(m.content) ? (m.content.find(p => p.type === 'text')?.text || '') : '');
      // truncate history from this user msg onward; remove DOM from this msg onward
      history = history.slice(0, hi);
      let n = msgDiv;
      while (n) { const next = n.nextSibling; n.remove(); n = next; }
      persistActive(); renderChatList();
      input.value = raw;
      input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 240) + 'px';
      input.focus();
    }

    function addMsg(role, displayText, files, opts = {}) {
      const empty = messagesEl.querySelector('.empty'); if (empty) empty.remove();
      const div = document.createElement('div');
      div.className = 'msg ' + role + (opts.error ? ' error' : '');
      if (files && files.length) {
        for (const f of files) {
          const ch = document.createElement('span'); ch.className = 'chip';
          ch.style.marginRight = '6px'; ch.style.marginBottom = '6px';
          ch.textContent = f.name; div.appendChild(ch);
        }
        div.appendChild(document.createElement('br'));
      }
      const txt = document.createElement('span');
      txt.dataset.raw = displayText || '';
      if (role === 'assistant' && displayText && !opts.error) {
        renderMarkdown(txt, displayText);
      } else {
        txt.textContent = displayText;
      }
      div.appendChild(txt);
      if (role === 'user' || role === 'assistant') {
        const editable = role === 'user' && !agentMode && !opts.noEdit;
        attachCopyAction(div, () => txt.dataset.raw || txt.textContent || '', {
          editable,
          onEdit: editable ? () => editUserMessage(div) : null,
        });
      }
      messagesEl.appendChild(div);
      messagesEl.parentElement.scrollTop = messagesEl.parentElement.scrollHeight;
      return { wrap: div, txt };
    }

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

    function addAgentToolCard(toolUseId, name) {
      const empty = messagesEl.querySelector('.empty'); if (empty) empty.remove();
      const card = document.createElement('div');
      card.className = 'tool-card running';
      card.dataset.toolId = toolUseId;
      card.innerHTML = `
        <div class="tool-head">
          <span class="tool-chevron">▸</span>
          <span class="tool-name"></span>
          <span class="tool-summary"></span>
          <span class="tool-status" style="color:var(--muted);font-size:11px"></span>
        </div>
        <div class="tool-body">
          <div><span class="label"></span><pre class="tool-input"></pre></div>
          <div class="out-wrap" style="margin-top:8px"><span class="label"></span><pre class="tool-output"></pre></div>
        </div>`;
      card.querySelector('.tool-name').textContent = name;
      card.querySelector('.tool-status').textContent = t('agent_running');
      card.querySelector('.tool-body .label').textContent = t('agent_input');
      card.querySelector('.out-wrap .label').textContent = t('agent_output');
      card.querySelector('.out-wrap').style.display = 'none';
      card.querySelector('.tool-head').addEventListener('click', () => card.classList.toggle('open'));
      messagesEl.appendChild(card);
      messagesEl.parentElement.scrollTop = messagesEl.parentElement.scrollHeight;
      return card;
    }
    function attachDiff(card, diffText, diffLines, actionId, filePath) {
      // Avoid duplicates on rerender.
      const old = card.querySelector('.diff-wrap'); if (old) old.remove();
      const wrap = document.createElement('div');
      wrap.className = 'diff-wrap';
      const fname = (filePath || '').split('/').pop() || '';
      wrap.innerHTML = `
        <div class="diff-head">
          <span class="diff-title">${t('diff_title')}</span>
          <span class="diff-meta"></span>
          <span class="diff-spacer"></span>
          <button class="diff-rb" style="display:none"></button>
        </div>
        <div class="diff-body"></div>`;
      wrap.querySelector('.diff-meta').textContent =
        (fname ? fname + '  ·  ' : '') + (diffLines||0) + ' ' + t('diff_lines');
      const body = wrap.querySelector('.diff-body');
      const lines = (diffText || '').split('\n');
      for (const ln of lines) {
        const span = document.createElement('span');
        span.className = 'ln';
        if (ln.startsWith('+++') || ln.startsWith('---')) span.classList.add('meta');
        else if (ln.startsWith('@@')) span.classList.add('hunk');
        else if (ln.startsWith('+')) span.classList.add('add');
        else if (ln.startsWith('-')) span.classList.add('del');
        span.textContent = ln;
        body.appendChild(span);
      }
      const rbBtn = wrap.querySelector('.diff-rb');
      if (actionId) {
        rbBtn.style.display = '';
        rbBtn.textContent = t('diff_rollback');
        rbBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          if (!confirm(t('diff_rollback_confirm'))) return;
          rbBtn.disabled = true;
          try {
            const r = await fetch(`/agent/actions/${actionId}/rollback`, {method:'POST'});
            const d = await r.json();
            if (r.ok) {
              rbBtn.textContent = '✓';
              wrap.style.opacity = '0.5';
            } else {
              alert('FAIL: ' + (d.detail || JSON.stringify(d)));
              rbBtn.disabled = false;
            }
          } catch (err) { alert(err); rbBtn.disabled = false; }
        });
      }
      wrap.querySelector('.diff-head').addEventListener('click', () => wrap.classList.toggle('open'));
      // Open by default if diff is small enough.
      if (lines.length <= 40) wrap.classList.add('open');
      card.querySelector('.tool-body').appendChild(wrap);
    }

    function attachRollbackOnly(card, actionId) {
      const old = card.querySelector('.diff-wrap'); if (old) return;
      const wrap = document.createElement('div');
      wrap.className = 'diff-wrap';
      wrap.innerHTML = `<div class="diff-head"><span class="diff-title">EDIT</span><span class="diff-spacer"></span><button class="diff-rb"></button></div>`;
      const btn = wrap.querySelector('.diff-rb');
      btn.textContent = t('diff_rollback');
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(t('diff_rollback_confirm'))) return;
        btn.disabled = true;
        const r = await fetch(`/agent/actions/${actionId}/rollback`, {method:'POST'});
        const d = await r.json();
        if (r.ok) { btn.textContent = '✓'; wrap.style.opacity = '0.5'; }
        else { alert('FAIL: ' + (d.detail || JSON.stringify(d))); btn.disabled = false; }
      });
      card.querySelector('.tool-body').appendChild(wrap);
    }

    async function fetchActionDiff(actionId) {
      try {
        const r = await fetch(`/agent/actions/${actionId}`);
        if (!r.ok) return null;
        return await r.json();
      } catch { return null; }
    }

    function addAgentStats(s) {
      const div = document.createElement('div');
      div.className = 'agent-stats';
      div.textContent = `· ${s.turns} ${t('agent_turns')} · ${(+s.credits).toFixed(4)} ${t('agent_credits')} · ${(+s.context_pct).toFixed(1)}% ${t('agent_context')}`;
      messagesEl.appendChild(div);
      messagesEl.parentElement.scrollTop = messagesEl.parentElement.scrollHeight;
    }

    let agentAbort = null;
    function setSendBtnMode(stop) {
      // swap the icon between paper-plane and square
      sendBtn.innerHTML = stop
        ? '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>';
      sendBtn.title = stop ? (lang === 'ru' ? 'Остановить' : 'Stop') : '';
    }

    async function sendAgent(text, images) {
      streaming = true;
      setSendBtnMode(true);
      addMsg('user', text, null);
      input.value = ''; input.style.height = 'auto';
      let curText = null, acc = '';
      const ensureText = () => {
        if (!curText) { curText = addMsg('assistant', '', null); curText.wrap.classList.add('typing'); acc = ''; }
        return curText;
      };
      const cards = new Map();
      let sawRestart = false;
      agentAbort = new AbortController();
      try {
        const r = await fetch('/agent', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: text, model: currentModel, session_id: agentSessionId, images: images || null }),
          signal: agentAbort.signal,
        });
        if (!r.ok) {
          const err = await r.text();
          addMsg('assistant', `${t('error_prefix')} ${r.status}: ${err}`, null, { error: true });
          return;
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
            let j; try { j = JSON.parse(line.slice(6)); } catch { continue; }
            if (j.type === 'meta') { agentSessionId = j.session_id; }
            else if (j.type === 'plan') { renderPlan(j.plan); }
            else if (j.type === 'iframe') {
              const empty = messagesEl.querySelector('.empty'); if (empty) empty.remove();
              const wrap = document.createElement('div');
              wrap.className = 'msg assistant iframe-msg';
              const head = document.createElement('div');
              head.className = 'iframe-head';
              head.textContent = j.title || 'iframe';
              const ifr = document.createElement('iframe');
              ifr.sandbox = 'allow-scripts';
              ifr.referrerPolicy = 'no-referrer';
              ifr.srcdoc = j.html || '';
              ifr.style.width = '100%';
              ifr.style.minHeight = '300px';
              ifr.style.border = '0';
              ifr.style.borderRadius = '10px';
              ifr.style.background = '#fff';
              wrap.appendChild(head); wrap.appendChild(ifr);
              messagesEl.appendChild(wrap);
              messagesEl.parentElement.scrollTop = messagesEl.parentElement.scrollHeight;
            }
            else if (j.type === 'text') {
              ensureText(); acc += j.delta; curText.txt.textContent = acc; curText.txt.dataset.raw = acc;
              messagesEl.parentElement.scrollTop = messagesEl.parentElement.scrollHeight;
            }
            else if (j.type === 'tool_call') {
              if (curText) {
                curText.wrap.classList.remove('typing');
                if (acc) renderMarkdown(curText.txt, acc);
                curText = null;
              }
              const card = addAgentToolCard(j.id, j.name);
              card.querySelector('.tool-input').textContent = JSON.stringify(j.input, null, 2);
              let sum = (j.input.path || j.input.command || j.input.pattern || '').toString();
              if (j.name === 'use_subagent') {
                const subs = j.input?.content?.subagents || [];
                sum = j.input?.command === 'ListAgents'
                  ? 'ListAgents'
                  : `${subs.length} ✕ ${t('agent_tool')}`;
              }
              card.querySelector('.tool-summary').textContent = sum.slice(0, 80);
              cards.set(j.id, card);
              // mark if the agent is restarting the service — the SSE will die
              const _cmd = (j.input?.command || '').toString();
              if (_cmd.includes('/admin/restart') || _cmd.includes('systemctl restart webchat')) {
                sawRestart = true;
              }
            }
            else if (j.type === 'subagent_start') {
              const card = cards.get(j.parent_id);
              if (card) {
                const list = document.createElement('div');
                list.className = 'subagent-list';
                list.innerHTML = j.queries.map((q,i) =>
                  `<div class="subagent-item pending" data-idx="${i}">
                     <span class="sa-spinner">○</span>
                     <span class="sa-query"></span>
                     <span class="sa-preview" style="color:var(--muted);font-size:11px"></span>
                   </div>`).join('');
                list.querySelectorAll('.subagent-item').forEach((it,i) => {
                  it.querySelector('.sa-query').textContent = j.queries[i].slice(0, 120);
                });
                card.appendChild(list);
                card.classList.add('open');
              }
            }
            else if (j.type === 'subagent_done') {
              const card = cards.get(j.parent_id);
              if (card) {
                const item = card.querySelector(`.subagent-item[data-idx="${j.index}"]`);
                if (item) {
                  item.classList.remove('pending');
                  item.classList.add(j.status);
                  item.querySelector('.sa-spinner').textContent = j.status === 'success' ? '●' : '✕';
                  item.querySelector('.sa-preview').textContent = ' – ' + (j.preview || '').slice(0, 160);
                }
              }
            }
            else if (j.type === 'critic') {
              const card = cards.get(j.id);
              if (card) {
                let bar = card.querySelector('.tool-critic');
                if (!bar) {
                  bar = document.createElement('div');
                  bar.className = 'tool-critic';
                  bar.style.cssText = 'margin:6px 0;padding:6px 10px;border-radius:6px;font-size:12px;';
                  card.insertBefore(bar, card.querySelector('.out-wrap') || null);
                }
                const ok = j.verdict !== 'BLOCK';
                bar.style.background = ok ? 'rgba(76,175,80,0.10)' : 'rgba(255,82,82,0.10)';
                bar.style.borderLeft = `3px solid ${ok ? '#4caf50' : '#ff5252'}`;
                bar.style.color = ok ? '#9ccc65' : '#ff8a80';
                const issues = (j.issues || []).slice(0, 5)
                  .map(s => '<div style="opacity:.8">· ' + s.replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c])) + '</div>').join('');
                bar.innerHTML = `<b>🔎 Critic: ${j.verdict}</b>` +
                  (j.reason ? ' — ' + j.reason : '') + issues;
              }
            }
            else if (j.type === 'dev_loop_iter' || j.type === 'dev_loop_test' || j.type === 'dev_loop_done') {
              const card = cards.get(j.parent_id);
              if (card) {
                let bar = card.querySelector('.dev-loop');
                if (!bar) {
                  bar = document.createElement('div');
                  bar.className = 'dev-loop';
                  bar.style.cssText = 'margin:8px 0;padding:8px 10px;background:rgba(126,87,194,0.08);border-left:3px solid #7e57c2;border-radius:4px;font-size:12px;font-family:monospace;';
                  card.insertBefore(bar, card.querySelector('.out-wrap') || null);
                }
                const line = document.createElement('div');
                if (j.type === 'dev_loop_iter') {
                  const icon = j.action === 'edit' ? '✏️' : '🧪';
                  line.textContent = `${icon} iter ${j.n}/${j.max}: ${j.summary}`;
                } else if (j.type === 'dev_loop_test') {
                  line.style.color = j.passed ? '#4caf50' : '#e57373';
                  line.textContent = `  → ${j.passed ? 'PASS' : 'FAIL'} ${j.summary || ''}`;
                } else {
                  line.style.fontWeight = 'bold';
                  line.style.color = j.ok ? '#4caf50' : '#e57373';
                  line.textContent = `${j.ok ? '✅' : '❌'} dev_loop ${j.summary}`;
                }
                bar.appendChild(line);
              }
            }
            else if (j.type === 'hook') {
              const card = cards.get(j.id);
              if (card) {
                let bar = card.querySelector('.tool-hooks');
                if (!bar) {
                  bar = document.createElement('div');
                  bar.className = 'tool-hooks';
                  bar.style.cssText = 'display:flex;flex-wrap:wrap;gap:4px;margin:4px 0;font-size:11px;';
                  card.insertBefore(bar, card.querySelector('.out-wrap') || null);
                }
                const badge = document.createElement('span');
                const at = j.action_type;
                const color = at === 'deny' ? '#ff5252' :
                              (at === 'shell' ? '#42a5f5' : '#9aa');
                badge.style.cssText = `background:${color}22;color:${color};border:1px solid ${color}66;padding:1px 6px;border-radius:6px;`;
                const icon = at === 'deny' ? '⛔' : (at === 'shell' ? '⚡' : 'hook');
                badge.textContent = `${icon} ${j.hook_id || ''} ${j.message ? '· ' + j.message.slice(0, 80) : ''}`;
                badge.title = `${j.event} → ${j.type}: ${j.message || ''}`;
                bar.appendChild(badge);
              }
            }
            else if (j.type === 'tool_result') {
              const card = cards.get(j.id);
              if (card) {
                card.classList.remove('running');
                card.classList.add(j.status === 'success' ? 'success' : 'error');
                card.querySelector('.tool-status').textContent = j.status === 'success' ? t('agent_success') : t('agent_error');
                const outEl = card.querySelector('.tool-output');
                outEl.textContent = (j.output || '').slice(0, 8000);
                card.querySelector('.out-wrap').style.display = '';
                // Special-case: browser_screenshot → embed the image inline
                if (card.querySelector('.tool-name').textContent === 'browser_screenshot'
                    && j.status === 'success' && agentSessionId) {
                  const m = (j.output || '').match(/saved to (\S+)/);
                  if (m) {
                    const rel = m[1].replace(/^\/workspace\//, '');
                    const img = document.createElement('img');
                    img.src = `/agent/file/${agentSessionId}/${rel}?t=${Date.now()}`;
                    img.style.maxWidth = '100%';
                    img.style.borderRadius = '8px';
                    img.style.marginTop = '8px';
                    img.style.display = 'block';
                    card.querySelector('.out-wrap').appendChild(img);
                  }
                }
                // diff panel for fs_write edits
                if (j.diff) {
                  attachDiff(card, j.diff, j.diff_lines || 0, j.action_id,
                             (j.output && (j.output.match(/(?:Replaced 1 occurrence in|Created|Appended \d+ chars to|Inserted after line \d+ in) (\S+)/) || [])[1]) || '');
                } else if (j.action_id && j.backup) {
                  // No diff payload (e.g. binary/big) but we still have a rollback target.
                  attachRollbackOnly(card, j.action_id);
                }
              }
            }
            else if (j.type === 'stats') { addAgentStats(j); }
            else if (j.type === 'throttle') {
              const div = document.createElement('div');
              div.className = 'agent-stats';
              div.style.color = '#e6a23c';
              const reason = j.reason || '?';
              const sleep = j.sleep ? ` (${j.sleep}s)` : '';
              div.textContent = `⏳ ${lang === 'ru' ? 'ждём повтор' : 'retrying'} · ${reason}${sleep}`;
              messagesEl.appendChild(div);
              messagesEl.parentElement.scrollTop = messagesEl.parentElement.scrollHeight;
            }
            else if (j.type === 'error') {
              addMsg('assistant', `${t('error_prefix')}: ${j.message}`, null, { error: true });
            }
            else if (j.type === 'cancelled') {
              const div = document.createElement('div');
              div.className = 'agent-stats'; div.style.color = '#888';
              div.textContent = lang === 'ru' ? '⏹ остановлено сервером' : '⏹ stopped';
              messagesEl.appendChild(div);
            }
            else if (j.type === 'done') { if (agentMode) { setTimeout(() => { loadAgentSessions(); refreshAgentBudget(); }, 250); } }
          }
        }
        if (curText) {
          curText.wrap.classList.remove('typing');
          if (acc) renderMarkdown(curText.txt, acc);
        }
      } catch (err) {
        if (err.name === 'AbortError') {
          if (curText) curText.wrap.classList.remove('typing');
          const div = document.createElement('div');
          div.className = 'agent-stats'; div.style.color = '#888';
          div.textContent = lang === 'ru' ? '⏹ остановлено' : '⏹ stopped';
          messagesEl.appendChild(div);
          if (agentMode) setTimeout(() => { loadAgentSessions(); refreshAgentBudget(); }, 250);
        } else if (sawRestart) {
          // The agent restarted the service; the SSE was torn down by design.
          const div = document.createElement('div');
          div.className = 'agent-stats'; div.style.color = 'var(--orange)';
          div.textContent = lang === 'ru'
            ? '♻ сервис перезапущен — обнови страницу через ~3 секунды'
            : '♻ service restarted — reload the page in ~3 seconds';
          messagesEl.appendChild(div);
          // schedule auto-check
          setTimeout(async () => {
            try {
              const r = await fetch('/healthz');
              if (r.ok) {
                const tip = document.createElement('div');
                tip.className = 'agent-stats'; tip.style.color = '#6fdd8b';
                tip.textContent = lang === 'ru' ? '✅ сервис снова в сети' : '✅ service is back online';
                messagesEl.appendChild(tip);
                if (agentMode) { loadAgentSessions(); refreshAgentBudget(); }
              }
            } catch {}
          }, 3500);
        } else {
          addMsg('assistant', `${t('error_prefix')}: ${err.message || err}`, null, { error: true });
        }
      } finally {
        streaming = false; setSendBtnMode(false); agentAbort = null;
      }
    }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (streaming) {
        if (agentMode && agentSessionId) {
          fetch('/agent/stop/' + agentSessionId, { method: 'POST' }).catch(() => {});
        }
        if (agentAbort) { agentAbort.abort(); }
        return;
      }
      const text = input.value.trim();
      if (!text && pendingFiles.length === 0) return;
      if (text.startsWith('/') && handleSlashCommand(text)) return;
      if (agentMode) {
        const imgs = pendingFiles.filter(f => f.dataUrl).map(f => {
          const m = f.dataUrl.match(/^data:image\/(\w+);base64,(.+)$/);
          return m ? { format: m[1], data_base64: m[2] } : null;
        }).filter(Boolean);
        const filesSnapshot = pendingFiles.slice();
        pendingFiles = []; renderAttachments();
        // Text files: append their content to the prompt for the agent.
        let agentText = text;
        for (const f of filesSnapshot) if (f.text != null) agentText += `\n\n[file: ${f.name}]\n${f.text}`;
        return sendAgent(agentText, imgs);
      }
      streaming = true; sendBtn.disabled = true;
      const filesSnapshot = pendingFiles.slice();
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
      pendingFiles = []; renderAttachments();
      const asst = addMsg('assistant', '', null);
      asst.wrap.classList.add('typing');
      let acc = '';
      try {
        const r = await fetch('/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ messages: history, model: currentModel }),
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
                asst.txt.textContent = `${t('error_prefix')}: ` + (typeof j.error === 'string' ? j.error : JSON.stringify(j.error));
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
        if (!asst.txt.textContent) { asst.txt.textContent = `${t('error_prefix')}: ` + err.message; asst.wrap.classList.add('error'); }
      } finally {
        asst.wrap.classList.remove('typing');
        streaming = false; sendBtn.disabled = false; input.focus();
      }
    });

    /* export current session to .md */
    function downloadFile(name, content) {
      const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = name;
      document.body.appendChild(a); a.click();
      setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 0);
    }
    function exportChatToMd() {
      const c = chats.find(x => x.id === activeChatId);
      if (!c || !c.history || !c.history.length) { alert(t('export_empty')); return; }
      const lines = [`# ${c.title || 'Chat'}`, '', `_Model: ${c.model || currentModel || '?'}_`, ''];
      for (const m of c.history) {
        const txt = typeof m.content === 'string'
          ? m.content
          : (Array.isArray(m.content) ? (m.content.find(p => p.type === 'text')?.text || '') : '');
        lines.push(m.role === 'user' ? '## 👤 User' : '## 🤖 Assistant', '', txt, '');
      }
      const safeTitle = (c.title || 'chat').replace(/[^\w\-]+/g, '_').slice(0, 60) || 'chat';
      downloadFile(`${safeTitle}.md`, lines.join('\n'));
    }
    async function exportAgentToMd() {
      if (!agentSessionId) { alert(t('export_empty')); return; }
      try {
        const r = await fetch('/agent/sessions/' + agentSessionId);
        if (!r.ok) { alert(t('export_empty')); return; }
        const d = await r.json();
        const tr = d.transcript || [];
        if (!tr.length) { alert(t('export_empty')); return; }
        const lines = [`# Agent session ${d.sid}`, '', `_Model: ${d.model || '?'}_`, ''];
        for (const m of tr) {
          if (m.role === 'user') {
            lines.push('## 👤 User', '', m.text || '', '');
          } else if (m.role === 'assistant') {
            lines.push('## 🤖 Assistant', '', m.text || '', '');
          } else if (m.role === 'tool') {
            lines.push(`### 🔧 Tool: \`${m.name}\` [${m.status || '?'}]`, '');
            try {
              lines.push('```json', JSON.stringify(m.input || {}, null, 2), '```', '');
            } catch {}
            if (m.output) {
              lines.push('```', String(m.output).slice(0, 4000), '```', '');
            }
            if (Array.isArray(m.subagents) && m.subagents.length) {
              for (const sa of m.subagents) {
                lines.push(`- **Subagent #${(sa.index || 0) + 1}** [${sa.status}] ${sa.query || ''}`);
                if (sa.preview) lines.push(`  > ${(sa.preview || '').replace(/\n/g, ' ')}`);
              }
              lines.push('');
            }
          }
        }
        const safe = ((d.sid) || 'agent').toString().slice(0, 40);
        downloadFile(`agent_${safe}.md`, lines.join('\n'));
      } catch (e) { alert('Export failed: ' + e.message); }
    }
    document.getElementById('export-btn').addEventListener('click', () => {
      if (agentMode) exportAgentToMd();
      else exportChatToMd();
    });

    /* profile + settings (placeholders) */
    document.getElementById('settings-btn').addEventListener('click', () => {
      alert(t('settings') + t('section_dev'));
    });
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
