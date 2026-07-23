import { isNetworkError, waitForConnection } from './net_status.js';
// Agent SSE driver — POST /agent and stream responses into the chat.
function _authedFileUrl(sid, rel) {
  let tok = '';
  try { tok = localStorage.getItem('kira_auth_token') || ''; } catch (_) {}
  const q = `?t=${Date.now()}` + (tok ? `&token=${encodeURIComponent(tok)}` : '');
  return `/agent/file/${sid}/${rel}${q}`;
}

//
// Pulled out of app.js phase 3 split. The function is intentionally one
// giant switch over SSE event types: each event maps 1:1 to a DOM mutation.
// Refactoring further would mean inventing a renderer abstraction; not worth
// the indirection for a flat dispatch.
//
// Usage:
//   import { createAgentRunner } from './agent_sse.js';
//   const runAgent = createAgentRunner({
//     state, t, lang,                                  // reactive bag (mutable)
//     dom: { messagesEl, input },
//     fns: { addMsg, addAgentToolCard, addAgentStats,
//            attachDiff, attachRollbackOnly,
//            setSendBtnMode, renderPlan, renderMarkdown,
//            loadAgentSessions, refreshAgentBudget },
//   });
//   await runAgent(text, images);
//
// state shape (caller owns the lifetime; module just reads/writes):
//   state.streaming      : bool          (set true while running)
//   state.agentAbort     : AbortController|null
//   state.agentSessionId : str|null      (updated on `meta` event)
//   state.currentModel   : str
//   state.agentMode      : bool

