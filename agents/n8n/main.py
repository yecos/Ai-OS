"""
Agente: n8n
============
Dispatcher delgado hacia webhooks de n8n.

n8n (https://n8n.io) es la alternativa open-source a Zapier. Lo instalas
localmente y creas flujos de trabajo visuales que se disparan por webhook.
Este agente SOLO dispara webhooks — toda la lógica vive en n8n.

Setup:
1. Instala n8n:  docker run -it --rm --name n8n -p 5679:5678 n8nio/n8n
2. Crea un workflow en la UI de n8n y añade un nodo "Webhook".
3. Copia el path del webhook (ej: /webhook/send-email) y úsalo como webhook_id.
4. Levanta este agente:
       cd agents/n8n
       uvicorn main:app --port 8006 --reload
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [n8n] %(message)s")
logger = logging.getLogger("n8n_agent")

app = FastAPI(title="n8n Agent", version="1.0")

N8N_BASE_URL = os.getenv("N8N_BASE_URL", "http://localhost:5679")
N8N_API_KEY = os.getenv("N8N_API_KEY", "")


class ActionRequest(BaseModel):
    action: str
    parameters: dict[str, Any] = {}


@app.post("/execute")
async def execute_action(req: ActionRequest):
    try:
        if req.action == "trigger_workflow":
            return await trigger_workflow(
                req.parameters.get("webhook_id", ""),
                req.parameters.get("data", {}),
            )
        if req.action == "list_workflows":
            return await list_workflows()
        return {"status": "error", "message": f"Acción '{req.action}' no soportada."}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error en n8n agent")
        return {"status": "error", "message": str(exc)}


@app.get("/health")
def health():
    return {"status": "ok", "agent": "n8n", "base_url": N8N_BASE_URL}


# --------------------------------------------------------------------
async def trigger_workflow(webhook_id: str, data: dict) -> dict:
    """POST a /webhook/{webhook_id} del n8n local."""
    if not webhook_id:
        return {"status": "error", "message": "Falta 'webhook_id'."}

    # webhook_id puede venir como "send-email" o como URL completa
    if webhook_id.startswith("http"):
        url = webhook_id
    else:
        # Quitar barra inicial si la trae
        path = webhook_id.lstrip("/")
        # Aceptar tanto "send-email" como "webhook/send-email"
        if not path.startswith("webhook/"):
            path = f"webhook/{path}"
        url = f"{N8N_BASE_URL}/{path}"

    logger.info("Disparando webhook n8n: %s con data=%s", url, data)
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(url, json=data)
            return {
                "status": "success",
                "webhook": url,
                "status_code": resp.status_code,
                "response": _safe_json(resp),
            }
        except httpx.ConnectError:
            return {
                "status": "error",
                "message": f"No se pudo conectar a n8n en {N8N_BASE_URL}. ¿Está corriendo?",
                "url": url,
            }


async def list_workflows() -> dict:
    """Lista workflows via API REST de n8n (requiere API key)."""
    if not N8N_API_KEY:
        return {
            "status": "error",
            "message": "Falta N8N_API_KEY en .env para listar workflows.",
        }
    url = f"{N8N_BASE_URL}/api/v1/workflows"
    headers = {"X-N8N-API-KEY": N8N_API_KEY}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return {
                "status": "error",
                "message": f"n8n API respondió {resp.status_code}",
                "body": resp.text,
            }
        data = resp.json()
        workflows = [
            {"id": w.get("id"), "name": w.get("name"), "active": w.get("active")}
            for w in data.get("data", [])
        ]
        return {"status": "success", "workflows": workflows, "count": len(workflows)}


def _safe_json(resp: httpx.Response):
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return resp.text[:500]
