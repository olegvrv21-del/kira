.PHONY: test smoke compile all

PY = ./.venv/bin/python
PYTEST = ./.venv/bin/pytest

test:
	$(PYTEST) -q tests

compile:
	$(PY) -m py_compile app.py agent_runtime.py agent_store.py agent_tools.py sandbox_runtime.py sandbox_tools.py agent_skills.py q_client.py

smoke:
	bash tests/smoke_live.sh

backup:
	./ops/backup.sh

backup-list:
	@ls -1dt $${BACKUP_DIR:-$$HOME/backups}/20*/ 2>/dev/null | head

all: compile test smoke
