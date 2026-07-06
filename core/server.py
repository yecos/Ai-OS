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
import re
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
        await ws.send_text(
            json.dumps(
                {"event": "system.boot", "agents": agent_manager.list_agents()},
                ensure_ascii=False,
            )
        )

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
# System Prompt — modo ONE-SHOT (legacy, sigue funcionando)
# --------------------------------------------------------------------
BASE_PROMPT = """You are Hermes, the core orchestrator of an AI Operating System.
Your goal is to fulfill the user's requests by delegating tasks to specialized agents.

Rules:
- ALWAYS respond with a single valid JSON object. No prose outside the JSON.
- If no agent/action matches the request, return a plan with an empty list and
  explain in `thought` what was missing.
- Never invent agent names or actions — only use the ones listed below.
- If multiple steps are needed, list them in order inside `plan`.
- Every item inside `plan` MUST be an object with exactly these keys:
  `target_agent`, `action`, and `parameters`. Never return strings in `plan`.
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
# System Prompt — modo AUTÓNOMO (ReAct loop)
# --------------------------------------------------------------------
AUTONOMOUS_PROMPT = """You are Hermes, an AUTONOMOUS AI Operating System.
Your mission: ACHIEVE the user's request by ANY means necessary. You NEVER give up.

You operate in ITERATIONS. Each turn you receive:
- The user's original request
- The tools available (listed below)
- The history of steps already taken and their results
- Your remaining iterations budget

Each turn you MUST respond with ONE of these JSON shapes:

1. If the task is DONE:
{
  "done": true,
  "summary": "Que lograste y como",
  "next_steps_optional": "Sugerencia opcional para el usuario"
}

2. If you need to take ONE more step:
{
  "done": false,
  "reasoning": "Por que este es el siguiente paso logico",
  "next_step": {
    "target_agent": "agent_name",
    "action": "action_name",
    "parameters": {"key": "value"}
  }
}

CRITICAL AUTONOMY RULES:
- You are NOT limited to the pre-installed tools. If no direct tool exists:
  1. Use web_search.search to find how to do it on the internet
  2. Use web_search.fetch_page to read a relevant page
  3. Use open_interpreter.run_python to write code that does it
  4. If the code works, use agent_forge.create_agent to save it as a reusable tool
- If a step FAILS, read the error, understand why, and try a DIFFERENT approach.
  Never repeat the exact same step expecting a different result.
- Be efficient: prefer 1-2 direct steps over 10 indirect ones when possible.
- Be safe: actions marked requires_approval will pause for human confirmation.
- You have a LIMITED number of iterations. Use them wisely.
- When you genuinely cannot achieve the task after trying, return done=true
  with summary explaining what you tried and what blocked you.

