"""
llm_client.py
=============
Cliente LLM unificado vía LiteLLM.

Soporta una **cadena de fallback** configurable:

    1. NVIDIA Megatron / NIM (OpenAI-compatible)   ← tu primario
    2. Ollama local                                ← offline / privacidad
    3. OpenAI / Anthropic (opcional)               ← tareas muy complejas

Si el primario falla (timeout, 5xx, conexión), cae al siguiente automáticamente.
Todo se configura por variables de entorno — ver `.env.example`.

Modelos soportados por LiteLLM (prefijos):
    openai/<model>          → cualquier endpoint OpenAI-compatible (Megatron!)
    ollama/<model>          → Ollama local
    anthropic/<model>       → Claude
    gpt-4o, gpt-4o-mini     → OpenAI nativo
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

# LiteLLM es opcional en tiempo de import — lo envolvemos para que el server
# arranque incluso si todavía no está instalado. Lanzará error claro al usar.
try:
    import litellm

    litellm.drop_params = True  # descarta silenciosamente params no soportados
    litellm.suppress_debug_info = True
    _LITELLM_AVAILABLE = True
except ImportError:  # pragma: no cover
    litellm = None  # type: ignore
    _LITELLM_AVAILABLE = False

logger = logging.getLogger("aios.llm")


# --------------------------------------------------------------------
# Configuración por variable de entorno
# --------------------------------------------------------------------
@dataclass
class ProviderConfig:
    """Un proveedor LLM con su modelo y parámetros."""

    name: str  # identificador para logs
    model: str  # ej: "openai/megatron-5.5", "ollama/hermes"
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    enabled: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


def _load_providers_from_env() -> list[ProviderConfig]:
    """Construye la cadena de fallback leyendo variables de entorno.

    Orden de prioridad:
      1. LLM_MEGATRON_*  (NVIDIA Megatron/NIM — OpenAI-compatible)
      2. LLM_OLLAMA_*    (Ollama local)
      3. LLM_OPENAI_*    (OpenAI cloud)
      4. LLM_ANTHROPIC_* (Claude cloud)
    """
    providers: list[ProviderConfig] = []

    # 1) NVIDIA Megatron / NIM (tu primario)
    megatron_model = os.getenv("LLM_MEGATRON_MODEL")  # ej: "openai/megatron-5.5"
    if megatron_model:
        providers.append(
            ProviderConfig(
                name="megatron",
                model=megatron_model,
                api_base=os.getenv("LLM_MEGATRON_BASE", "http://localhost:8000/v1"),
                api_key=os.getenv("LLM_MEGATRON_KEY", "nvidia-megatron"),
                enabled=os.getenv("LLM_MEGATRON_ENABLED", "true").lower() == "true",
            )
        )

    # 2) Ollama local
    ollama_model = os.getenv("LLM_OLLAMA_MODEL", "hermes")
    providers.append(
        ProviderConfig(
            name="ollama",
            model=f"ollama/{ollama_model}",
            api_base=os.getenv("LLM_OLLAMA_BASE", "http://localhost:11434"),
            api_key=os.getenv("LLM_OLLAMA_KEY", "ollama"),
            enabled=os.getenv("LLM_OLLAMA_ENABLED", "true").lower() == "true",
        )
    )

    # 3) OpenAI cloud (fallback opcional)
    if os.getenv("LLM_OPENAI_MODEL"):
        providers.append(
            ProviderConfig(
                name="openai",
                model=os.getenv("LLM_OPENAI_MODEL"),  # ej: "gpt-4o"
                api_key=os.getenv("LLM_OPENAI_KEY", ""),
                enabled=os.getenv("LLM_OPENAI_ENABLED", "false").lower() == "true",
            )
        )

    # 4) Anthropic (fallback opcional)
    if os.getenv("LLM_ANTHROPIC_MODEL"):
        providers.append(
            ProviderConfig(
                name="anthropic",
                model=os.getenv("LLM_ANTHROPIC_MODEL"),  # ej: "claude-3-5-sonnet-20240620"
                api_key=os.getenv("LLM_ANTHROPIC_KEY", ""),
                enabled=os.getenv("LLM_ANTHROPIC_ENABLED", "false").lower() == "true",
            )
        )

    return [p for p in providers if p.enabled]


# --------------------------------------------------------------------
# Cliente
# --------------------------------------------------------------------
class HermesClient:
    """Cliente LLM con fallback automático entre proveedores.

    Uso:
        hermes = HermesClient()  # carga config de .env
        texto = await hermes.achat(system_prompt=..., user_message=...)
    """

    def __init__(self, providers: Optional[list[ProviderConfig]] = None) -> None:
        if not _LITELLM_AVAILABLE:
            raise RuntimeError(
                "LiteLLM no está instalado. Ejecuta: pip install litellm"
            )
        self.providers = providers or _load_providers_from_env()
        if not self.providers:
            logger.warning(
                "No hay proveedores LLM configurados. "
                "Crea un .env (ver .env.example) o exporta las variables."
            )
        else:
            logger.info(
                "Cadena LLM activa: %s",
                " → ".join(p.name for p in self.providers),
            )
        self.timeout = float(os.getenv("LLM_TIMEOUT", "120"))

    # ------------------------------------------------------------------
    async def achat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,
        force_json: bool = True,
    ) -> str:
        """Llama al primer proveedor disponible. Si falla, prueba el siguiente."""
        if not self.providers:
            raise RuntimeError("Sin proveedores LLM configurados.")

        last_error: Optional[Exception] = None
        for provider in self.providers:
            try:
                logger.info("LLM → probando %s (%s)", provider.name, provider.model)
                content = await self._call_provider(
                    provider, system_prompt, user_message, temperature, force_json
                )
                logger.info("LLM ✓ respondió %s", provider.name)
                return content
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM ✗ %s falló: %s", provider.name, exc)
                last_error = exc
                continue

        raise RuntimeError(
            f"Todos los proveedores LLM fallaron. Último error: {last_error}"
        )

    def chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,
        force_json: bool = True,
    ) -> str:
        """Versión síncrona — útil para scripts de prueba."""
        import asyncio

        return asyncio.run(
            self.achat(system_prompt, user_message, temperature, force_json)
        )

    # ------------------------------------------------------------------
    async def _call_provider(
        self,
        provider: ProviderConfig,
        system_prompt: str,
        user_message: str,
        temperature: float,
        force_json: bool,
    ) -> str:
        """Hace la llamada real a un proveedor vía LiteLLM."""
        kwargs: dict[str, Any] = {
            "model": provider.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
            "timeout": self.timeout,
        }
        if provider.api_base:
            kwargs["api_base"] = provider.api_base
        if provider.api_key:
            kwargs["api_key"] = provider.api_key
        if force_json:
            # LiteLLM convierte esto al formato nativo de cada proveedor.
            kwargs["response_format"] = {"type": "json_object"}
        kwargs.update(provider.extra)

        # LiteLLM tiene versión async nativa
        response = await litellm.acompletion(**kwargs)
        return response["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------
    @staticmethod
    def parse_json_response(raw: str) -> Optional[dict]:
        """Extrae JSON aunque el LLM lo envuelva en markdown."""
        if not raw:
            return None
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(cleaned[start : end + 1])
                except json.JSONDecodeError:
                    return None
            return None

    def close(self) -> None:
        pass
