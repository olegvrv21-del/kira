// Skills modal. Loads /skills + /skills/:name and renders into a passed-in
// modal element. Also supports creating new skills via POST /skills.
//
// Usage:
//   import { initSkills } from './skills.js';
//   const skills = initSkills({ modal, body, title, back, t });
//   skills.open();

export function initSkills({ modal, body, title, back, t }) {

  function tt(key, fallback) {
    // small wrapper — if i18n key missing, fall back to provided text.
    try { const v = t(key); if (v && v !== key) return v; } catch (e) {}
    return fallback;
  }

  async function open() {
    modal.classList.add('show');
    title.textContent = tt('nav_skills', 'Навыки');
    back.style.display = 'none';
    body.innerHTML = '<div style="color:var(--muted)">…</div>';
    try {
      const r = await fetch('/skills');
      const d = await r.json();
      body.innerHTML = '';

      // "Create new skill" tile is always first.
      const createBtn = document.createElement('div');
      createBtn.className = 'skill-row skill-create-row';
      createBtn.innerHTML = `
        <div class="sk-name">+ ${tt('skill_create_btn', 'Создать новый навык')}</div>
        <div class="sk-desc">${tt('skill_create_hint', 'Напиши, в каких ситуациях Кире использовать этот навык. Подсказки появятся в форме.')}</div>
      `;
      createBtn.addEventListener('click', () => openCreateForm());
      body.appendChild(createBtn);

      if (!d.skills || !d.skills.length) {
        const empty = document.createElement('div');
        empty.style.color = 'var(--muted)';
        empty.style.marginTop = '12px';
        empty.textContent = tt('skills_empty', 'Навыки не найдены.');
        body.appendChild(empty);
        return;
      }
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

  function openCreateForm() {
    title.textContent = tt('skill_create_title', 'Новый навык');
    back.style.display = '';
    body.innerHTML = '';

    // The form. Every field has an inline hint that explains "what this is for"
    // in plain language — Oleg doesn\'t read code, so hints are user-level.
    const form = document.createElement('div');
    form.className = 'skill-form';
    form.innerHTML = `
      <div class="sf-section">
        <label class="sf-label">${tt('skill_name_label', 'Имя навыка')}</label>
        <input class="sf-input" id="sf-name" placeholder="например: morning-digest" maxlength="40" />
        <div class="sf-hint">
          ${tt('skill_name_hint', 'Короткое английское имя через дефис: только буквы a-z, цифры и тире. Это имя файла, его потом не поменять.')}
        </div>
      </div>

      <div class="sf-section">
        <label class="sf-label">${tt('skill_desc_label', 'Когда использовать (одна строка)')}</label>
        <input class="sf-input" id="sf-desc" placeholder="${tt('skill_desc_ph','например: Use when the user asks for a daily summary in plain Russian.')}" maxlength="300" />
        <div class="sf-hint">
          ${tt('skill_desc_hint', 'Самое важное поле. Кира решает использовать навык именно по этому описанию. Опиши <b>триггер</b> — в какой ситуации этот навык включается. По-английски лучше, но не критично.')}
        </div>
      </div>

      <div class="sf-section">
        <label class="sf-label">${tt('skill_body_label', 'Содержание навыка (инструкции)')}</label>
        <textarea class="sf-textarea" id="sf-body" rows="14" placeholder="${tt('skill_body_ph', '## Шаги\n1. Прочитай ~/notebook/JOURNAL.md за последние сутки\n2. Сделай 3-4 короткие фразы по-русски\n3. Запиши в ~/notebook/SUMMARY-<дата>.md')}"></textarea>
        <div class="sf-hint">
          ${tt('skill_body_hint', 'Текст-инструкция для Киры. Пиши шагами или абзацами, простым языком. Это её "памятка" — что делать когда навык активирован. Можно по-русски.')}
        </div>
      </div>

      <div class="sf-tips">
        <div class="sf-tips-title">💡 ${tt('skill_tips_title', 'Подсказки')}</div>
        <ul>
          <li>${tt('skill_tip1', '<b>Имя</b> придумывается раз и навсегда — выбирай со смыслом (что делает навык).')}</li>
          <li>${tt('skill_tip2', '<b>Описание</b> должно начинаться со слов "Use when..." — Кира ищет триггер именно так.')}</li>
          <li>${tt('skill_tip3', '<b>Содержание</b> — это <i>не</i> код, это инструкция словами. Что прочитать, что сделать, в каком порядке.')}</li>
          <li>${tt('skill_tip4', 'Чем конкретнее триггер, тем чаще навык сработает в нужный момент. Размытые описания Кира игнорирует.')}</li>
          <li>${tt('skill_tip5', 'После сохранения навык появится в списке. Если что-то не так — попроси Киру удалить файл через чат.')}</li>
        </ul>
      </div>

      <div class="sf-actions">
        <button type="button" id="sf-cancel">${tt('skill_cancel', 'Отмена')}</button>
        <button type="button" id="sf-save" class="primary">${tt('skill_save', 'Создать')}</button>
      </div>

      <div class="sf-result" id="sf-result"></div>
    `;
    body.appendChild(form);

    const $ = (id) => document.getElementById(id);
    $('sf-cancel').addEventListener('click', () => open());
    $('sf-save').addEventListener('click', async () => {
      const name = $('sf-name').value.trim();
      const description = $('sf-desc').value.trim();
      const bodyTxt = $('sf-body').value;
      const out = $('sf-result');
      out.className = 'sf-result';
      out.textContent = tt('skill_saving', 'Сохраняю…');
      try {
        const r = await fetch('/skills', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, description, body: bodyTxt }),
        });
        const d = await r.json();
        if (!d.ok) {
          out.classList.add('error');
          out.textContent = '✗ ' + (d.error || 'ошибка');
          return;
        }
        out.classList.add('ok');
        out.textContent = '✓ ' + tt('skill_saved', 'Навык создан') + ': ' + d.file;
        // brief delay so the user sees the success, then refresh list
        setTimeout(() => open(), 900);
      } catch (e) {
        out.classList.add('error');
        out.textContent = '✗ ' + String(e);
      }
    });
  }

  return { open, detail, openCreateForm };
}
