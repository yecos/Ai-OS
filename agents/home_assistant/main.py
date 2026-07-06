"""
Agente: Home Assistant
======================
Wrapper delgado sobre la REST API de Home Assistant.

Setup:
1. Instala Home Assistant (https://www.home-assistant.io/installation/)
2. Entra a tu perfil → Long-Lived Access Tokens → crea uno.
3. Copia el token a .env: HA_TOKEN=eyJ...
4. Verifica la URL (por defecto http://homeassistant.local:8123)
5. Levanta este agente:
       cd agents/home_assistant
       uvicorn main:app --port 8005 --reload

Documentación de la API:
   https://developers.home-assistant.io/docs/api/rest/
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ha] %(message)s")
logger = logging.getLogger("ha_agent")

app = FastAPI(title="Home Assistant Agent", version="1.0")

HA_BASE_URL = os.getenv("HA_BASE_URL", "http://homeassistant.local:8123").rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "")


def _headers() -> dict[str, str]:
    if not HA_TOKEN:
        raise RuntimeError("Falta HA_TOKEN en .env")
    return {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }


class ActionRequest(BaseModel):
    action: str
    parameters: dict[str, Any] = {}


@app.post("/execute")
async def execute_action(req: ActionRequest):
    try:
        if req.action == "turn_on":
            return await _call_service("homeassistant", "turn_on", {"entity_id": req.parameters.get("entity_id", "")})
        if req.action == "turn_off":
            return await _call_service("homeassistant", "turn_off", {"entity_id": req.parameters.get("entity_id", "")})
        if req.action == "toggle":
            return await _call_service("homeassistant", "toggle", {"entity_id": req.parameters.get("entity_id", "")})
        if req.action == "get_state":
            return await get_state(req.parameters.get("entity_id"))
        if req.action == "set_value":
            return await set_value(
                req.parameters.get("entity_id", ""),
                req.parameters.get("value"),
            )
        if req.action == "call_service":
            return await _call_service(
                req.parameters.get("domain", ""),
                req.parameters.get("service", ""),
                req.parameters.get("data", {}),
            )
        if req.action == "list_entities":
            return await list_entities(req.parameters.get("domain"))
        return {"status": "error", "message": f"Acción '{req.action}' no soportada."}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error en HA agent")
        return {"status": "error", "message": str(exc)}


@app.get("/health")
async def health():
    if not HA_TOKEN:
        return {"status": "no_token", "agent": "home_assistant"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{HA_BASE_URL}/api/", headers=_headers())
            return {
                "status": "ok" if resp.status_code == 200 else "error",
                "agent": "home_assistant",
                "ha_responding": resp.status_code == 200,
                "ha_message": resp.text.strip('"') if resp.status_code == 200 else None,
            }
    except httpx.ConnectError:
        return {"status": "no_ha", "agent": "home_assistant", "message": "Home Assistant no responde"}


# --------------------------------------------------------------------
async def _call_service(domain: str, service: str, data: dict) -> dict:
    if not domain or not service:
        return {"status": "error", "message": "Faltan 'domain' y 'service'."}
    url = f"{HA_BASE_URL}/api/services/{domain}/{service}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=_headers(), json=data)
        if resp.status_code != 200:
            return {
                "status": "error",
                "message": f"HA respondió {resp.status_code}",
                "body": resp.text,
            }
        return {
            "status": "success",
            "service": f"{domain}.{service}",
            "data": data,
            "result": _safe_json(resp),
        }


async def get_state(entity_id: str | None) -> dict:
    if entity_id:
        url = f"{HA_BASE_URL}/api/states/{entity_id}"
    else:
        url = f"{HA_BASE_URL}/api/states"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code != 200:
            return {"status": "error", "message": f"HA respondió {resp.status_code}"}
        return {"status": "success", "states": _safe_json(resp)}


async def set_value(entity_id: str, value: Any) -> dict:
    if not entity_id:
        return {"status": "error", "message": "Falta 'entity_id'."}
    # Detectar dominio: climate → set_temperature, media_player → volume_set, etc.
    domain = entity_id.split(".")[0]
    service_map = {
        "climate": "set_temperature",
        "media_player": "volume_set",
        "light": "turn_on",
        "number": "set_value",
        "input_number": "set_value",
    }
    service = service_map.get(domain, "set_value")
    data = {"entity_id": entity_id}
    if domain == "climate":
        data["temperature"] = value
    elif domain == "media_player":
        data["volume_level"] = float(value) / 100 if value > 1 else value
    else:
        data["value"] = value
    return await _call_service(domain, service, data)


async def list_entities(domain: str | None) -> dict:
    return await get_state(None) if not domain else await get_state(None)
    # Nota: HA devuelve todas las entidades; el filtrado por dominio
    # podría hacerse aquí, pero dejamos que el LLM lo haga en su razonamiento.


def _safe_json(resp: httpx.Response):
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return resp.text[:500]
