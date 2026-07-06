"""
agent_manager.py
================
Carga dinámicamente todos los `agent.json` que viva en `agents/<name>/agent.json`.

Cada vez que se instancia (o se llama a `reload()`), escanea la carpeta
`agents/` del proyecto, parsea los manifiestos y construye:

- `installed_agents`: lista de dicts con la metadata completa.
- `get_prompt_context()`: texto que se inyecta en el System Prompt de Hermes
  para que el LLM "sepa" qué herramientas tiene disponibles.

Diseño intencional: si un `agent.json` está roto, lo saltamos y seguimos
cargando el resto — un plugin malo NO debe tirar el kernel.
"""

from __future__ import annotations

import glob
import json
import logging
import os
from typing import Optional

logger = logging.getLogger("aios.agent_manager")


class AgentManager:
    def __init__(self, agents_dir: Optional[str] = None) -> None:
        # Resolvemos la ruta absoluta para que funcione sin importar el cwd.
        if agents_dir is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            agents_dir = os.path.join(project_root, "agents")
        self.agents_dir = agents_dir
        self.installed_agents: list[dict] = []
        self.load_agents()

    # ------------------------------------------------------------------
    def load_agents(self) -> None:
        """Escanea `agents/*/agent.json` y carga cada manifiesto."""
        self.installed_agents = []
        pattern = os.path.join(self.agents_dir, "*", "agent.json")
        agent_files = glob.glob(pattern)

        for file_path in agent_files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    agent_data = json.load(f)

                # Validación mínima del esquema
                required = {"name", "description", "endpoint", "actions"}
                if not required.issubset(agent_data.keys()):
                    missing = required - set(agent_data.keys())
                    logger.warning(
                        "[AgentManager] %s omitido: faltan campos %s",
                        file_path,
                        missing,
                    )
                    continue

                self.installed_agents.append(agent_data)
                logger.info(
                    "[AgentManager] Agente cargado: %s (%d acciones)",
                    agent_data["name"],
                    len(agent_data["actions"]),
                )
            except json.JSONDecodeError as exc:
                logger.error("[AgentManager] JSON inválido en %s: %s", file_path, exc)
            except OSError as exc:
                logger.error("[AgentManager] No se pudo leer %s: %s", file_path, exc)

        print(f"[Sistema] {len(self.installed_agents)} agentes cargados.")

    def reload(self) -> None:
        """Recarga en caliente — útil mientras desarrollas nuevos agentes."""
        self.load_agents()

    # ------------------------------------------------------------------
    def get_prompt_context(self) -> str:
        """Genera el bloque de texto que se le inyecta al System Prompt."""
        if not self.installed_agents:
            return (
                "No hay agentes instalados. Responde al usuario explicándole "
                "que todavía no tiene plugins disponibles."
            )

        lines = ["You have access to the following tools/agents:", ""]
        for agent in self.installed_agents:
            lines.append(f"Agent: {agent['name']}")
            lines.append(f"Description: {agent['description']}")
            lines.append("Actions:")
            for action in agent["actions"]:
                params = json.dumps(action.get("parameters", {}))
                lines.append(
                    f"- {action['name']}: {action['description']}. Params: {params}"
                )
            lines.append("")  # separador entre agentes
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def find_agent(self, name: str) -> Optional[dict]:
        """Busca un agente por nombre. Devuelve None si no existe."""
        return next((a for a in self.installed_agents if a["name"] == name), None)

    def list_agents(self) -> list[dict]:
        """Devuelve un resumen plano (sin actions anidadas) para endpoints /agents."""
        return [
            {
                "name": a["name"],
                "description": a["description"],
                "endpoint": a["endpoint"],
                "actions_count": len(a["actions"]),
            }
            for a in self.installed_agents
        ]
