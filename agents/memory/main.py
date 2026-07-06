"""
Agente: Memory
==============
Memoria persistente del usuario usando ChromaDB (vector DB local).

Acciones:
- remember: guarda un hecho con categoría
- recall: búsqueda semántica por significado
- forget: elimina por ID (requiere approval)
- list_all: lista todo
- index_document: indexa un archivo de texto para RAG

Setup:
1. pip install chromadb
2. (Opcional) Para embeddings locales con Ollama:
   ollama pull nomic-embed-text
3. Levanta este agente:
       cd agents/memory
       uvicorn main:app --port 8003 --reload

La base de datos vive en ./data/memory_db (configurable vía .env).
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [memory] %(message)s")
logger = logging.getLogger("memory_agent")

app = FastAPI(title="Memory Agent", version="1.0")

DB_PATH = os.getenv("MEMORY_DB_PATH", "./data/memory_db")
EMBEDDING_MODEL = os.getenv("MEMORY_EMBEDDING_MODEL", "ollama/nomic-embed-text")
EMBEDDING_BASE = os.getenv("MEMORY_EMBEDDING_BASE", "http://localhost:11434")

# --------------------------------------------------------------------
# ChromaDB client — instanciado perezosamente para que el server arranque
# aunque chromadb no esté instalado aún.
# --------------------------------------------------------------------
_client = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is not None:
        return _collection
    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError("chromadb no instalado. Ejecuta: pip install chromadb") from exc

    Path(DB_PATH).mkdir(parents=True, exist_ok=True)
    _client = chromadb.PersistentClient(path=DB_PATH)

    # Configurar embeddings: Ollama local si el modelo empieza con "ollama/",
    # si no, ChromaDB usa su embedding default (sentence-transformers).
    if EMBEDDING_MODEL.startswith("ollama/"):
        model_name = EMBEDDING_MODEL.removeprefix("ollama/")
        _collection = _client.get_or_create_collection(
            name="aios_memory",
            metadata={"hnsw:space": "cosine"},
            embedding_function=_OllamaEmbedder(model_name, EMBEDDING_BASE),
        )
    else:
        _collection = _client.get_or_create_collection(
            name="aios_memory",
            metadata={"hnsw:space": "cosine"},
        )
    logger.info("Colección ChromaDB inicializada en %s", DB_PATH)
    return _collection


class _OllamaEmbedder:
    """Embedding function compatible con ChromaDB que usa Ollama."""

    def __init__(self, model: str, base_url: str) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")

    def __call__(self, input: list[str]) -> list[list[float]]:
        import httpx

        embeddings: list[list[float]] = []
        with httpx.Client(timeout=30.0) as client:
            for text in input:
                resp = client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                )
                resp.raise_for_status()
                embeddings.append(resp.json()["embedding"])
        return embeddings

    def name(self) -> str:
        return f"ollama_{self.model}"


# --------------------------------------------------------------------
class ActionRequest(BaseModel):
    action: str
    parameters: dict[str, Any] = {}


@app.post("/execute")
def execute_action(req: ActionRequest):
    try:
        if req.action == "remember":
            return remember(
                req.parameters.get("content", ""),
                req.parameters.get("category", "general"),
            )
        if req.action == "recall":
            return recall(
                req.parameters.get("query", ""),
                req.parameters.get("limit", 5),
            )
        if req.action == "forget":
            return forget(req.parameters.get("id", ""))
        if req.action == "list_all":
            return list_all()
        if req.action == "index_document":
            return index_document(
                req.parameters.get("path", ""),
                req.parameters.get("category", "documents"),
            )
        return {"status": "error", "message": f"Acción '{req.action}' no soportada."}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error en memory agent")
        return {"status": "error", "message": str(exc)}


@app.get("/health")
def health():
    try:
        col = _get_collection()
        return {
            "status": "ok",
            "agent": "memory",
            "db_path": DB_PATH,
            "embedding_model": EMBEDDING_MODEL,
            "count": col.count(),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": str(exc)}


# --------------------------------------------------------------------
def remember(content: str, category: str) -> dict:
    if not content:
        return {"status": "error", "message": "Falta 'content'."}
    col = _get_collection()
    mem_id = str(uuid.uuid4())
    col.add(
        ids=[mem_id],
        documents=[content],
        metadatas=[{"category": category or "general", "source": "user"}],
    )
    logger.info("Recuerdo guardado [%s]: %s", category, content[:80])
    return {"status": "success", "id": mem_id, "content": content, "category": category}


def recall(query: str, limit: int = 5) -> dict:
    if not query:
        return {"status": "error", "message": "Falta 'query'."}
    col = _get_collection()
    n = max(1, min(int(limit or 5), 50))
    results = col.query(query_texts=[query], n_results=n)
    memories = []
    for i, doc_id in enumerate(results.get("ids", [[]])[0]):
        meta = results.get("metadatas", [[]])[0][i] if results.get("metadatas") else {}
        dist = results.get("distances", [[]])[0][i] if results.get("distances") else None
        memories.append(
            {
                "id": doc_id,
                "content": results["documents"][0][i],
                "category": meta.get("category", "general"),
                "distance": dist,
            }
        )
    return {"status": "success", "query": query, "memories": memories, "count": len(memories)}


def forget(mem_id: str) -> dict:
    if not mem_id:
        return {"status": "error", "message": "Falta 'id'."}
    col = _get_collection()
    col.delete(ids=[mem_id])
    return {"status": "success", "deleted": mem_id}


def list_all() -> dict:
    col = _get_collection()
    data = col.get()
    items = [
        {
            "id": _id,
            "content": doc,
            "category": (meta or {}).get("category", "general"),
        }
        for _id, doc, meta in zip(
            data.get("ids", []),
            data.get("documents", []),
            data.get("metadatas", []),
        )
    ]
    return {"status": "success", "memories": items, "count": len(items)}


def index_document(path: str, category: str) -> dict:
    if not path:
        return {"status": "error", "message": "Falta 'path'."}
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        return {"status": "error", "message": f"Archivo no existe: {path}"}

    # Lectura simple: txt, md, py, json, csv. Para PDFs haría falta pypdf.
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return {"status": "error", "message": str(exc)}

    # Particionar en chunks de ~1000 caracteres para embeddings eficientes
    chunk_size = 1000
    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
    if not chunks:
        return {"status": "error", "message": "Archivo vacío."}

    col = _get_collection()
    ids = [str(uuid.uuid4()) for _ in chunks]
    metadatas = [
        {"category": category, "source": str(file_path), "chunk": i}
        for i in range(len(chunks))
    ]
    col.add(ids=ids, documents=chunks, metadatas=metadatas)
    return {
        "status": "success",
        "path": str(file_path),
        "chunks_indexed": len(chunks),
        "category": category,
    }
