# 🧠 AI OS — Sistema Operativo con IA

Núcleo de un SO con IA: **Hermes** (LLM local vía Ollama) orquesta **agentes** (plugins HTTP) que se descubren automáticamente. Stack 100% libre y gratuito.

```
ai-os/
├── core/                          # Kernel FastAPI (puerto 8000)
│   ├── server.py                  # Orquestador principal + WebSocket
│   ├── agent_manager.py           # Descubre agents/*/agent.json
│   └── llm_client.py              # LiteLLM con fallback (Ollama → Megatron → cloud)
├── agents/                        # Microservicios (cada uno es un plugin)
│   ├── pc_controller/             # Archivos, apps, mouse (8001)
│   ├── android_adb/               # ADB + Android (8002)
│   ├── memory/                    # ChromaDB, RAG (8003)
│   ├── browser/                   # Playwright (8004)
│   ├── home_assistant/            # REST API HA (8005)
│   ├── n8n/                       # Webhooks n8n (8006)
│   └── open_interpreter/          # Código/shell arbitrario (8007)
├── dashboard/                     # Frontend futurista Next.js (puerto 3000)
├── .env.example                   # Plantilla de configuración
├── requirements.txt               # Dependencias Python
└── README.md                      # Este archivo
```

---

## 🚀 Puesta en marcha en tu PC (paso a paso)

Esta guía te lleva desde cero hasta tener Hermes corriendo y controlando tu PC.

### 0. Requisitos previos

- **Python 3.10+** → https://www.python.org/downloads/
- **Ollama** → https://ollama.com (instala el binario para tu SO)
- **Git** → https://git-scm.com
- **Node.js 20+** (solo si quieres el dashboard) → https://nodejs.org

### 1. Clonar el repo

```bash
git clone https://github.com/yecos/Ai-OS.git
cd Ai-OS
```

### 2. Descargar el modelo Hermes en Ollama

Ollama es el runtime que ejecuta LLMs localmente. Hermes es el modelo que actúa como cerebro de tu AI OS.

```bash
# Instala Ollama desde https://ollama.com si no lo tienes

# Arranca el servidor Ollama (en otra terminal, déjalo corriendo):
ollama serve

# Descarga el modelo Hermes (unos 4.5 GB, tarda varios minutos la 1ª vez):
ollama pull hermes

# Verifica que está disponible:
ollama list
# Debes ver:  hermes    latest    4.5 GB

# (Opcional) Descarga el modelo de embeddings para ChromaDB (memoria):
ollama pull nomic-embed-text
```

> **Modelos alternativos:** si `hermes` no te convence, prueba `ollama pull llama3.1:8b` o `ollama pull qwen2.5:7b`. Luego cambia `LLM_OLLAMA_MODEL` en el `.env`.

### 3. Configurar el entorno Python

```bash
# Windows: py -m venv .venv && .venv\Scripts\activate
python -m venv .venv
source .venv/bin/activate          # macOS/Linux
pip install -r requirements.txt
```

### 4. Configurar variables de entorno

```bash
cp .env.example .env
# Edita .env si quieres cambiar algo. Por defecto ya funciona con Ollama + Hermes.
```

El `.env` por defecto ya viene listo para Ollama con Hermes. Solo necesitas editar:
- `HA_TOKEN` si tienes Home Assistant
- `N8N_API_KEY` si tienes n8n
- Los proveedores cloud (`LLM_OPENAI_*`, `LLM_ANTHROPIC_*`) si quieres fallback en la nube

### 5. (Opcional) Instalar Chromium para el agente Browser

```bash
playwright install chromium
```

### 6. Levantar el stack

Abre **una terminal por componente** + **una para el kernel**. En cada una, recuerda activar el venv primero (`source .venv/bin/activate`).

```bash
# Terminal 1 — KERNEL (obligatorio)
uvicorn core.server:app --port 8000 --reload

# Terminal 2 — PC Controller (recomendado)
cd agents/pc_controller && uvicorn main:app --port 8001 --reload

# Terminal 3 — Memory (recomendado, para que Hermes recuerde)
cd agents/memory && uvicorn main:app --port 8003 --reload

# Terminal 4 — Browser (opcional)
cd agents/browser && uvicorn main:app --port 8004 --reload

# Terminal 5 — Android ADB (opcional, si tienes un móvil conectado)
cd agents/android_adb && uvicorn main:app --port 8002 --reload

# Terminal 6 — Home Assistant (opcional, si tienes HA)
cd agents/home_assistant && uvicorn main:app --port 8005 --reload

# Terminal 7 — n8n (opcional, si tienes n8n)
cd agents/n8n && uvicorn main:app --port 8006 --reload

# Terminal 8 — Open Interpreter (opcional, ejecución de código)
cd agents/open_interpreter && uvicorn main:app --port 8007 --reload
```

**Solo necesitas levantar el kernel + los agentes que vayas a usar.** El kernel descubre automáticamente cuáles están activos.

### 7. (Opcional) Levantar el dashboard futurista

```bash
cd dashboard
npm install        # o: bun install
npm run dev        # o: bun run dev
```

Abre http://localhost:3000 en tu navegador. Verás un HUD cyberpunk que se conecta automáticamente al kernel en `ws://localhost:8000/ws`. Si el kernel no está activo, el dashboard entra en **modo DEMO** para que veas la interfaz.

### 8. Verificar que todo responde

```bash
curl http://localhost:8000/                # info del kernel
curl http://localhost:8000/agents          # catálogo de agentes
curl http://localhost:8001/health          # cada agente tiene /health
```

Si ves los 7 agentes en `/agents`, ¡está listo!

