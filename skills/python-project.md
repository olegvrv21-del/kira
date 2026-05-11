---
name: python-project
description: Use when scaffolding a Python project, setting up a venv, or installing pip dependencies.
---

## Virtualenv

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
```

Always pin runtime deps in `requirements.txt` (or `pyproject.toml`).

## FastAPI starter

```python
from fastapi import FastAPI
app = FastAPI()

@app.get("/healthz")
async def healthz():
    return {"ok": True}
```

Run:

```bash
.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
```

## Testing

```bash
pip install pytest
pytest -q
```

Prefer `pytest` over `unittest` for new code. Keep tests next to source as `test_*.py`.

## Code style

- Pure functions over stateful classes when possible.
- Type-hint public APIs.
- `from __future__ import annotations` if targeting Python 3.10+.
- f-strings, not `.format()` or `%`.
