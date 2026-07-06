"""
Agente: Open Interpreter
========================
Ejecuta código Python y comandos de shell en la PC local.

Diseño:
- No usa el paquete `open-interpreter` directamente porque ese paquete
  introduce su propio loop de LLM. En nuestra arquitectura, Hermes YA
  es el LLM — este agente solo ejecuta lo que Hermes decide.
- Por eso es literalmente un ejecutor subprocess con timeouts y
  captura de stdout/stderr. Toda la "inteligencia" vive en Hermes.

Seguridad:
- Todas las acciones están marcadas `requires_approval: true` en agent.json
- El server.py intercepta y pide confirmación humana por WebSocket
  ANTES de despachar la petición HTTP a este agente.
- Hay un timeout duro para evitar bucles infinitos.

Setup:
1. Levanta este agente:
       cd agents/open_interpreter
       uvicorn main:app --port 8007 --reload
2. (Opcional) Para capacidades extra instala los paquetes que quieras
   tener disponibles: pandas, numpy, openpyxl, requests, etc.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [oi] %(message)s")
logger = logging.getLogger("open_interpreter_agent")

app = FastAPI(title="Open Interpreter Agent", version="1.0")

MAX_TIMEOUT = 300  # 5 min hard cap


class ActionRequest(BaseModel):
    action: str
    parameters: dict[str, Any] = {}


@app.post("/execute")
def execute_action(req: ActionRequest):
    try:
        if req.action == "run_python":
            return run_python(
                req.parameters.get("code", ""),
                int(req.parameters.get("timeout", 30)),
            )
        if req.action == "run_shell":
            return run_shell(
                req.parameters.get("command", ""),
                int(req.parameters.get("timeout", 30)),
            )
        return {"status": "error", "message": f"Acción '{req.action}' no soportada."}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error en open_interpreter")
        return {"status": "error", "message": str(exc)}


@app.get("/health")
def health():
    return {"status": "ok", "agent": "open_interpreter", "python": sys.version.split()[0]}


# --------------------------------------------------------------------
def run_python(code: str, timeout: int) -> dict:
    if not code:
        return {"status": "error", "message": "Falta 'code'."}
    timeout = max(1, min(timeout, MAX_TIMEOUT))

    # Escribir el código a un archivo temporal para mejor traceback
    tmp = Path("/tmp") / "aios_exec.py" if sys.platform != "win32" else Path.cwd() / "aios_exec.py"
    tmp.write_text(code, encoding="utf-8")

    logger.info("Ejecutando Python (timeout=%ss):\n%s", timeout, code[:200])
    try:
        result = subprocess.run(
            [sys.executable, str(tmp)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "status": "success" if result.returncode == 0 else "error",
            "returncode": result.returncode,
            "stdout": result.stdout[-5000:],  # truncar
            "stderr": result.stderr[-5000:],
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "message": f"Timeout tras {timeout}s.",
            "stdout": "",
            "stderr": f"Proceso cancelado por timeout de {timeout}s.",
        }
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def run_shell(command: str, timeout: int) -> dict:
    if not command:
        return {"status": "error", "message": "Falta 'command'."}
    timeout = max(1, min(timeout, MAX_TIMEOUT))

    logger.info("Ejecutando shell (timeout=%ss): %s", timeout, command[:200])
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "status": "success" if result.returncode == 0 else "error",
            "returncode": result.returncode,
            "stdout": result.stdout[-5000:],
            "stderr": result.stderr[-5000:],
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "message": f"Timeout tras {timeout}s.",
            "stdout": "",
            "stderr": f"Proceso cancelado por timeout de {timeout}s.",
        }
