// Markdown exporters for chat / agent sessions.
//
// Phase 4c split. Two pure functions, each reads its source and calls
// `downloadFile(filename, content)` from utils.js. No global state.
//
// ctx:
//   t                    i18n translator
//   downloadFile         from utils.js
//   safeFilename         from utils.js
//   getChats             () => chats[]
//   getActiveChatId      () => str | null
//   getCurrentModel      () => str | null
//   getAgentSessionId    () => str | null
//
// Returns: { exportChatToMd, exportAgentToMd }

export function createExporters(ctx) {
  const { t, downloadFile, safeFilename,
          getChats, getActiveChatId, getCurrentModel, getAgentSessionId } = ctx;

  function exportChatToMd() {
    const chats = getChats();
    const c = chats.find(x => x.id === getActiveChatId());
    if (!c || !c.history || !c.history.length) { alert(t('export_empty')); return; }
    const lines = [`# ${c.title || 'Chat'}`, '', `_Model: ${c.model || getCurrentModel() || '?'}_`, ''];
    for (const m of c.history) {
      const txt = typeof m.content === 'string'
        ? m.content
        : (Array.isArray(m.content) ? (m.content.find(p => p.type === 'text')?.text || '') : '');
      lines.push(m.role === 'user' ? '## 👤 User' : '## 🤖 Assistant', '', txt, '');
    }
    const safeTitle = safeFilename(c.title || 'chat');
    downloadFile(`${safeTitle}.md`, lines.join('\n'));
  }

  async function exportAgentToMd() {
    const sid = getAgentSessionId();
    if (!sid) { alert(t('export_empty')); return; }
    try {
      const r = await fetch('/agent/sessions/' + sid);
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

  return { exportChatToMd, exportAgentToMd };
}
