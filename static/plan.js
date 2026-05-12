// Plan panel renderer. Pure DOM, no state.
//
// Usage:
//   import { renderPlan, clearPlan } from './plan.js';
//   renderPlan({ items: [...] }, { panel, list, counter });

export function renderPlan(plan, els) {
  const { panel, list, counter } = els;
  const items = (plan && plan.items) || [];
  if (!items.length) { panel.style.display = 'none'; list.innerHTML = ''; return; }
  panel.style.display = '';
  list.innerHTML = '';
  let done = 0;
  for (const it of items) {
    const d = it || {};
    const li = document.createElement('li');
    li.className = 'plan-item ' + (d.status || 'pending');
    const mark = d.status === 'done' ? '✔'
               : d.status === 'in_progress' ? '▸'
               : d.status === 'skipped' ? '—' : '·';
    if (d.status === 'done') done++;
    li.innerHTML = `<span class="plan-mark">${mark}</span><span class="plan-text"></span>`;
    li.querySelector('.plan-text').textContent = d.text || '';
    list.appendChild(li);
  }
  counter.textContent = `${done}/${items.length}`;
}

export function clearPlan(els) {
  renderPlan({ items: [] }, els);
}
