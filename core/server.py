"""
server.py
=========
El "Kernel" del AI OS.

Flujo de una petición:
1. POST /command recibe el texto del usuario.
2. Se inyecta el catálogo de agentes en el System Prompt de Hermes.
3. Se llama al LLM (LiteLLM con fallback Megatron → Ollama → cloud).
4. Se parsea el JSON devuelto.
5. Para cada step del plan:
   - Si la acción tiene `requires_approval: true`, se emite
     `step.pending_approval` por WebSocket y se espera hasta recibir
     POST /approve/{step_id} o POST /reject/{step_id} (o timeout).
   - Si se aprueba (o no requiere), se despacha HTTP al agente.
6. Se devuelve el resultado final al cliente.

Endpoints:
- GET  /                  info del kernel
- GET  /agents            catálogo completo
- POST /agents/reload     recarga en caliente
- POST /command           endpoint principal
- POST /approve/{id}      aprueba un step pendiente
- POST /reject/{id}       rechaza un step pendiente
- WS   /ws                panel de control en tiempo real
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.agent_manager import AgentManager
from core.llm_client import HermesClient

# --------------------------------------------------------------------
# Cargar .env en cuanto se importe este módulo
# --------------------------------------------------------------------
load_dotenv()

# --------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("aios.server")

# --------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------
APPROVAL_ENABLED = os.getenv("APPROVAL_ENABLED", "true").lower() == "true"
APPROVAL_TIMEOUT = float(os.getenv("APPROVAL_TIMEOUT", "120"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))

# --------------------------------------------------------------------
# Componentes globales
# --------------------------------------------------------------------
agent_manager = AgentManager()
hermes = HermesClient()


# --------------------------------------------------------------------
# WebSocket manager
# --------------------------------------------------------------------
class WSManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.active.append(ws)
        await ws.send_text("Conectado al Core de AI OS. Esperando eventos...")

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self.active:
                self.active.remove(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        text = json.dumps(message, ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in list(self.active):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


ws_manager = WSManager()


# --------------------------------------------------------------------
# Approval manager — eventos pendientes de confirmación humana
# --------------------------------------------------------------------
class ApprovalManager:
    """Lleva la cuenta de steps que esperan aprobación humana."""

    def __init__(self) -> None:
        self.pending: dict[str, asyncio.Future] = {}

    def create(self, step_id: str) -> asyncio.Future:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self.pending[step_id] = fut
        return fut

    def approve(self, step_id: str) -> bool:
        fut = self.pending.pop(step_id, None)
        if fut and not fut.done():
            fut.set_result("approved")
            return True
        return False

    def reject(self, step_id: str, reason: str = "") -> bool:
        fut = self.pending.pop(step_id, None)
        if fut and not fut.done():
            fut.set_exception(RuntimeError(f"Rechazado por humano: {reason}"))
            return True
        return False


approval_manager = ApprovalManager()


# --------------------------------------------------------------------
# System Prompt
# --------------------------------------------------------------------
BASE_PROMPT = """You are Hermes, the core orchestrator of an AI Operating System.
Your goal is to fulfill the user's requests by delegating tasks to specialized agents.

Rules:
- ALWAYS respond with a single valid JSON object. No prose outside the JSON.
- If no agent/action matches the request, return a plan with an empty list and
  explain in `thought` what was missing.
- Never invent agent names or actions — only use the ones listed below.
- If multiple steps are needed, list them in order inside `plan`.
- Some actions require human approval before execution — they are marked
  `requires_approval: true`. Use them when truly needed, but be aware the user
  will be asked to confirm.

