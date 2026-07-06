"use client";

import { useEffect, useRef, useState, useCallback } from "react";

// ============================================================
// Types matching the Python kernel's WS events
// ============================================================
export type AgentInfo = {
  name: string;
  description: string;
  endpoint: string;
  actions_count: number;
};

export type PlanStep = {
  step: number;
  step_id: string;
  agent: string;
  action: string;
  parameters: Record<string, unknown>;
  status: "pending" | "approved" | "rejected" | "timeout" | "running" | "done" | "error";
  requires_approval?: boolean;
  result?: unknown;
  error?: string;
  timestamp: number;
};

export type LogEvent = {
  id: string;
  event: string;
  data: Record<string, unknown>;
  timestamp: number;
};

export type PendingApproval = {
  step_id: string;
  step: number;
  agent: string;
  action: string;
  parameters: Record<string, unknown>;
  timeout_sec: number;
  received_at: number;
};

type AiosState = {
  connected: boolean;
  demoMode: boolean;
  agents: AgentInfo[];
  thought: string | null;
  steps: PlanStep[];
  logs: LogEvent[];
  pendingApprovals: PendingApproval[];
  llmProvider: string | null;
  thinking: boolean;
};

const INITIAL: AiosState = {
  connected: false,
  demoMode: false,
  agents: [],
  thought: null,
  steps: [],
  logs: [],
  pendingApprovals: [],
  llmProvider: null,
  thinking: false,
};

// ============================================================
// Demo agents — mirror the real ai-os/agents/ catalog
// ============================================================
const DEMO_AGENTS: AgentInfo[] = [
  { name: "pc_controller", description: "Controla archivos, ventanas, mouse y teclado en el sistema operativo local.", endpoint: "http://localhost:8001/execute", actions_count: 4 },
  { name: "android_adb", description: "Controla un dispositivo Android conectado por USB o red mediante ADB.", endpoint: "http://localhost:8002/execute", actions_count: 4 },
  { name: "memory", description: "Memoria persistente del usuario. Guarda hechos, preferencias y documentos.", endpoint: "http://localhost:8003/execute", actions_count: 5 },
  { name: "browser", description: "Controla un navegador web real (Chromium) via Playwright.", endpoint: "http://localhost:8004/execute", actions_count: 9 },
  { name: "home_assistant", description: "Controla dispositivos del hogar via Home Assistant.", endpoint: "http://localhost:8005/execute", actions_count: 7 },
  { name: "n8n", description: "Dispara flujos de trabajo en n8n (Gmail, WhatsApp, Calendar, etc.).", endpoint: "http://localhost:8006/execute", actions_count: 2 },
  { name: "open_interpreter", description: "Ejecuta codigo Python y comandos de shell en la PC local.", endpoint: "http://localhost:8007/execute", actions_count: 2 },
];

let logIdCounter = 0;
function nextLogId() {
  return `log-${++logIdCounter}`;
}

