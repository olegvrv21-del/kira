---
name: notebook-backup
description: Use when Oleg asks to back up notebook or before risky operations. Commits and pushes ~/notebook to kira-vault.
---

## Trigger

Use when Oleg says "сделай бэкап", "сохрани заметки", "backup notebook", "пушни в vault", or before any risky operation that touches the host.

Also REQUIRED to suggest after major events:
- Crossed +5 PRs in a day
- Edited HANDOFF.md or PHRASES.md
- Restored from a backup

## Что сделать

### Шаг 1 — git status в kira-vault

```bash
cd /home/exedev/notebook
git status -s
```

Если ничего не изменилось — сообщить Олегу "нечего бэкапить, всё уже в vault" и стоп.

### Шаг 2 — commit + push

```bash
cd /home/exedev/notebook
git add -A
git commit -m "notebook: snapshot $(date +%Y-%m-%d)"
git push
```

Если push падает с 403 — это известная ловушка с `http.extraheader`:
```bash
cd /home/exedev/notebook
git config --unset-all http."https://github.com/".extraheader
git push
```

### Шаг 3 — Подтверди Олегу

Короткое сообщение:
```
✓ notebook бэкапнут в kira-vault (commit abc1234)
Изменено: HANDOFF.md, JOURNAL.md
Размер vault: ~200 KB
Доступ: github.com/olegvrv21-del/kira-vault
```

## Дополнительно (если попросит "полный бэкап")

Помимо notebook, упомянуть что также:
- `/home/exedev/webchat/agent_sessions.db` — это база сессий и кредитов. SQLite файл, можно скопировать `cp /home/exedev/webchat/agent_sessions.db /tmp/backup-$(date +%s).db`
- `/home/exedev/webchat/MEMORY.md` — Кирина память (отдельный файл от notebook/MEMORY.md)
- `/etc/systemd/system/webchat.service.d/override.conf` — env-переменные и токены (sudo нужен)

Но НЕ делай этот полный бэкап без явной просьбы — он медленный и трогает sudo.

## Anti-patterns

- ❌ Бэкапить если git status пустой
- ❌ Force-push (`git push -f`) — никогда без явной команды Олега
- ❌ Удалять старые бэкапы автоматически
