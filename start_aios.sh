#!/usr/bin/env bash
set -euo pipefail

# Arranca AI OS desde Git Bash / Hermes en Windows.
# Importante: Hermes exporta PYTHONPATH hacia su propio entorno; si no se limpia,
# esta app puede importar paquetes equivocados y fallar con "LiteLLM no está instalado".
unset PYTHONPATH

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [ ! -d .venv ]; then
  python -m venv .venv
fi

. .venv/Scripts/activate
python -m pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
fi

mkdir -p logs

start_service() {
  local name="$1"
  local port="$2"
  local dir="$3"
  echo "[AI OS] Arrancando $name en puerto $port..."
  (cd "$dir" && unset PYTHONPATH && "$ROOT/.venv/Scripts/python" -m uvicorn main:app --port "$port" --reload > "$ROOT/logs/$name.log" 2>&1 &)
}

echo "[AI OS] Arrancando core en http://localhost:8000 ..."
(unset PYTHONPATH && "$ROOT/.venv/Scripts/python" -m uvicorn core.server:app --port 8000 --reload --reload-dir core > "$ROOT/logs/core.log" 2>&1 &)

start_service pc_controller 8001 agents/pc_controller
start_service android_adb 8002 agents/android_adb
start_service memory 8003 agents/memory
start_service browser 8004 agents/browser
start_service home_assistant 8005 agents/home_assistant
start_service n8n 8006 agents/n8n
start_service open_interpreter 8007 agents/open_interpreter

if [ -d dashboard ]; then
  echo "[AI OS] Arrancando dashboard en http://localhost:3000 ..."
  (cd dashboard && npm install >/dev/null 2>&1 && npm run dev > "$ROOT/logs/dashboard.log" 2>&1 &)
fi

echo "[AI OS] Listo. Abre http://localhost:3000 o usa http://localhost:8000/command"
echo "[AI OS] Logs en: $ROOT/logs/"
