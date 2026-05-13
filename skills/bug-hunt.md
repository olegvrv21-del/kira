---
name: bug-hunt
description: Use when Oleg asks Kira to find a real bug (Tier-3 iteration). Forces one zone, repro before PR, regression tests, teaching-mode explanation.
---

## Trigger

Use when Oleg says "найди баг", "поищи bug", "потрать N turns на поиск", "Tier-3 итерация", "bug hunt". Also when explicitly asked to audit a specific module.

## Стратегия (важно: не сжигать turns на разведку)

### Шаг 1 — Выбери ОДНУ зону

Не сканируй весь репозиторий. Выбери одну из:
- `agent_runtime.py` (стрим, cancel, plan-loop)
- `agent_store.py` (SQLite, TTL, ownership, race на UPSERT)
- `llm/q_provider.py` / `llm/openrouter_provider.py` (протокольные нюансы)
- `agent_pr.py` / `agent_prod.py` / `agent_hooks.py` (свежий код, мало пробега)
- `app.py` (cost limits, SSE, session cache, multi-user изоляция)

Если "за 10 turns ничего убедительного" — переключайся, не залипай.

### Шаг 2 — Прицельное чтение

`outline` → потом `fs_read` узкими диапазонами (50-150 строк). НЕ читай по 500+ строк сразу — это пожирает контекст.

Что искать (паттерны реальных багов):
- race conditions (две async-операции пишут в один state без lock)
- забытые `await`
- mutation в итерации (`for k in d: del d[k]`)
- `except Exception: pass` без логирования (silent swallow)
- off-by-one в slicing/range
- неинициализированные переменные при ошибке (try без else)
- ресурс-лики (open file без close в exception path)
- неправильная обработка пустых строк / None / отрицательных чисел
- ownership / authz граничные случаи (NULL trick, как PR #10)

### Шаг 3 — Repro обязателен

Прежде чем писать фикс — `execute_bash` с минимальным reproducer. Без repro = нет уверенности что баг настоящий. Если не получается воспроизвести — это **не баг**, это гипотеза.

### Шаг 4 — Фикс + регрессионный тест

- Минимальный fix (1-5 строк)
- 2-4 регрессионных теста (положительные + граничные)
- `verify_change` чтобы проверить компиляцию + наличие паттерна
- Полный `pytest` локально

### Шаг 5 — PR через gh_pr_open

- branch=`kira/<slug>` (slug = краткое имя бага через дефис)
- files=[файл-с-фиксом, файл-с-тестом]
- body описывает: что баг, как воспроизвести, как починила, ссылка на repro
- НЕ трогай `.github/workflows/*` — guardrail заблокирует

### Шаг 6 — Объясни Олегу (REQUIRED)

После открытия PR — 4 пункта в teaching mode:
1. Что изменилось (эффект, не код)
2. Почему важно (какая боль была)
3. Как сам мог бы заметить такое в будущем (конкретный сигнал)
4. Что теперь умеешь нового (если capability добавилась; пропусти если чистый багфикс)

## Anti-patterns

- ❌ Открыть PR с косметикой ("можно отрефакторить", "стиль") — Олег просил **реальные** баги, не nitpicks
- ❌ Открыть PR без repro — даже если код выглядит подозрительно
- ❌ "Не нашла, но вот 10 идей" — лучше честное "не нашла, проверила X, Y, Z"
- ❌ Тратить >15 turns на разведку без переключения зоны

## История успешных итераций

- Iter #1 — PR #8 fix critic IndexError (пустой REASON)
- Iter #2 — PR #10 cross-user session leak (security, ownership NULL trick)
- Iter #3 — попутно нашла под-баг в `updated_at` (ещё не закрыт)

Это твой baseline качества: либо такого уровня баг, либо честное "не нашла".
