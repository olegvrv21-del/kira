"""Long-term memory over the notebook directory.

Indexes Markdown files into paragraph-sized chunks and serves BM25 ranked
search. Cheap, dependency-free, refreshed on file mtime change.

Env:
  KIRA_NOTEBOOK_DIR    notebook root (default: ~/notebook).
  KIRA_MEMORY_EXCLUDE  comma-separated globs to skip (default: SECRETS*.md).

Public API:
  memory.search(query, k=5) -> list[{file, snippet, score, start_line, end_line}]
  memory.add(text, file=None) -> {file, lines, chunks}
  memory.status() -> {root, files, chunks, mtime, excluded}
"""

from __future__ import annotations

import fnmatch
import math
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path


def _root() -> Path:
    return Path(os.environ.get("KIRA_NOTEBOOK_DIR", str(Path.home() / "notebook"))).resolve()


def _excludes() -> list[str]:
    raw = os.environ.get("KIRA_MEMORY_EXCLUDE", "SECRETS*.md,*.secret.md")
    return [p.strip() for p in raw.split(",") if p.strip()]


_TOKEN = re.compile(r"[A-Za-z\u0400-\u04FF][A-Za-z0-9\u0400-\u04FF_-]+")
_STOP = set(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "by",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "и",
        "в",
        "на",
        "с",
        "о",
        "для",
        "к",
        "по",
        "от",
        "это",
        "эта",
        "этот",
        "как",
        "что",
    ]
)


def _tokenize(s: str) -> list[str]:
    return [w.lower() for w in _TOKEN.findall(s) if len(w) > 1 and w.lower() not in _STOP]


def _split_chunks(text: str, path: str) -> list[dict]:
    """Split markdown into paragraph chunks tagged with file/line range."""
    chunks: list[dict] = []
    lines = text.splitlines()
    buf: list[str] = []
    start = 1
    last_heading = ""
    for i, line in enumerate(lines, 1):
        if line.startswith("#"):
            if buf:
                body = "\n".join(buf).strip()
                if body:
                    chunks.append(
                        {"file": path, "start_line": start, "end_line": i - 1, "text": body, "heading": last_heading}
                    )
                buf, start = [], i
            last_heading = line.lstrip("# ").strip()
        if line.strip() == "" and buf and any(b.strip() for b in buf):
            body = "\n".join(buf).strip()
            if len(body) >= 12:  # skip tiny
                chunks.append(
                    {"file": path, "start_line": start, "end_line": i - 1, "text": body, "heading": last_heading}
                )
            buf, start = [], i + 1
            continue
        buf.append(line)
    if buf:
        body = "\n".join(buf).strip()
        if body:
            chunks.append(
                {"file": path, "start_line": start, "end_line": len(lines), "text": body, "heading": last_heading}
            )
    return chunks


# ---- optional embeddings (sentence-transformers) ----
#
# When KIRA_MEMORY_EMBED=1 we additionally compute dense vectors for each
# chunk and blend cosine similarity with BM25 (default 70% semantic /
# 30% bm25). Falls back gracefully if the package is missing.
_EMBED_MODEL_NAME = os.environ.get("KIRA_EMBED_MODEL", "all-MiniLM-L6-v2")
_EMBED_BLEND = float(os.environ.get("KIRA_EMBED_BLEND", "0.7"))  # weight of semantic score

_embed_model = None
_embed_load_error: str | None = None


def _get_embed_model():
    global _embed_model, _embed_load_error
    if _embed_model is not None or _embed_load_error is not None:
        return _embed_model
    if os.environ.get("KIRA_MEMORY_EMBED") != "1":
        return None
    try:
        from sentence_transformers import SentenceTransformer  # noqa: WPS433

        _embed_model = SentenceTransformer(_EMBED_MODEL_NAME)
        return _embed_model
    except Exception as e:  # ImportError or model download failure
        _embed_load_error = f"{type(e).__name__}: {e}"
        return None