OUTPUT FORMAT (strict JSON):
{
  "thought": "Briefly explain what the user wants and your reasoning",
  "plan": [
    {
      "target_agent": "agent_name",
      "action": "function_name",
      "parameters": {"key": "value"}
    }
  ]
}
"""


# --------------------------------------------------------------------
# Modelos
# --------------------------------------------------------------------
class UserRequest(BaseModel):
    text: str


class ApprovalDecision(BaseModel):
    reason: str = ""


# --------------------------------------------------------------------
# Lifespan
# --------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AI OS Core arrancando...")
    logger.info("Aprobación humana: %s", "ON" if APPROVAL_ENABLED else "OFF")
    logger.info("Agentes descubiertos: %d", len(agent_manager.installed_agents))
    await ws_manager.broadcast(
        {"event": "system.boot", "agents": agent_manager.list_agents()}
    )
    yield
    hermes.close()
    logger.info("AI OS Core detenido.")


app = FastAPI(title="AI OS Core", version="2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------
# Endpoints HTTP básicos
# --------------------------------------------------------------------
@app.get("/")
def root():
    return {
        "name": "AI OS Core",
        "version": "2.0",
        "llm_providers": [p.name for p in hermes.providers],
        "approval_enabled": APPROVAL_ENABLED,
        "agents": agent_manager.list_agents(),
    }


@app.get("/agents")
def list_agents():
    return {"agents": agent_manager.installed_agents}


@app.post("/agents/reload")
def reload_agents():
    before = len(agent_manager.installed_agents)
    agent_manager.reload()
    after = len(agent_manager.installed_agents)
    return {"before": before, "after": after, "agents": agent_manager.list_agents()}


# --------------------------------------------------------------------
# Endpoints de aprobación
# --------------------------------------------------------------------
@app.post("/approve/{step_id}")
def approve_step(step_id: str):
    ok = approval_manager.approve(step_id)
    return {"status": "approved" if ok else "not_found", "step_id": step_id}


@app.post("/reject/{step_id}")
def reject_step(step_id: str, decision: ApprovalDecision = ApprovalDecision()):
    ok = approval_manager.reject(step_id, decision.reason)
    return {"status": "rejected" if ok else "not_found", "step_id": step_id, "reason": decision.reason}


# --------------------------------------------------------------------
# Endpoint principal
# --------------------------------------------------------------------
@app.post("/command")
async def process_command(request: UserRequest):
    """Texto del usuario → plan del LLM → ejecución (con approvals) → resultado."""

    tools_context = agent_manager.get_prompt_context()
    full_system_prompt = BASE_PROMPT + "\n" + tools_context

    await ws_manager.broadcast({"event": "user.command", "text": request.text})

    # 1. Llamar al LLM
    try:
        raw_response = await hermes.achat(
            system_prompt=full_system_prompt,
            user_message=request.text,
            temperature=LLM_TEMPERATURE,
            force_json=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Fallo de LLM: %s", exc)
        await ws_manager.broadcast({"event": "llm.error", "error": str(exc)})
        return {"status": "error", "message": str(exc)}

    await ws_manager.broadcast({"event": "llm.response", "raw": raw_response})

    # 2. Parsear
    plan_data = HermesClient.parse_json_response(raw_response)
    if plan_data is None:
        msg = "Hermes no devolvió un JSON válido."
        await ws_manager.broadcast({"event": "parse.error", "raw": raw_response})
        return {"status": "error", "message": msg, "raw": raw_response}

    thought = plan_data.get("thought", "")
    plan = plan_data.get("plan", [])

    if not plan:
        await ws_manager.broadcast({"event": "plan.empty", "thought": thought})
        return {"status": "no_action", "thought": thought, "results": []}

    # 3. Ejecutar cada step
    results: list[dict] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for idx, step in enumerate(plan, start=1):
            target_name = step.get("target_agent")
            action = step.get("action")
            params = step.get("parameters", {})
            step_id = str(uuid.uuid4())

            await ws_manager.broadcast(
                {
                    "event": "step.start",
                    "step": idx,
                    "step_id": step_id,
                    "agent": target_name,
                    "action": action,
                    "parameters": params,
                }
            )

            # 3.a Buscar agente
            agent_info = agent_manager.find_agent(target_name)
            if not agent_info:
                err = {"error": f"Agente '{target_name}' no encontrado."}
                results.append(err)
                await ws_manager.broadcast(
                    {"event": "step.error", "step": idx, "error": err}
                )
                continue

            # 3.b Verificar si requiere aprobación
            action_def = next(
                (a for a in agent_info["actions"] if a["name"] == action), None
            )
            needs_approval = (
                APPROVAL_ENABLED
                and action_def
                and action_def.get("requires_approval", False)
            )

            if needs_approval:
                await ws_manager.broadcast(
                    {
                        "event": "step.pending_approval",
                        "step": idx,
                        "step_id": step_id,
                        "agent": target_name,
                        "action": action,
                        "parameters": params,
                        "timeout_sec": APPROVAL_TIMEOUT,
                    }
                )
                try:
                    await asyncio.wait_for(
                        approval_manager.create(step_id),
                        timeout=APPROVAL_TIMEOUT if APPROVAL_TIMEOUT > 0 else None,
                    )
                    await ws_manager.broadcast(
                        {"event": "step.approved", "step": idx, "step_id": step_id}
                    )
                except asyncio.TimeoutError:
                    err = {"error": f"Timeout esperando aprobación ({APPROVAL_TIMEOUT}s)."}
                    results.append(err)
                    await ws_manager.broadcast(
                        {"event": "step.timeout", "step": idx, "step_id": step_id}
                    )
                    continue
                except RuntimeError as exc:
                    err = {"error": str(exc)}
                    results.append(err)
                    await ws_manager.broadcast(
                        {"event": "step.rejected", "step": idx, "step_id": step_id, "reason": str(exc)}
                    )
                    continue

            # 3.c Despachar al agente
            endpoint = agent_info["endpoint"]
            logger.info("[Ejecutando] %s → %s", target_name, action)
            try:
                resp = await client.post(
                    endpoint, json={"action": action, "parameters": params}
                )
                resp.raise_for_status()
                result = resp.json()
            except httpx.HTTPError as exc:
                result = {"error": f"Fallo llamando a {target_name}: {exc}"}
                logger.error("[Fallo] %s", result["error"])

            results.append(result)
            await ws_manager.broadcast(
                {"event": "step.done", "step": idx, "step_id": step_id, "result": result}
            )

    return {
        "status": "success",
        "thought": thought,
        "results": results,
    }


# --------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Aceptamos JSON para approvals vía WS también
            try:
                msg = json.loads(data)
                if msg.get("type") == "approve":
                    approval_manager.approve(msg["step_id"])
                elif msg.get("type") == "reject":
                    approval_manager.reject(msg["step_id"], msg.get("reason", ""))
                elif data == "ping":
                    await websocket.send_text("pong")
            except json.JSONDecodeError:
                if data == "ping":
                    await websocket.send_text("pong")
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