// ============================================================
// Hook
// ============================================================
export function useAios(kernelUrl: string) {
  const [state, setState] = useState<AiosState>(INITIAL);
  const wsRef = useRef<WebSocket | null>(null);
  const demoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const addLog = useCallback((event: string, data: Record<string, unknown>) => {
    setState((s) => ({
      ...s,
      logs: [
        ...s.logs.slice(-199), // keep last 200
        { id: nextLogId(), event, data, timestamp: Date.now() },
      ],
    }));
  }, []);

  // ---- Demo mode callbacks (must be declared before sendCommand/approve/reject) ----
  const runDemoCommand = useCallback((text: string) => {
    demoRunCommand(text, addLog, setState);
  }, [addLog]);

  const demoApprove = useCallback((stepId: string) => {
    demoDoApprove(stepId, addLog, setState);
  }, [addLog]);

  const demoReject = useCallback((stepId: string, reason: string) => {
    demoDoReject(stepId, reason, addLog, setState);
  }, [addLog]);

  const sendCommand = useCallback((text: string) => {
    if (!text.trim()) return;
    if (state.demoMode) {
      runDemoCommand(text);
      return;
    }
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    // For real kernel, we POST to /command. But for the dashboard,
    // we send the command via WS (the kernel would need to support this,
    // OR we POST). We'll POST via fetch.
    fetch(`${kernelUrl.replace("ws", "http").replace("/ws", "")}/command`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).catch((e) => addLog("fetch.error", { error: String(e) }));
    addLog("user.command", { text });
    setState((s) => ({ ...s, thought: null, steps: [], thinking: true }));
  }, [state.demoMode, kernelUrl, addLog, runDemoCommand]);

  const approve = useCallback((stepId: string) => {
    if (state.demoMode) {
      demoApprove(stepId);
      return;
    }
    fetch(`${kernelUrl.replace("ws", "http").replace("/ws", "")}/approve/${stepId}`, {
      method: "POST",
    }).catch((e) => addLog("fetch.error", { error: String(e) }));
  }, [state.demoMode, kernelUrl, addLog, demoApprove]);

  const reject = useCallback((stepId: string, reason: string = "") => {
    if (state.demoMode) {
      demoReject(stepId, reason);
      return;
    }
    fetch(`${kernelUrl.replace("ws", "http").replace("/ws", "")}/reject/${stepId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    }).catch((e) => addLog("fetch.error", { error: String(e) }));
  }, [state.demoMode, kernelUrl, addLog, demoReject]);

  // ---- Real WebSocket connection ----
  const connect = useCallback(() => {
    if (wsRef.current) {
      try { wsRef.current.close(); } catch {}
    }
    try {
      const ws = new WebSocket(kernelUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setState((s) => ({ ...s, connected: true, demoMode: false }));
        addLog("ws.connected", { url: kernelUrl });
      };

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          handleKernelEvent(msg, addLog, setState);
        } catch {
          // ignore non-JSON
        }
      };

      ws.onerror = () => {
        addLog("ws.error", { message: "connection failed" });
      };

      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false }));
        addLog("ws.disconnected", {});
        // Try demo mode after a short delay
        if (!demoTimerRef.current) {
          demoTimerRef.current = setTimeout(() => {
            startDemoMode(addLog, setState);
          }, 1500);
        }
      };
    } catch {
      startDemoMode(addLog, setState);
    }
  }, [kernelUrl, addLog]);

  useEffect(() => {
    // Initial: try real connection, fall back to demo
    const timeout = setTimeout(() => {
      if (!state.connected && !state.demoMode) {
        startDemoMode(addLog, setState);
      }
    }, 2500);
    connect();

    return () => {
      clearTimeout(timeout);
      if (demoTimerRef.current) clearTimeout(demoTimerRef.current);
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      if (wsRef.current) {
        try { wsRef.current.close(); } catch {}
      }
    };
  }, [kernelUrl]);

  return {
    ...state,
    sendCommand,
    approve,
    reject,
    reconnect: connect,
  };
}

// ============================================================
// Event handler for real kernel events
// ============================================================
function handleKernelEvent(
  msg: Record<string, unknown>,
  addLog: (e: string, d: Record<string, unknown>) => void,
  setState: React.Dispatch<React.SetStateAction<AiosState>>
) {
  const evt = msg.event as string;
  addLog(evt, msg);

  setState((s) => {
    switch (evt) {
      case "system.boot":
        return { ...s, agents: (msg.agents as AgentInfo[]) || [] };
      case "user.command":
        return { ...s, thought: null, steps: [], thinking: true };
      case "llm.response":
        return { ...s, thinking: false };
      case "llm.error":
      case "parse.error":
        return { ...s, thinking: false };
      case "step.start": {
        const step: PlanStep = {
          step: msg.step as number,
          step_id: msg.step_id as string,
          agent: msg.agent as string,
          action: msg.action as string,
          parameters: (msg.parameters as Record<string, unknown>) || {},
          status: "running",
          requires_approval: false,
          timestamp: Date.now(),
        };
        return { ...s, steps: [...s.steps, step] };
      }
      case "step.pending_approval": {
        const pending: PendingApproval = {
          step_id: msg.step_id as string,
          step: msg.step as number,
          agent: msg.agent as string,
          action: msg.action as string,
          parameters: (msg.parameters as Record<string, unknown>) || {},
          timeout_sec: (msg.timeout_sec as number) || 120,
          received_at: Date.now(),
        };
        return {
          ...s,
          pendingApprovals: [...s.pendingApprovals, pending],
          steps: s.steps.map((st) =>
            st.step_id === pending.step_id ? { ...st, status: "pending", requires_approval: true } : st
          ),
        };
      }
      case "step.approved":
        return {
          ...s,
          pendingApprovals: s.pendingApprovals.filter((p) => p.step_id !== msg.step_id),
          steps: s.steps.map((st) => st.step_id === msg.step_id ? { ...st, status: "running" } : st),
        };
      case "step.rejected":
      case "step.timeout":
        return {
          ...s,
          pendingApprovals: s.pendingApprovals.filter((p) => p.step_id !== msg.step_id),
          steps: s.steps.map((st) => st.step_id === msg.step_id ? { ...st, status: evt === "step.rejected" ? "rejected" : "timeout" } : st),
        };
      case "step.done":
        return {
          ...s,
          steps: s.steps.map((st) => st.step_id === msg.step_id ? { ...st, status: "done", result: msg.result } : st),
        };
      case "step.error":
        return {
          ...s,
          steps: s.steps.map((st) => st.step_id === msg.step_id ? { ...st, status: "error", error: String((msg as { error?: { error?: string } }).error?.error || "error") } : st),
        };
      default:
        return s;
    }
  });
}

// ============================================================
// DEMO MODE — simulates the full kernel flow so the UI is alive
// in the preview even without a real Python backend running.
// ============================================================
function startDemoMode(
  addLog: (e: string, d: Record<string, unknown>) => void,
  setState: React.Dispatch<React.SetStateAction<AiosState>>
) {
  setState((s) => ({
    ...s,
    demoMode: true,
    connected: false,
    agents: DEMO_AGENTS,
    llmProvider: "MEGATRON-5.5",
  }));
  addLog("demo.mode.activated", {
    message: "No real kernel detected — running in DEMO mode. Start your ai-os kernel to connect for real.",
  });
  addLog("system.boot", { agents: DEMO_AGENTS });
}

// Demo command simulation
const DEMO_SCENARIOS: Array<{
  match: RegExp;
  thought: string;
  steps: Array<{ agent: string; action: string; parameters: Record<string, unknown>; requires_approval?: boolean; result?: unknown }>;
}> = [
  {
    match: /recuerd|recordar|memoriza|remember/i,
    thought: "El usuario quiere que recuerde una informacion personal. Usare el agente memory con la accion remember.",
    steps: [
      { agent: "memory", action: "remember", parameters: { content: "Color favorito: azul marino", category: "preferences" }, result: { status: "success", id: "mem-abc123" } },
    ],
  },
  {
    match: /enciende|prende|turn.?on|luz|light/i,
    thought: "El usuario quiere controlar un dispositivo del hogar. Usare home_assistant.turn_on. Requiere aprobacion humana porque es una accion fisica.",
    steps: [
      { agent: "home_assistant", action: "turn_on", parameters: { entity_id: "light.living_room" }, requires_approval: true, result: { status: "success", service: "homeassistant.turn_on" } },
    ],
  },
  {
    match: /organiza|ordena|organize|tidy/i,
    thought: "El usuario quiere organizar archivos. Usare pc_controller.organize_folder con la ruta del escritorio.",
    steps: [
      { agent: "pc_controller", action: "organize_folder", parameters: { path: "C:/Users/User/Desktop" }, result: { status: "success", moved: 14 } },
    ],
  },
  {
    match: /navega|entra|abre.*web|browser|scrape|website|pagina/i,
    thought: "El usuario quiere hacer una tarea en el navegador. Usare el agente browser con Playwright: navegar, esperar y extraer texto.",
    steps: [
      { agent: "browser", action: "navigate", parameters: { url: "https://news.ycombinator.com" }, result: { status: "success", title: "Hacker News" } },
      { agent: "browser", action: "get_text", parameters: {}, result: { status: "success", text: "Top stories: AI breakthrough...", truncated: false } },
    ],
  },
  {
    match: /envia|correo|email|gmail|whatsapp|mensaje/i,
    thought: "El usuario quiere enviar un mensaje. Usare n8n para disparar el webhook pre-configurado de envio de correo.",
    steps: [
      { agent: "n8n", action: "trigger_workflow", parameters: { webhook_id: "send-email", data: { to: "carlos@example.com", subject: "Hola" } }, result: { status: "success", status_code: 200 } },
    ],
  },
  {
    match: /script|codigo|code|python|calcula|procesa/i,
    thought: "El usuario quiere ejecutar codigo personalizado. Usare open_interpreter.run_python. Requiere aprobacion humana porque ejecuta codigo arbitrario.",
    steps: [
      { agent: "open_interpreter", action: "run_python", parameters: { code: "import sys; print(f'Python {sys.version}')", timeout: 10 }, requires_approval: true, result: { status: "success", stdout: "Python 3.12.13", returncode: 0 } },
    ],
  },
];

const DEFAULT_SCENARIO = {
  thought: "He analizado la solicitud. Generare un plan multi-step combinando agentes para resolver la tarea.",
  steps: [
    { agent: "memory", action: "recall", parameters: { query: "contexto del usuario", limit: 3 }, result: { status: "success", memories: [], count: 0 } },
    { agent: "pc_controller", action: "list_files", parameters: { path: "/home/user" }, result: { status: "success", items: [], count: 0 } },
  ],
};

let demoStepCounter = 0;
function demoRunCommand(
  text: string,
  addLog: (e: string, d: Record<string, unknown>) => void,
  setState: React.Dispatch<React.SetStateAction<AiosState>>
) {
  addLog("user.command", { text });
  setState((s) => ({ ...s, thought: null, steps: [], thinking: true }));

  const scenario = DEMO_SCENARIOS.find((sc) => sc.match.test(text)) || DEFAULT_SCENARIO;

  // Simulate LLM thinking time
  setTimeout(() => {
    addLog("llm.response", { thought: scenario.thought });
    setState((s) => ({ ...s, thinking: false, thought: scenario.thought }));

    // Then execute steps one by one
    scenario.steps.forEach((stepDef, idx) => {
      const stepId = `demo-${++demoStepCounter}`;
      const stepNum = idx + 1;

      setTimeout(() => {
        addLog("step.start", { step: stepNum, step_id: stepId, agent: stepDef.agent, action: stepDef.action, parameters: stepDef.parameters });

        const newStep: PlanStep = {
          step: stepNum,
          step_id: stepId,
          agent: stepDef.agent,
          action: stepDef.action,
          parameters: stepDef.parameters,
          status: stepDef.requires_approval ? "pending" : "running",
          requires_approval: stepDef.requires_approval,
          timestamp: Date.now(),
        };
        setState((s) => ({ ...s, steps: [...s.steps, newStep] }));

        if (stepDef.requires_approval) {
          addLog("step.pending_approval", {
            step: stepNum, step_id: stepId, agent: stepDef.agent, action: stepDef.action,
            parameters: stepDef.parameters, timeout_sec: 60,
          });
          setState((s) => ({
            ...s,
            pendingApprovals: [...s.pendingApprovals, {
              step_id: stepId, step: stepNum, agent: stepDef.agent, action: stepDef.action,
              parameters: stepDef.parameters, timeout_sec: 60, received_at: Date.now(),
            }],
          }));
          // The step waits for explicit approve/reject — see demoDoApprove/demoDoReject
        } else {
          // Simulate execution time then mark done
          const execTime = 600 + Math.random() * 1200;
          setTimeout(() => {
            addLog("step.done", { step: stepNum, step_id: stepId, result: stepDef.result });
            setState((s) => ({
              ...s,
              steps: s.steps.map((st) => st.step_id === stepId ? { ...st, status: "done", result: stepDef.result } : st),
            }));
          }, execTime);
        }
      }, 300 + idx * 800);
    });
  }, 800 + Math.random() * 600);
}

function demoDoApprove(
  stepId: string,
  addLog: (e: string, d: Record<string, unknown>) => void,
  setState: React.Dispatch<React.SetStateAction<AiosState>>
) {
  addLog("step.approved", { step_id: stepId });
  setState((s) => ({
    ...s,
    pendingApprovals: s.pendingApprovals.filter((p) => p.step_id !== stepId),
    steps: s.steps.map((st) => st.step_id === stepId ? { ...st, status: "running" } : st),
  }));

  // Simulate execution then done
  const execTime = 700 + Math.random() * 1000;
  setTimeout(() => {
    setState((s) => {
      const st = s.steps.find((x) => x.step_id === stepId);
      const fakeResult = { status: "success", message: `Action ${st?.action || ""} executed` };
      addLog("step.done", { step_id: stepId, result: fakeResult });
      return {
        ...s,
        steps: s.steps.map((x) => x.step_id === stepId ? { ...x, status: "done", result: fakeResult } : x),
      };
    });
  }, execTime);
}

function demoDoReject(
  stepId: string,
  reason: string,
  addLog: (e: string, d: Record<string, unknown>) => void,
  setState: React.Dispatch<React.SetStateAction<AiosState>>
) {
  addLog("step.rejected", { step_id: stepId, reason });
  setState((s) => ({
    ...s,
    pendingApprovals: s.pendingApprovals.filter((p) => p.step_id !== stepId),
    steps: s.steps.map((st) => st.step_id === stepId ? { ...st, status: "rejected", error: `Rechazado: ${reason}` } : st),
  }));
}
