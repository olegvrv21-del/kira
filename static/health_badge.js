// Health/spend indicator chip. Polls /agent/health and shows the day's LLM
// spend against the daily limit, plus a warning state when the provider
// balance is exhausted or health is degraded/critical.
//
// NOTE: Unity2 exposes no real dollar-balance endpoint, so this reflects
// Kira's *internal* spend counter (credits/day vs day_limit) and the
// llm_fallback.balance_exhausted flag — the honest signals we actually have.

const STR = {
  ru: {
    spend:      (d, l) => l > 0 ? `$${d.toFixed(2)} / ${l.toFixed(0)}` : `$${d.toFixed(2)}`,
    exhausted:  '⚠ баланс исчерпан',
    degraded:   'внимание',
    loading:    '…',
    tip_day:    'Расход за день',
    tip_month:  'За месяц',
    tip_status: 'Статус',
    tip_note:   'Показан внутренний счётчик расхода Kira (реального $-баланса провайдер не отдаёт).',
  },
  en: {
    spend:      (d, l) => l > 0 ? `$${d.toFixed(2)} / ${l.toFixed(0)}` : `$${d.toFixed(2)}`,
    exhausted:  '⚠ balance exhausted',
    degraded:   'attention',
    loading:    '…',
    tip_day:    'Spend today',
    tip_month:  'This month',
    tip_status: 'Status',
    tip_note:   'Shows Kira’s internal spend counter (the provider exposes no real $ balance).',
  },
};

/**
 * @param {object} opts
 * @param {HTMLElement} opts.el  chip container element
 * @param {() => string} opts.getLang
 * @param {number} [opts.intervalMs=60000]
 */
export function initHealthBadge({ el, getLang, intervalMs = 60000 }) {
  if (!el) return { refresh: () => {} };
  let timer = null;

  function paint(h) {
    const lang = getLang();
    const S = STR[lang] || STR.ru;
    if (!h) { el.textContent = S.loading; el.className = 'health-chip'; el.title = ''; return; }
    const c = h.credits || {};
    const fb = h.llm_fallback || {};
    const day = Number(c.day || 0);
    const dayLimit = Number(c.day_limit || 0);
    const month = Number(c.month || 0);
    const monthLimit = Number(c.month_limit || 0);
    const status = h.status || 'ok';
    const exhausted = !!fb.balance_exhausted;

    let cls = 'health-chip';
    let label = S.spend(day, dayLimit);
    if (exhausted) { cls += ' danger'; label = S.exhausted; }
    else if (status === 'critical') { cls += ' danger'; }
    else if (status === 'degraded') { cls += ' warn'; }
    el.className = cls;
    el.textContent = label;

    const reasons = Array.isArray(h.reasons) ? h.reasons : [];
    el.title =
      `${S.tip_day}: $${day.toFixed(2)}` + (dayLimit > 0 ? ` / ${dayLimit.toFixed(0)}` : '') + '\n' +
      `${S.tip_month}: $${month.toFixed(2)}` + (monthLimit > 0 ? ` / ${monthLimit.toFixed(0)}` : '') + '\n' +
      `${S.tip_status}: ${status}` + (reasons.length ? ` (${reasons.join('; ')})` : '') + '\n\n' +
      S.tip_note;
  }

  async function refresh() {
    try {
      const r = await fetch('/agent/health', { cache: 'no-store' });
      if (!r.ok) { paint(null); return; }
      paint(await r.json());
    } catch (_) {
      paint(null);
    }
  }

  paint(null);
  refresh();
  timer = setInterval(refresh, intervalMs);
  window.addEventListener('beforeunload', () => timer && clearInterval(timer));
  return { refresh, repaintLang: () => refresh() };
}
