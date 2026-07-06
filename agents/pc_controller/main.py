"""
Agente: PC Controller
=====================
Microservicio FastAPI que ejecuta acciones reales en el sistema operativo local.

Este script NO sabe nada de LLMs ni de planes. Solo recibe {"action", "parameters"}
y ejecuta la acción pedida.

Para arrancar:
    cd agents/pc_controller
    uvicorn main:app --port 8001 --reload
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [pc_controller] %(message)s")
logger = logging.getLogger("pc_controller")

app = FastAPI(title="PC Controller Agent", version="1.0")


class ActionRequest(BaseModel):
    action: str
    parameters: dict[str, Any]


# --------------------------------------------------------------------
# Router principal
# --------------------------------------------------------------------
@app.post("/execute")
def execute_action(req: ActionRequest):
    logger.info("Recibido: action=%s params=%s", req.action, req.parameters)
    try:
        if req.action == "organize_folder":
            return organize_folder(req.parameters.get("path", ""))
        if req.action == "open_app":
            return open_app(req.parameters.get("app_name", ""))
        if req.action == "list_files":
            return list_files(req.parameters.get("path", ""))
        if req.action == "create_file":
            return create_file(
                req.parameters.get("path", ""),
                req.parameters.get("content", ""),
            )
        return {"status": "error", "message": f"Acción '{req.action}' no soportada."}
    except Exception as exc:  # noqa: BLE001 — los agentes nunca deben morir
        logger.exception("Error ejecutando acción")
        return {"status": "error", "message": str(exc)}


@app.get("/health")
def health():
    return {"status": "ok", "agent": "pc_controller", "os": platform.system()}


# --------------------------------------------------------------------
# Acciones
# --------------------------------------------------------------------
def organize_folder(path: str) -> dict:
    if not path:
        return {"status": "error", "message": "Falta el parámetro 'path'."}
    folder = Path(path)
    if not folder.exists() or not folder.is_dir():
        return {"status": "error", "message": f"La ruta no existe o no es carpeta: {path}"}

    moved = 0
    for entry in folder.iterdir():
        if entry.is_file():
            ext = entry.suffix.lstrip(".") if entry.suffix else "Otros"
            dest_dir = folder / ext.upper()
            dest_dir.mkdir(exist_ok=True)
            shutil.move(str(entry), str(dest_dir / entry.name))
            moved += 1
    return {"status": "success", "message": f"Se movieron {moved} archivos.", "moved": moved}


def open_app(app_name: str) -> dict:
    if not app_name:
        return {"status": "error", "message": "Falta el parámetro 'app_name'."}

    system = platform.system()
    try:
        if system == "Windows":
            subprocess.Popen(["start", "", app_name], shell=True)
        elif system == "Darwin":  # macOS
            subprocess.Popen(["open", "-a", app_name])
        else:  # Linux y similares
            subprocess.Popen([app_name])
        return {"status": "success", "message": f"Intentando abrir {app_name}."}
    except FileNotFoundError:
        return {"status": "error", "message": f"No se encontró la aplicación: {app_name}"}


def list_files(path: str) -> dict:
    if not path:
        return {"status": "error", "message": "Falta el parámetro 'path'."}
    folder = Path(path)
    if not folder.exists() or not folder.is_dir():
        return {"status": "error", "message": f"La ruta no existe: {path}"}

    items = [
        {"name": entry.name, "type": "dir" if entry.is_dir() else "file", "size": entry.stat().st_size if entry.is_file() else None}
        for entry in folder.iterdir()
    ]
    return {"status": "success", "path": path, "items": items, "count": len(items)}


def create_file(path: str, content: str) -> dict:
    if not path:
        return {"status": "error", "message": "Falta el parámetro 'path'."}
    file_path = Path(path)
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return {"status": "success", "message": f"Archivo creado: {path}", "bytes": len(content)}
    except OSError as exc:
        return {"status": "error", "message": str(exc)}
