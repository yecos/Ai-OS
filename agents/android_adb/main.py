"""
Agente: Android ADB
===================
Microservicio FastAPI que envuelve comandos ADB hacia dispositivos Android.

Requisitos:
- Tener `adb` instalado y en el PATH (`adb version` debe funcionar).
- Un dispositivo conectado por USB con depuración USB activada, o por red
  (`adb connect IP:5555`).

Para arrancar:
    cd agents/android_adb
    uvicorn main:app --port 8002 --reload
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [android_adb] %(message)s")
logger = logging.getLogger("android_adb")

app = FastAPI(title="Android ADB Agent", version="1.0")


class ActionRequest(BaseModel):
    action: str
    parameters: dict[str, Any] = {}


# --------------------------------------------------------------------
def _adb_available() -> bool:
    return shutil.which("adb") is not None


def _run_adb(args: list[str], device: Optional[str] = None) -> tuple[int, str, str]:
    """Ejecuta `adb [(-s DEVICE)] args...` y devuelve (rc, stdout, stderr)."""
    cmd: list[str] = ["adb"]
    if device:
        cmd += ["-s", device]
    cmd += args
    logger.info("Ejecutando: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


# --------------------------------------------------------------------
@app.post("/execute")
def execute_action(req: ActionRequest):
    if not _adb_available():
        return {
            "status": "error",
            "message": "`adb` no está instalado o no está en el PATH.",
        }
    try:
        if req.action == "list_devices":
            return list_devices()
        if req.action == "screenshot":
            return screenshot(
                req.parameters.get("output_path", ""),
                req.parameters.get("device"),
            )
        if req.action == "tap":
            return tap(
                req.parameters.get("x"),
                req.parameters.get("y"),
                req.parameters.get("device"),
            )
        if req.action == "open_app":
            return open_app(
                req.parameters.get("package", ""),
                req.parameters.get("device"),
            )
        return {"status": "error", "message": f"Acción '{req.action}' no soportada."}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "ADB tardó demasiado (timeout)."}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error en android_adb")
        return {"status": "error", "message": str(exc)}


@app.get("/health")
def health():
    return {
        "status": "ok" if _adb_available() else "no_adb",
        "agent": "android_adb",
        "adb_available": _adb_available(),
    }


# --------------------------------------------------------------------
# Acciones
# --------------------------------------------------------------------
def list_devices() -> dict:
    rc, out, _ = _run_adb(["devices"])
    lines = [l for l in out.splitlines()[1:] if l.strip()]
    devices = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 2:
            devices.append({"serial": parts[0], "state": parts[1]})
    return {"status": "success", "devices": devices, "count": len(devices)}


def screenshot(output_path: str, device: Optional[str] = None) -> dict:
    if not output_path:
        return {"status": "error", "message": "Falta 'output_path'."}
    # Capturamos en el device y hacemos pull
    remote = "/sdcard/aios_screenshot.png"
    rc, _, err = _run_adb(["shell", "screencap", "-p", remote], device)
    if rc != 0:
        return {"status": "error", "message": f"screencap falló: {err}"}
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    rc, _, err = _run_adb(["pull", remote, output_path], device)
    _run_adb(["shell", "rm", remote], device)  # limpieza
    if rc != 0:
        return {"status": "error", "message": f"pull falló: {err}"}
    return {"status": "success", "message": f"Screenshot en {output_path}", "path": output_path}


def tap(x: Any, y: Any, device: Optional[str] = None) -> dict:
    if x is None or y is None:
        return {"status": "error", "message": "Faltan coordenadas x/y."}
    try:
        xi, yi = int(x), int(y)
    except (TypeError, ValueError):
        return {"status": "error", "message": "x e y deben ser enteros."}
    rc, _, err = _run_adb(["shell", "input", "tap", str(xi), str(yi)], device)
    if rc != 0:
        return {"status": "error", "message": f"tap falló: {err}"}
    return {"status": "success", "message": f"Tap en ({xi},{yi})."}


def open_app(package: str, device: Optional[str] = None) -> dict:
    if not package:
        return {"status": "error", "message": "Falta 'package'."}
    rc, _, err = _run_adb(
        ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
        device,
    )
    if rc != 0:
        return {"status": "error", "message": f"No se pudo abrir {package}: {err}"}
    return {"status": "success", "message": f"Abriendo {package}."}