export function createAgentRunner(opts) {
  const { state, dom, fns } = opts;
  let { t, lang } = opts;
  // Allow lang to be a live getter so the SSE labels stay current.
  const _lang = () => (typeof lang === 'function' ? lang() : lang);
  const _t    = (k) => (typeof t === 'function' ? t(k) : t[k] || k);

  return async function sendAgent(text, images) {
    const { messagesEl, input } = dom;
    const {
      addMsg, addAgentToolCard, addAgentStats,
      attachDiff, attachRollbackOnly,
      setSendBtnMode, renderPlan, renderMarkdown,
      loadAgentSessions, refreshAgentBudget,
    } = fns;

    state.streaming = true;
    setSendBtnMode(true);
    addMsg('user', text, null);
    input.value = ''; input.style.height = 'auto';

    let curText = null, acc = '';
    const ensureText = () => {
      if (!curText) {
        curText = addMsg('assistant', '', null);
        curText.wrap.classList.add('typing');
        acc = '';
      }
      return curText;
    };
    const cards = new Map();
    let sawRestart = false;
    state.agentAbort = new AbortController();

    try {
      const r = await fetch('/agent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: text,
          model: state.currentModel,
          session_id: state.agentSessionId,
          images: images || null,
        }),
        signal: state.agentAbort.signal,
      });
      if (!r.ok) {
        const err = await r.text();
        addMsg('assistant', `${_t('error_prefix')} ${r.status}: ${err}`, null, { error: true });
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
          if (j.type === 'meta') { state.agentSessionId = j.session_id; }
          else if (j.type === 'route') {
            const div = document.createElement('div');
            div.className = 'agent-stats';
            div.style.color = '#67c23a';
            const tierName = { simple: _lang() === 'ru' ? 'простой' : 'simple',
                               standard: _lang() === 'ru' ? 'обычный' : 'standard',
                               hard: _lang() === 'ru' ? 'сложный' : 'hard' }[j.tier] || j.tier;
            const pick = _lang() === 'ru' ? 'выбрана модель' : 'picked model';
            div.textContent = `⚡ ${pick}: ${j.model} · ${tierName}`;
            messagesEl.appendChild(div);
            messagesEl.parentElement.scrollTop = messagesEl.parentElement.scrollHeight;
          }
          else if (j.type === 'recall') {
            const div = document.createElement('div');
            div.className = 'agent-stats';
            div.style.color = '#909399';
            const files = (j.files || []).join(', ');
            const label = _lang() === 'ru' ? '\u{1f9e0} \u0432\u0441\u043f\u043e\u043c\u043d\u0438\u043b\u0430' : '\u{1f9e0} recalled';
            div.textContent = `${label}: ${j.count}` + (files ? ` \u00b7 ${files}` : '');
            messagesEl.appendChild(div);
            messagesEl.parentElement.scrollTop = messagesEl.parentElement.scrollHeight;
          }
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
            ensureText(); acc += j.delta;
            curText.txt.textContent = acc;
            curText.txt.dataset.raw = acc;
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
                : `${subs.length} ✕ ${_t('agent_tool')}`;
            }
            card.querySelector('.tool-summary').textContent = sum.slice(0, 80);
            cards.set(j.id, card);
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
                {
                  const ref = card.querySelector('.out-wrap');
                  if (ref && ref.parentNode === card) card.insertBefore(bar, ref);
                  else card.appendChild(bar);
                }
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
                {
                  const ref = card.querySelector('.out-wrap');
                  if (ref && ref.parentNode === card) card.insertBefore(bar, ref);
                  else card.appendChild(bar);
                }
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
                {
                  const ref = card.querySelector('.out-wrap');
                  if (ref && ref.parentNode === card) card.insertBefore(bar, ref);
                  else card.appendChild(bar);
                }
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
              card.querySelector('.tool-status').textContent = j.status === 'success' ? _t('agent_success') : _t('agent_error');
              const outEl = card.querySelector('.tool-output');
              outEl.textContent = (j.output || '').slice(0, 8000);
              card.querySelector('.out-wrap').style.display = '';
              if (card.querySelector('.tool-name').textContent === 'browser_screenshot'
                  && j.status === 'success' && state.agentSessionId) {
                const m = (j.output || '').match(/saved to (\S+)/);
                if (m) {
                  const rel = m[1].replace(/^\/workspace\//, '');
                  const src = _authedFileUrl(state.agentSessionId, rel);
                  const img = document.createElement('img');
                  img.src = src;
                  img.style.maxWidth = '100%';
                  img.style.borderRadius = '8px';
                  img.style.marginTop = '8px';
                  img.style.display = 'block';
                  card.querySelector('.out-wrap').appendChild(img);
                  // Auto-expand the tool card so the screenshot is visible
                  // without the user having to tap it open.
                  card.classList.add('open');
                  // Also emit a real standalone assistant message holding
                  // the screenshot, so it survives session reload and is
                  // unmissable even when the tool card is collapsed. The
                  // bubble lives in the normal message flow with copy/
                  // markdown semantics; clicking opens full-res in a tab.
                  const bubble = addMsg('assistant', '', null, { noEdit: true });
                  bubble.txt.textContent = '';
                  const link = document.createElement('a');
                  link.href = src;
                  link.target = '_blank';
                  link.rel = 'noopener';
                  link.style.cssText = 'display:block;max-width:480px';
                  const img2 = document.createElement('img');
                  img2.src = src;
                  img2.alt = rel.split('/').pop() || 'screenshot';
                  img2.title = _lang() === 'ru' ? 'Открыть в новой вкладке' : 'Open in new tab';
                  img2.style.cssText = 'width:100%;border-radius:10px;display:block;box-shadow:0 4px 16px rgba(0,0,0,0.35);cursor:zoom-in';
                  link.appendChild(img2);
                  bubble.txt.appendChild(link);
                  const cap = document.createElement('div');
                  cap.style.cssText = 'font-size:11px;color:var(--muted);margin-top:4px';
                  cap.textContent = `📷 ${img2.alt}`;
                  bubble.txt.appendChild(cap);
                }
              }
              if (j.diff) {
                attachDiff(card, j.diff, j.diff_lines || 0, j.action_id,
                           (j.output && (j.output.match(/(?:Replaced 1 occurrence in|Created|Appended \d+ chars to|Inserted after line \d+ in) (\S+)/) || [])[1]) || '');
              } else if (j.action_id && j.backup) {
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
            div.textContent = `⏳ ${_lang() === 'ru' ? 'ждём повтор' : 'retrying'} · ${reason}${sleep}`;
            messagesEl.appendChild(div);
            messagesEl.parentElement.scrollTop = messagesEl.parentElement.scrollHeight;
          }
          else if (j.type === 'error') {
            // Upstream HTTP errors (e.g. Q 400 ValidationException) ride on
            // `j.body` — append it verbatim so failures don't go silent.
            let msg = j.message || '';
            if (j.status) msg = `[HTTP ${j.status}] ${msg}`;
            if (j.body) msg += '\n\n' + (typeof j.body === 'string' ? j.body : JSON.stringify(j.body, null, 2));
            addMsg('assistant', `${_t('error_prefix')}: ${msg}`, null, { error: true });
          }
          else if (j.type === 'cancelled') {
            const div = document.createElement('div');
            div.className = 'agent-stats'; div.style.color = '#888';
            div.textContent = _lang() === 'ru' ? '⏹ остановлено сервером' : '⏹ stopped';
            messagesEl.appendChild(div);
          }
          else if (j.type === 'done') {
            if (state.agentMode) {
              setTimeout(() => { loadAgentSessions(); refreshAgentBudget(); }, 250);
            }
          }
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
        div.textContent = _lang() === 'ru' ? '⏹ остановлено' : '⏹ stopped';
        messagesEl.appendChild(div);
        if (state.agentMode) setTimeout(() => { loadAgentSessions(); refreshAgentBudget(); }, 250);
      } else if (sawRestart || isNetworkError(err)) {
        // Either /admin/restart was observed mid-stream OR the fetch died
        // with TypeError ("Failed to fetch" / NetworkError / Load failed).
        // Both look the same to the user: connection broke. Show a single
        // retry banner that auto-clears on /healthz 200.
        if (curText) curText.wrap.classList.remove('typing');
        const ok = await waitForConnection(messagesEl, { lang: _lang() });
        if (ok && state.agentMode) {
          loadAgentSessions(); refreshAgentBudget();
        }
      } else {
        addMsg('assistant', `${_t('error_prefix')}: ${err.message || err}`, null, { error: true });
      }
    } finally {
      state.streaming = false;
      setSendBtnMode(false);
      state.agentAbort = null;
    }
  };
}
