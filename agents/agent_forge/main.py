"""
Agente: Agent Forge
===================
Permite a Hermes FORJAR nuevos agentes al vuelo.

Cuando Hermes resuelve una tarea escribiendo codigo en open_interpreter,
puede usar agent_forge para guardar esa solucion como un agente reutilizable.
La proxima vez que el usuario pida lo mismo, Hermes ya tendra la herramienta.

Seguridad:
- create_agent y delete_agent requieren aprobacion humana
- El codigo se guarda en agents/<name>/main.py
- El kernel recarga automaticamente los agentes via /agents/reload

Arranque:
    cd agents/agent_forge
    uvicorn main:app --port 8021 --reload
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [forge] %(message)s")
logger = logging.getLogger("agent_forge")

app = FastAPI(title="Agent Forge", version="1.0")

# Resolver ruta absoluta a agents/
AGENTS_DIR = Path(__file__).resolve().parent.parent
KERNEL_URL = os.getenv("KERNEL_URL", "http://localhost:8000")


class ActionRequest(BaseModel):
    action: str
    parameters: dict[str, Any] = {}


@app.post("/execute")
async def execute_action(req: ActionRequest):
    try:
        if req.action == "create_agent":
            return await create_agent(req.parameters)
        if req.action == "list_forged":
            return list_forged()
        if req.action == "delete_agent":
            return delete_agent(req.parameters.get("name", ""))
        return {"status": "error", "message": f"Acción '{req.action}' no soportada."}
    except Exception as exc:
        logger.exception("Error en agent_forge")
        return {"status": "error", "message": str(exc)}


@app.get("/health")
def health():
    return {"status": "ok", "agent": "agent_forge", "agents_dir": str(AGENTS_DIR)}


# --------------------------------------------------------------------
async def create_agent(params: dict) -> dict:
    name = params.get("name", "").strip().lower().replace(" ", "_")
    description = params.get("description", "")
    port = int(params.get("port", 8030))
    actions = params.get("actions", [])
    code = params.get("code", "")

    # Validaciones
    if not name or not description or not code or not actions:
        return {"status": "error", "message": "Faltan campos obligatorios: name, description, actions, code"}
    if not name.replace("_", "").isalnum():
        return {"status": "error", "message": "name debe ser alfanumerico con _"}
    agent_dir = AGENTS_DIR / name
    if agent_dir.exists():
        return {"status": "error", "message": f"El agente '{name}' ya existe en {agent_dir}"}

    # Construir agent.json
    agent_json = {
        "name": name,
        "description": description,
        "endpoint": f"http://localhost:{port}/execute",
        "forged": True,
        "actions": actions,
    }

    # Crear carpeta y escribir archivos
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.json").write_text(
        json.dumps(agent_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (agent_dir / "main.py").write_text(code, encoding="utf-8")

    logger.info("Agente '%s' forjado en %s (puerto %d)", name, agent_dir, port)

    # Intentar recargar el kernel para que lo descubra
    reload_msg = ""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(f"{KERNEL_URL}/agents/reload")
            if r.status_code == 200:
                reload_msg = "Kernel recargado. Hermes ya puede usar este agente."
            else:
                reload_msg = f"Kernel respondio {r.status_code} al reload."
    except Exception as exc:
        reload_msg = f"No se pudo recargar el kernel automaticamente: {exc}. Llama a POST /agents/reload manualmente."

    return {
        "status": "success",
        "agent_name": name,
        "path": str(agent_dir),
        "port": port,
        "actions_count": len(actions),
        "reload_status": reload_msg,
        "next_step": f"Arranca el nuevo agente: cd agents/{name} && uvicorn main:app --port {port} --reload",
    }


def list_forged() -> dict:
    """Lista los agentes con forged=true en su agent.json."""
    forged = []
    for agent_file in AGENTS_DIR.glob("*/agent.json"):
        try:
            data = json.loads(agent_file.read_text(encoding="utf-8"))
            if data.get("forged"):
                forged.append({
                    "name": data["name"],
                    "description": data["description"],
                    "endpoint": data["endpoint"],
                    "actions_count": len(data.get("actions", [])),
                    "path": str(agent_file.parent),
                })
        except (json.JSONDecodeError, KeyError):
            continue
    return {"status": "success", "forged_agents": forged, "count": len(forged)}


def delete_agent(name: str) -> dict:
    if not name:
        return {"status": "error", "message": "Falta 'name'."}
    agent_dir = AGENTS_DIR / name
    if not agent_dir.exists():
        return {"status": "error", "message": f"El agente '{name}' no existe."}
    # Verificar que es forjado (no borrar agentes del core)
    agent_json_path = agent_dir / "agent.json"
    if agent_json_path.exists():
        data = json.loads(agent_json_path.read_text(encoding="utf-8"))
        if not data.get("forged"):
            return {"status": "error", "message": f"'{name}' no es un agente forjado. No se puede borrar."}
    shutil.rmtree(agent_dir)
    return {"status": "success", "deleted": name, "path": str(agent_dir)}
