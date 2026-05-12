// Message composer + chat-side rendering.
//
// Phase 4c split. Six functions covering the input-side pipeline:
//   renderAttachments()                          — chip strip below input
//   ingestFiles(fileList)                        — paste/drop/file-input intake
//   buildUserContent(text, files)                — text|parts for /chat or /agent
//   attachCopyAction(wrap, getText, opts)        — Copy/Edit buttons on a .msg
//   editUserMessage(msgDiv)                      — chat-mode rewind to user msg
//   addMsg(role, displayText, files, opts)       — append rendered .msg to DOM
//
// `pendingFiles` lives inside the module; the form-submit handler in app.js
// gets to it via getPendingFiles() / clearPending(). All other state is
// reactive via getters in ctx.
//
// ctx:
//   t                       i18n translator
//   getLang                 () => 'ru' | 'en'
//   utils: { fmtSize, fileToDataUrl, copyToClipboard }
//   renderMarkdown          from markdown.js
//   dom: { attachmentsEl, messagesEl, input }
//   state: {
//     getStreaming, getAgentMode,
//     getHistory, setHistory,
//   }
//   fns: { persistActive, renderChatList }
//
// Returns: { renderAttachments, ingestFiles, buildUserContent,
//            attachCopyAction, editUserMessage, addMsg,
//            getPendingFiles, clearPending }

export function createComposer(ctx) {
  const { t, getLang, utils, renderMarkdown, dom, state, fns } = ctx;
  const { fmtSize, fileToDataUrl, copyToClipboard } = utils;
  const { attachmentsEl, messagesEl, input } = dom;
  const { getStreaming, getAgentMode, getHistory, setHistory } = state;
  const { persistActive, renderChatList } = fns;

  let pendingFiles = [];

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

  function attachCopyAction(wrap, getText, opts = {}) {
    const lang = getLang();
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
    // On touch devices toggle a .show-actions class on tap so the buttons
    // appear above the bubble instead of always overlapping the text.
    if (window.matchMedia && window.matchMedia('(hover: none)').matches) {
      wrap.addEventListener('click', (e) => {
        if (e.target.closest('.msg-actions')) return;
        const wasShown = wrap.classList.contains('show-actions');
        for (const m of document.querySelectorAll('.msg.show-actions')) {
          if (m !== wrap) m.classList.remove('show-actions');
        }
        wrap.classList.toggle('show-actions', !wasShown);
      });
    }
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
    if (getStreaming()) return;
    // count which user msg this is (0-based among user msgs in DOM)
    const userMsgs = [...messagesEl.querySelectorAll('.msg.user')];
    const idx = userMsgs.indexOf(msgDiv);
    if (idx < 0) return;
    // find same-index user msg in history
    const history = getHistory();
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
    setHistory(history.slice(0, hi));
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
      const editable = role === 'user' && !getAgentMode() && !opts.noEdit;
      attachCopyAction(div, () => txt.dataset.raw || txt.textContent || '', {
        editable,
        onEdit: editable ? () => editUserMessage(div) : null,
      });
    }
    messagesEl.appendChild(div);
    messagesEl.parentElement.scrollTop = messagesEl.parentElement.scrollHeight;
    return { wrap: div, txt };
  }

  function getPendingFiles() { return pendingFiles; }
  function clearPending() { pendingFiles = []; renderAttachments(); }

  return {
    renderAttachments, ingestFiles, buildUserContent,
    attachCopyAction, editUserMessage, addMsg,
    getPendingFiles, clearPending,
  };
}
