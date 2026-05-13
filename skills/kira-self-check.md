---
name: kira-self-check
description: Use when Oleg asks how Kira is doing or for a status check. Returns a short human-readable health snapshot with 🟢🟡🔴 markers.
---

## Trigger

Use when Oleg asks "как ты", "что у тебя", "проверь себя", "всё ли в порядке", "self check", "статус", or after a known incident (deploy failure, freeze, restart).

## Что проверить (в этом порядке)

1. **freeze status** — `GET /agent/freeze` (или просто чтение файла `/home/exedev/webchat/.frozen`). Если frozen — это критично, сообщить сразу.

2. **self_status** (свой tool) — git HEAD + последние 5 commits + test count + coverage + uptime + in-flight sessions + список tools.

3. **webchat health** — `prod_observe what=systemctl_status unit=webchat`. Если не `active (running)` — главная проблема.

4. **последние ошибки в journal** — `prod_observe what=journalctl lines=50 pattern=ERROR`. Если за последний час есть нетривиальные — упомянуть.

5. **CI status** — `gh run list -L 5` (через execute_bash). Если последние runs красные — это сигнал.

## Формат ответа Олегу

Короткий, человекочитаемый. Максимум 5 пунктов. Например:

```
Состояние:
- 🟢 жива и работает (uptime: 4ч 12м)
- 🟢 не заморожена
- 🟢 последний deploy: 17:33, коммит abc1234 (PR #11 kill-switch)
- 🟡 в журнале за час 3 предупреждения про timeout — не критично
- 🟢 CI зелёный на main
- Тестов: 890, coverage: 94.3%
```

Используй эмодзи 🟢🟡🔴 чтобы Олег визуально сканировал.

## Anti-patterns

- ❌ Дамп всего вывода `self_status` без интерпретации
- ❌ "Всё хорошо" без проверки (минимум 3 из 5 пунктов выше)
- ❌ Технические термины (RSS, fd, deadlock) без объяснения