### 9. Hablar con Hermes

**Por cURL:**
```bash
curl -X POST http://localhost:8000/command \
  -H "Content-Type: application/json" \
  -d '{"text": "Recuerda que mi color favorito es el azul marino"}'

curl -X POST http://localhost:8000/command \
  -H "Content-Type: application/json" \
  -d '{"text": "¿Qué color me gusta?"}'

curl -X POST http://localhost:8000/command \
  -H "Content-Type: application/json" \
  -d '{"text": "Organiza mi carpeta C:/Users/MiUsuario/Desktop"}'

curl -X POST http://localhost:8000/command \
  -H "Content-Type: application/json" \
  -d '{"text": "Entra a wikipedia.org y dime qué dice en la portada"}'
```

**Por el dashboard:** abre http://localhost:3000 y escribe en la terminal.

---

## 🛡️ Sistema de aprobación humana

Cualquier acción marcada `"requires_approval": true` en su `agent.json` **pausa la ejecución** y emite un evento `step.pending_approval` por WebSocket. No se ejecuta hasta que llegue un `POST /approve/{step_id}`.

### Aprobar desde cURL

```bash
# El step_id te llega en el body del POST /command o por el WebSocket
curl -X POST http://localhost:8000/approve/<step_id>
curl -X POST http://localhost:8000/reject/<step_id> \
  -H "Content-Type: application/json" \
  -d '{"reason":"no quiero"}'
```

### Aprobar desde el dashboard

Los steps pendientes aparecen como cards ámbar en el panel derecho con botones ✓ APPROVE / ✗ REJECT.

### Acciones que requieren aprobación por defecto

- `open_interpreter.run_python` y `run_shell` → siempre
- `home_assistant.turn_on/off/toggle/set_value/call_service` → siempre
- `memory.forget` → siempre

Para desactivar todo el sistema: `APPROVAL_ENABLED=false` en `.env`.

---

## 🧠 Cadena LLM con fallback

```
Ollama (Hermes)  ──fail──▶  Megatron 5.5  ──fail──▶  OpenAI  ──fail──▶  Anthropic
   (primario)                  (opcional)              (opcional)         (opcional)
```

- Cada proveedor se configura con variables `LLM_<PROVIDER>_*` en `.env`.
- Si uno falla (timeout, 5xx, conexión), LiteLLM cae al siguiente automáticamente.
- Los logs muestran `LLM → probando ollama...` / `LLM ✓ respondió ollama`.

---

## ➕ Añadir un nuevo agente (3 pasos)

1. Crea `agents/<mi_agente>/agent.json` con su manifiesto.
2. Crea `agents/<mi_agente>/main.py` con un FastAPI en su propio puerto.
3. Llama a `POST /agents/reload` — Hermes lo descubre solo.

Hermes empezará a usarlo **sin tocar el kernel**. Ese es el punto.

---

## 🌐 WebSocket — Eventos en tiempo real

Conéctate a `ws://localhost:8000/ws` para recibir eventos:

| Evento                  | Cuándo se emite                                       |
|-------------------------|--------------------------------------------------------|
| `system.boot`           | Arranque del kernel                                    |
| `user.command`          | Llega una petición a `/command`                        |
| `llm.response`          | El LLM respondió (con el JSON crudo)                   |
| `llm.error`             | El LLM falló                                           |
| `parse.error`           | El LLM no devolvió JSON válido                         |
| `step.start`            | Empieza un step del plan                               |
| `step.pending_approval` | El step requiere aprobación humana                     |
| `step.approved`         | Aprobado                                               |
| `step.rejected`         | Rechazado                                              |
| `step.timeout`          | Timeout esperando aprobación                           |
| `step.done`             | El step terminó (con resultado)                        |
| `step.error`            | El step falló                                          |

---

## 🔧 Troubleshooting

| Síntoma                                          | Causa probable                                          |
|--------------------------------------------------|---------------------------------------------------------|
| `Sin proveedores LLM configurados`               | No creaste `.env` o no tiene `LLM_*_MODEL`              |
| `No se pudo hablar a Ollama`                     | `ollama serve` no está corriendo                        |
| `Hermes no devolvió un JSON válido`              | Cambia de modelo (algunos son peores con JSON)          |
| `Agente no encontrado`                           | No levantaste ese microservicio o el puerto está mal    |
| `chromadb no instalado`                          | `pip install chromadb`                                  |
| `playwright no instalado`                        | `pip install playwright && playwright install chromium` |
| Dashboard en modo DEMO                           | El kernel (puerto 8000) no está corriendo               |

Logs detallados:
```bash
LOGLEVEL=DEBUG uvicorn core.server:app --port 8000 --reload
```

---

## 🎯 Próximos pasos sugeridos

1. **Probar Hermes:** ejecuta `curl -X POST http://localhost:8000/command -d '{"text":"hola"}'`
2. **Personalizar agentes:** edita los `agent.json` para añadir tus propias acciones
3. **Más integraciones n8n:** Gmail, WhatsApp, Calendar son solo crear workflows en la UI de n8n
4. **Audit log:** persistir cada step ejecutado a SQLite para tener historial
5. **Permisos por usuario:** whitelist de acciones peligrosas

---

## 📄 Licencia

MIT. Haz lo que quieras con este código.

## 🙋 Créditos

- **Hermes** → modelo LLM de Nous Research
- **Ollama** → runtime local de LLMs
- **LiteLLM** → unificación de APIs de LLM
- **FastAPI** → framework del kernel
- **ChromaDB** → base de datos vectorial
- **Playwright** → automatización de navegador
- **Home Assistant** → domótica open-source
- **n8n** → integraciones open-source
