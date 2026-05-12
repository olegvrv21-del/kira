// Skills modal. Loads /skills + /skills/:name and renders into a passed-in
// modal element. Decoupled from app.js state.
//
// Usage:
//   import { initSkills } from './skills.js';
//   const skills = initSkills({ modal, body, title, back, t });
//   skills.open();

export function initSkills({ modal, body, title, back, t }) {
  async function open() {
    modal.classList.add('show');
    title.textContent = t('nav_skills');
    back.style.display = 'none';
    body.innerHTML = '<div style="color:var(--muted)">…</div>';
    try {
      const r = await fetch('/skills');
      const d = await r.json();
      if (!d.skills || !d.skills.length) {
        body.innerHTML = `<div style="color:var(--muted)">${t('skills_empty')}</div>`;
        return;
      }
      body.innerHTML = '';
      for (const s of d.skills) {
        const row = document.createElement('div');
        row.className = 'skill-row';
        row.innerHTML = `<div class="sk-name"></div><div class="sk-desc"></div>`;
        row.querySelector('.sk-name').textContent = s.name;
        row.querySelector('.sk-desc').textContent = s.description || '';
        row.addEventListener('click', () => detail(s.name));
        body.appendChild(row);
      }
    } catch (e) { body.textContent = String(e); }
  }

  async function detail(name) {
    title.textContent = name;
    back.style.display = '';
    body.innerHTML = '<div style="color:var(--muted)">…</div>';
    try {
      const r = await fetch('/skills/' + encodeURIComponent(name));
      const d = await r.json();
      const pre = document.createElement('div');
      pre.className = 'skill-body';
      pre.textContent = d.body || '';
      body.innerHTML = '';
      body.appendChild(pre);
    } catch (e) { body.textContent = String(e); }
  }

  return { open, detail };
}
