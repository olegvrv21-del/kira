// Tool-card UI builders — DOM constructors for one row in the agent transcript.
//
// Pulled out of app.js phase 4 split. Each function is a pure DOM builder
// (no module-level state); the caller passes a context object with the
// translator `t` and the `messagesEl` it should append into.
//
// Five exports, all returned together by `createToolCards(ctx)`:
//   addAgentToolCard(toolUseId, name)       -> <div.tool-card>
//   attachDiff(card, diff, lines, aid, fp)  -> appends collapsible diff
//   attachRollbackOnly(card, actionId)      -> rollback button only
//   fetchActionDiff(actionId)               -> GET /agent/actions/{id}
//   addAgentStats(s)                        -> stats line in the transcript
//
// ctx shape: { t, messagesEl }   (both required; `t` is the i18n translator)

export function createToolCards(ctx) {
  const { t, messagesEl } = ctx;
  if (!t || !messagesEl) {
    throw new Error('createToolCards: t and messagesEl are required');
  }

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

  return { addAgentToolCard, attachDiff, attachRollbackOnly, fetchActionDiff, addAgentStats };
}