class MemoryIndex:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.chunks: list[dict] = []
        self.df: Counter = Counter()
        self.doc_len: list[int] = []
        self.avgdl: float = 0.0
        self.mtime: float = 0.0
        self.files: list[str] = []
        self.excluded_files: list[str] = []
        self._tokens: list[list[str]] = []
        self._embeddings = None  # numpy array (N, D) or None

    # ---- index build ----

    def _list_files(self) -> tuple[list[Path], list[str]]:
        root = _root()
        if not root.exists():
            return [], []
        excl = _excludes()
        files, excluded = [], []
        for p in sorted(root.rglob("*.md")):
            rel = str(p.relative_to(root))
            if any(fnmatch.fnmatch(rel, g) for g in excl):
                excluded.append(rel)
                continue
            files.append(p)
        return files, excluded

    def _need_rebuild(self) -> bool:
        try:
            files, _ = self._list_files()
        except Exception:
            return False
        if not files:
            return self.chunks != []
        latest = max(f.stat().st_mtime for f in files)
        return latest > self.mtime + 0.001

    def ensure(self) -> None:
        with self._lock:
            if not self._need_rebuild() and self.chunks:
                return
            self._rebuild()

    def rebuild(self) -> None:
        with self._lock:
            self._rebuild()

    def _rebuild(self) -> None:
        files, excluded = self._list_files()
        chunks: list[dict] = []
        tokens: list[list[str]] = []
        df: Counter = Counter()
        max_mtime = 0.0
        rel_files: list[str] = []
        for p in files:
            max_mtime = max(max_mtime, p.stat().st_mtime)
            rel = str(p.relative_to(_root()))
            rel_files.append(rel)
            try:
                text = p.read_text("utf-8", errors="replace")
            except Exception:
                continue
            for ch in _split_chunks(text, rel):
                tks = _tokenize(ch["text"])
                if not tks:
                    continue
                chunks.append(ch)
                tokens.append(tks)
                for w in set(tks):
                    df[w] += 1
        self.chunks = chunks
        self._tokens = tokens
        self.df = df
        self.doc_len = [len(t) for t in tokens]
        self.avgdl = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        self.mtime = max_mtime
        self.files = rel_files
        self.excluded_files = excluded

        # ---- optional embeddings rebuild ----
        self._embeddings = None
        model = _get_embed_model()
        if model is not None and chunks:
            try:
                texts = [c["text"][:1500] for c in chunks]  # cap to keep encoding fast
                emb = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
                self._embeddings = emb
            except Exception as e:  # pragma: no cover
                global _embed_load_error
                _embed_load_error = f"encode failed: {type(e).__name__}: {e}"

    # ---- search ----

    def _bm25_scores(self, query: str) -> list[tuple[float, int]]:
        qt = _tokenize(query)
        if not qt or not self.chunks:
            return []
        N = len(self.chunks)
        k1, b = 1.5, 0.75
        idf: dict[str, float] = {}
        for w in set(qt):
            n = self.df.get(w, 0)
            idf[w] = math.log((N - n + 0.5) / (n + 0.5) + 1.0)
        scores = []
        for i, tks in enumerate(self._tokens):
            if not tks:
                continue
            tf = Counter(tks)
            score = 0.0
            dl = self.doc_len[i]
            for w in qt:
                if w not in tf:
                    continue
                f = tf[w]
                score += idf.get(w, 0.0) * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / (self.avgdl or 1)))
            scores.append((score, i))
        return scores

    def _semantic_scores(self, query: str) -> list[tuple[float, int]] | None:
        if self._embeddings is None:
            return None
        model = _get_embed_model()
        if model is None:
            return None
        try:
            qv = model.encode([query], show_progress_bar=False, normalize_embeddings=True)
            sims = (self._embeddings @ qv.T).flatten()
            return [(float(s), i) for i, s in enumerate(sims)]
        except Exception:  # pragma: no cover
            return None

    def search(self, query: str, k: int = 5) -> list[dict]:
        self.ensure()
        if not self.chunks:
            return []
        bm25 = self._bm25_scores(query)
        sem = self._semantic_scores(query)
        if sem is not None:
            # Min-max normalise BM25 to [0,1] for fair blending with cosine.
            if bm25:
                bmax = max(s for s, _ in bm25) or 1.0
                bm25_norm = {i: s / bmax for s, i in bm25}
            else:
                bm25_norm = {}
            w = _EMBED_BLEND
            blended: dict[int, float] = {}
            for sc, i in sem:
                blended[i] = w * sc
            for i, sc_n in bm25_norm.items():
                blended[i] = blended.get(i, 0.0) + (1 - w) * sc_n
            scores = [(sc, i) for i, sc in blended.items() if sc > 0]
        else:
            scores = [(s, i) for s, i in bm25 if s > 0]
        scores.sort(reverse=True)
        out = []
        for sc, i in scores[:k]:
            ch = self.chunks[i]
            out.append(
                {
                    "file": ch["file"],
                    "heading": ch.get("heading", ""),
                    "start_line": ch["start_line"],
                    "end_line": ch["end_line"],
                    "score": round(sc, 3),
                    "snippet": ch["text"][:800],
                }
            )
        return out

    # ---- status ----

    def status(self) -> dict:
        self.ensure()
        st = {
            "root": str(_root()),
            "chunks": len(self.chunks),
            "files": self.files,
            "excluded": self.excluded_files,
            "avgdl": round(self.avgdl, 1),
            "mtime": self.mtime,
        }
        if self._embeddings is not None:
            st["embeddings"] = {
                "enabled": True,
                "model": _EMBED_MODEL_NAME,
                "dim": int(self._embeddings.shape[1]),
                "blend_weight": _EMBED_BLEND,
            }
        else:
            st["embeddings"] = {
                "enabled": False,
                "reason": _embed_load_error or ("set KIRA_MEMORY_EMBED=1 to enable"),
            }
        return st

    # ---- add ----

    def add(self, text: str, file: str | None = None) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("text is required")
        root = _root()
        root.mkdir(parents=True, exist_ok=True)
        # default: append to MEMORY.md, marked with timestamp
        rel = file or "MEMORY.md"
        if "/" in rel or rel.startswith("."):
            raise ValueError("file must be a plain filename inside notebook")
        target = (root / rel).resolve()
        if not str(target).startswith(str(root)):
            raise ValueError("file must be inside notebook dir")
        ts = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())
        block = f"\n## {ts}\n{text}\n"
        with target.open("a", encoding="utf-8") as f:
            f.write(block)
        self.rebuild()
        return {"file": rel, "lines": len(text.splitlines()) + 2, "bytes": len(block.encode("utf-8"))}


memory = MemoryIndex()