OUTPUT: A single valid JSON object. No markdown, no prose outside JSON.
"""


def fallback_plan_for(text: str) -> Optional[dict[str, Any]]:
    """Deterministic router for common commands when a local LLM drifts."""

    raw = text.strip()
    lower = raw.lower()

    def step(agent: str, action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        return {"target_agent": agent, "action": action, "parameters": parameters}

    if any(word in lower for word in ("recuerda", "recordar", "memoriza", "remember")):
        content = re.sub(r"^(recuerda|recordar|memoriza|remember)\s+(que\s+)?", "", raw, flags=re.I).strip()
        return {
            "thought": "Guardaré la información indicada usando el agente de memoria.",
            "plan": [step("memory", "remember", {"content": content or raw, "category": "facts"})],
        }

    if any(word in lower for word in ("lista", "listar", "muestra", "archivos", "carpeta", "folder")):
        match = re.search(r"([A-Za-z]:[/\\][^\n\r]+)$", raw)
        path = match.group(1).strip().strip('\"') if match else os.path.expanduser("~")
        return {
            "thought": f"Listaré los archivos de {path} usando el controlador de PC.",
            "plan": [step("pc_controller", "list_files", {"path": path})],
        }

    if any(word in lower for word in ("organiza", "ordena", "organize")):
        match = re.search(r"([A-Za-z]:[/\\][^\n\r]+)$", raw)
        path = match.group(1).strip().strip('\"') if match else os.path.join(os.path.expanduser("~"), "Desktop")
        return {
            "thought": f"Organizaré la carpeta {path} con el controlador de PC.",
            "plan": [step("pc_controller", "organize_folder", {"path": path})],
        }

    if any(word in lower for word in ("navega", "entra", "abre", "browser", "web", "página", "pagina")):
        match = re.search(r"https?://\S+|(?:[\w-]+\.)+[a-z]{2,}(?:/\S*)?", raw, flags=re.I)
        url = match.group(0) if match else "https://example.com"
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return {
            "thought": f"Abriré {url} en el navegador y extraeré el texto visible.",
            "plan": [
                step("browser", "navigate", {"url": url}),
                step("browser", "get_text", {}),
            ],
        }

    if "adb" in lower or "android" in lower or "dispositivo" in lower:
        return {
            "thought": "Consultaré los dispositivos Android conectados por ADB.",
            "plan": [step("android_adb", "list_devices", {})],
        }

    if "home assistant" in lower or "entidad" in lower or "estado" in lower:
        return {
            "thought": "Consultaré entidades/estado en Home Assistant.",
            "plan": [step("home_assistant", "list_entities", {})],
        }

    return None


def normalize_plan(plan_data: dict[str, Any], user_text: str) -> dict[str, Any]:
    """Validate LLM plan shape and fall back for common malformed outputs."""

    plan = plan_data.get("plan", [])
    valid = isinstance(plan, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("target_agent"), str)
        and isinstance(item.get("action"), str)
        and isinstance(item.get("parameters", {}), dict)
        for item in plan
    )
    if valid:
        return plan_data

    fallback = fallback_plan_for(user_text)
    if fallback:
        fallback["thought"] += " (Plan determinístico usado porque el LLM devolvió un plan inválido.)"
        return fallback

    return {
        "thought": "El LLM devolvió un plan inválido y no hay regla determinística para esta solicitud.",
        "plan": [],
    }


# --------------------------------------------------------------------
# Modelos
# --------------------------------------------------------------------
class UserRequest(BaseModel):
    text: str


class AutonomousRequest(BaseModel):
    text: str
    max_iterations: int = 10


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
        fallback = fallback_plan_for(request.text)
        if fallback is None:
            msg = "Hermes no devolvió un JSON válido."
            await ws_manager.broadcast({"event": "parse.error", "raw": raw_response})
            return {"status": "error", "message": msg, "raw": raw_response}
        plan_data = fallback
        await ws_manager.broadcast({"event": "plan.fallback", "reason": "invalid_json", "plan": plan_data})
    else:
        plan_data = normalize_plan(plan_data, request.text)

    thought = plan_data.get("thought", "")
    plan = plan_data.get("plan", [])

    if not plan:
        await ws_manager.broadcast({"event": "plan.empty", "thought": thought})
        return {"status": "no_action", "thought": thought, "results": []}

    await ws_manager.broadcast({"event": "plan.ready", "thought": thought, "plan": plan})

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

    response = {
        "status": "success",
        "thought": thought,
        "results": results,
    }
    await ws_manager.broadcast({"event": "command.done", **response})
    return response


# --------------------------------------------------------------------
# Endpoint AUTÓNOMO — ReAct loop
# --------------------------------------------------------------------
@app.post("/command_react")
async def process_command_autonomous(request: AutonomousRequest):
    """Loop ReAct: Hermes decide un paso, lo ejecuta, observa, repite."""

    tools_context = agent_manager.get_prompt_context()
    full_system_prompt = AUTONOMOUS_PROMPT + "\n" + tools_context

    await ws_manager.broadcast({"event": "user.command", "text": request.text, "mode": "autonomous"})

    history: list[dict] = []  # cada item: {step, agent, action, parameters, result, status}
    max_iter = max(1, min(request.max_iterations, 30))
    consecutive_failures = 0
    final_summary = None

    async with httpx.AsyncClient(timeout=120.0) as client:
        for iteration in range(1, max_iter + 1):
            remaining = max_iter - iteration + 1
            logger.info("[ReAct] Iteración %d/%d (quedan %d)", iteration, max_iter, remaining)

            # 1. Construir el mensaje del usuario con el historial
            user_msg = _build_react_user_message(request.text, history, remaining)

            await ws_manager.broadcast({
                "event": "react.thinking",
                "iteration": iteration,
                "remaining": remaining,
            })

            # 2. Llamar al LLM
            try:
                raw = await hermes.achat(
                    system_prompt=full_system_prompt,
                    user_message=user_msg,
                    temperature=0.4,
                    force_json=True,
                )
            except Exception as exc:
                logger.error("[ReAct] LLM falló: %s", exc)
                await ws_manager.broadcast({"event": "llm.error", "error": str(exc), "iteration": iteration})
                return {"status": "error", "message": str(exc), "history": history, "iterations": iteration - 1}

            await ws_manager.broadcast({"event": "llm.response", "raw": raw, "iteration": iteration})

            # 3. Parsear respuesta
            decision = HermesClient.parse_json_response(raw)
            if decision is None:
                logger.error("[ReAct] LLM no devolvió JSON válido: %s", raw[:200])
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    return {
                        "status": "error",
                        "message": "LLM devolvió JSON inválido 3 veces seguidas. Abortando.",
                        "history": history,
                        "iterations": iteration,
                    }
                continue

            # 4. ¿Terminó?
            if decision.get("done"):
                final_summary = decision.get("summary", "Tarea completada.")
                await ws_manager.broadcast({
                    "event": "react.done",
                    "iteration": iteration,
                    "summary": final_summary,
                    "next_steps": decision.get("next_steps_optional", ""),
                })
                logger.info("[ReAct] DONE en iteración %d: %s", iteration, final_summary[:100])
                break

            # 5. Ejecutar el next_step
            next_step = decision.get("next_step")
            if not next_step:
                consecutive_failures += 1
                continue

            target_name = next_step.get("target_agent", "")
            action = next_step.get("action", "")
            params = next_step.get("parameters", {})
            step_id = str(uuid.uuid4())
            reasoning = decision.get("reasoning", "")

            await ws_manager.broadcast({
                "event": "step.start",
                "step": iteration,
                "step_id": step_id,
                "agent": target_name,
                "action": action,
                "parameters": params,
                "reasoning": reasoning,
                "mode": "react",
            })

            # 5.a Verificar que el agente existe
            agent_info = agent_manager.find_agent(target_name)
            if not agent_info:
                err = {"error": f"Agente '{target_name}' no encontrado."}
                history.append({
                    "iteration": iteration,
                    "agent": target_name,
                    "action": action,
                    "parameters": params,
                    "reasoning": reasoning,
                    "result": err,
                    "status": "error",
                })
                await ws_manager.broadcast({"event": "step.error", "step": iteration, "step_id": step_id, "error": err})
                consecutive_failures += 1
                continue

            # 5.b ¿Requiere aprobación?
            action_def = next((a for a in agent_info["actions"] if a["name"] == action), None)
            needs_approval = (
                APPROVAL_ENABLED
                and action_def
                and action_def.get("requires_approval", False)
            )

            if needs_approval:
                await ws_manager.broadcast({
                    "event": "step.pending_approval",
                    "step": iteration,
                    "step_id": step_id,
                    "agent": target_name,
                    "action": action,
                    "parameters": params,
                    "timeout_sec": APPROVAL_TIMEOUT,
                })
                try:
                    await asyncio.wait_for(
                        approval_manager.create(step_id),
                        timeout=APPROVAL_TIMEOUT if APPROVAL_TIMEOUT > 0 else None,
                    )
                    await ws_manager.broadcast({"event": "step.approved", "step": iteration, "step_id": step_id})
                except asyncio.TimeoutError:
                    err = {"error": f"Timeout esperando aprobación ({APPROVAL_TIMEOUT}s)."}
                    history.append({
                        "iteration": iteration, "agent": target_name, "action": action,
                        "parameters": params, "reasoning": reasoning, "result": err, "status": "timeout",
                    })
                    await ws_manager.broadcast({"event": "step.timeout", "step": iteration, "step_id": step_id})
                    consecutive_failures += 1
                    continue
                except RuntimeError as exc:
                    err = {"error": str(exc)}
                    history.append({
                        "iteration": iteration, "agent": target_name, "action": action,
                        "parameters": params, "reasoning": reasoning, "result": err, "status": "rejected",
                    })
                    await ws_manager.broadcast({"event": "step.rejected", "step": iteration, "step_id": step_id, "reason": str(exc)})
                    consecutive_failures += 1
                    continue

            # 5.c Ejecutar
            endpoint = agent_info["endpoint"]
            logger.info("[ReAct] Ejecutando %s → %s", target_name, action)
            try:
                resp = await client.post(endpoint, json={"action": action, "parameters": params})
                resp.raise_for_status()
                result = resp.json()
                status = "success" if result.get("status") != "error" else "error"
                if status == "error":
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0
            except httpx.HTTPError as exc:
                result = {"error": f"Fallo llamando a {target_name}: {exc}"}
                status = "error"
                consecutive_failures += 1

            history.append({
                "iteration": iteration,
                "agent": target_name,
                "action": action,
                "parameters": params,
                "reasoning": reasoning,
                "result": result,
                "status": status,
            })

            await ws_manager.broadcast({
                "event": "step.done",
                "step": iteration,
                "step_id": step_id,
                "result": result,
                "status": status,
            })

            # Circuit breaker: 3 fallos consecutivos
            if consecutive_failures >= 3:
                logger.warning("[ReAct] 3 fallos consecutivos. Abortando.")
                final_summary = "Abortado tras 3 fallos consecutivos. Último error: " + str(result)[:200]
                await ws_manager.broadcast({
                    "event": "react.aborted",
                    "reason": "3 fallos consecutivos",
                    "iteration": iteration,
                })
                break

    if final_summary is None:
        final_summary = f"Alcanzadas {max_iter} iteraciones sin completar la tarea. Revisa el historial."

    return {
        "status": "success" if consecutive_failures < 3 else "partial",
        "summary": final_summary,
        "history": history,
        "iterations_used": len(history),
        "max_iterations": max_iter,
    }


def _build_react_user_message(original_request: str, history: list[dict], remaining: int) -> str:
    """Construye el mensaje que se le envía al LLM en cada iteración."""
    msg = f"USER REQUEST:\n{original_request}\n\n"
    msg += f"ITERATIONS REMAINING: {remaining}\n\n"
    if not history:
        msg += "HISTORY: (none yet — this is your first step)\n\n"
        msg += "Decide your FIRST step. If a direct tool exists, use it. If not, use web_search to learn how."
    else:
        msg += "HISTORY OF STEPS TAKEN:\n"
        for h in history:
            msg += f"\n--- Iteration {h['iteration']} ---\n"
            msg += f"Agent: {h['agent']} | Action: {h['action']}\n"
            msg += f"Parameters: {json.dumps(h['parameters'], ensure_ascii=False)}\n"
            msg += f"Reasoning: {h.get('reasoning', '(none)')}\n"
            msg += f"Status: {h['status']}\n"
            result_str = json.dumps(h['result'], ensure_ascii=False)
            if len(result_str) > 1500:
                result_str = result_str[:1500] + "...[truncated]"
            msg += f"Result: {result_str}\n"
        msg += "\nBased on the history above, decide your NEXT step. "
        msg += "If the task is now complete, return done=true. If a step failed, try a DIFFERENT approach."
    msg += "\n\nRespond with ONE JSON object: {done: true, summary} OR {done: false, reasoning, next_step}."
    return msg


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
