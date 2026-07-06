"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Activity,
  AlertTriangle,
  Brain,
  CheckCircle2,
  Cpu,
  Database,
  Globe,
  Home as HomeIcon,
  MonitorSmartphone,
  Send,
  Smartphone,
  Terminal,
  XCircle,
  Clock,
  Zap,
  Radio,
  Layers,
} from "lucide-react";
import { useAios, type AgentInfo, type PlanStep, type PendingApproval, type LogEvent } from "@/hooks/use-aios";

const KERNEL_URL = "ws://localhost:8000/ws";

// ============================================================
// Agent icon mapping
// ============================================================
const AGENT_ICONS: Record<string, React.ComponentType<React.SVGProps<SVGSVGElement>>> = {
  pc_controller: MonitorSmartphone,
  android_adb: Smartphone,
  memory: Database,
  browser: Globe,
  home_assistant: HomeIcon,
  n8n: Layers,
  open_interpreter: Terminal,
};

// ============================================================
// MAIN PAGE
// ============================================================
export default function Home() {
  const aios = useAios(KERNEL_URL);
  const [input, setInput] = useState("");
  const [clock, setClock] = useState(new Date());

  useEffect(() => {
    const t = setInterval(() => setClock(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const submit = () => {
    if (!input.trim()) return;
    aios.sendCommand(input);
    setInput("");
  };

  return (
    <div className="h-screen w-screen overflow-hidden flex flex-col scanlines relative">
      {/* Top status bar */}
      <TopBar
        connected={aios.connected}
        demoMode={aios.demoMode}
        llmProvider={aios.llmProvider}
        clock={clock}
        agentCount={aios.agents.length}
      />

      {/* Main 3-column layout */}
      <div className="flex-1 grid grid-cols-12 gap-3 p-3 min-h-0">
        {/* Left: agents */}
        <aside className="col-span-3 min-h-0 flex flex-col gap-3">
          <AgentsPanel agents={aios.agents} />
          <SystemPanel aios={aios} />
        </aside>

        {/* Center: command + plan */}
        <main className="col-span-6 min-h-0 flex flex-col gap-3">
          <CommandPanel
            input={input}
            setInput={setInput}
            submit={submit}
            thinking={aios.thinking}
            demoMode={aios.demoMode}
          />
          <PlanPanel thought={aios.thought} steps={aios.steps} thinking={aios.thinking} />
        </main>

        {/* Right: approvals + logs */}
        <aside className="col-span-3 min-h-0 flex flex-col gap-3">
          <ApprovalsPanel
            pending={aios.pendingApprovals}
            onApprove={aios.approve}
            onReject={aios.reject}
          />
          <LogsPanel logs={aios.logs} />
        </aside>
      </div>
    </div>
  );
}

// ============================================================
// TOP STATUS BAR
// ============================================================
function TopBar({
  connected, demoMode, llmProvider, clock, agentCount,
}: {
  connected: boolean; demoMode: boolean; llmProvider: string | null; clock: Date; agentCount: number;
}) {
  const status = connected ? "ONLINE" : demoMode ? "DEMO" : "OFFLINE";
  const dotClass = connected ? "status-dot-online" : demoMode ? "status-dot-busy" : "status-dot-offline";
  const statusColor = connected ? "text-[var(--neon-green)]" : demoMode ? "text-[var(--neon-amber)]" : "text-[var(--neon-red)]";

  return (
    <header className="hud-panel mx-3 mt-3 px-4 py-2 flex items-center justify-between gap-6 flex-shrink-0">
      <div className="flex items-center gap-3">
        <motion.div
          initial={{ rotate: 0 }}
          animate={{ rotate: 360 }}
          transition={{ duration: 12, repeat: Infinity, ease: "linear" }}
        >
          <Brain className="w-6 h-6 text-[var(--neon-cyan)] glow-cyan" />
        </motion.div>
        <div className="flex flex-col">
          <h1 className="text-sm font-bold tracking-[0.3em] text-[var(--neon-cyan)] glow-cyan glitch-hover">
            AI OS // CORE
          </h1>
          <span className="text-[10px] text-[var(--text-faint)] tracking-widest uppercase">
            Neural Command Interface v2.0
          </span>
        </div>
      </div>

      <div className="flex items-center gap-6 text-xs">
        <StatusChip label="STATUS" value={status} colorClass={statusColor} dotClass={dotClass} />
        <StatusChip label="LLM" value={llmProvider || "—"} colorClass="text-[var(--neon-magenta)] glow-magenta" icon={<Cpu className="w-3 h-3" />} />
        <StatusChip label="AGENTS" value={String(agentCount)} colorClass="text-[var(--neon-cyan)]" icon={<Radio className="w-3 h-3" />} />
        <div className="flex items-center gap-2 text-[var(--text-dim)]">
          <Clock className="w-3 h-3" />
          <span className="font-mono text-[var(--text-primary)] tabular-nums">
            {clock.toLocaleTimeString("es-ES", { hour12: false })}
          </span>
        </div>
      </div>
    </header>
  );
}

function StatusChip({
  label, value, colorClass, dotClass, icon,
}: {
  label: string; value: string; colorClass: string; dotClass?: string; icon?: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2">
      {dotClass && <span className={`status-dot ${dotClass}`} />}
      {icon}
      <span className="text-[10px] text-[var(--text-faint)] tracking-widest uppercase">{label}</span>
      <span className={`font-bold tracking-wider ${colorClass}`}>{value}</span>
    </div>
  );
}

// ============================================================
// LEFT — AGENTS PANEL
// ============================================================
function AgentsPanel({ agents }: { agents: AgentInfo[] }) {
  return (
    <div className="hud-panel flex-1 min-h-0 flex flex-col">
      <PanelHeader title="AGENT NETWORK" subtitle={`${agents.length} DISCOVERED`} icon={<Layers className="w-3.5 h-3.5" />} />
      <div className="flex-1 overflow-y-auto futuristic-scroll p-2 space-y-1.5">
        <AnimatePresence>
          {agents.map((agent, i) => (
            <AgentCard key={agent.name} agent={agent} index={i} />
          ))}
        </AnimatePresence>
        {agents.length === 0 && (
          <div className="text-center py-8 text-[var(--text-faint)] text-xs">
            Scanning agent network...
          </div>
        )}
      </div>
    </div>
  );
}

function AgentCard({ agent, index }: { agent: AgentInfo; index: number }) {
  const Icon = AGENT_ICONS[agent.name] || Activity;
  const port = agent.endpoint.match(/:(\d+)/)?.[1] || "?";
  return (
    <motion.div
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.05 }}
      className="group relative p-2.5 border border-[rgba(0,240,255,0.1)] hover:border-[var(--neon-cyan)] bg-black/40 transition-all cursor-pointer"
    >
      <div className="flex items-start gap-2.5">
        <div className="p-1.5 bg-[rgba(0,240,255,0.06)] border border-[rgba(0,240,255,0.2)] group-hover:bg-[rgba(0,240,255,0.15)] transition-colors">
          <Icon className="w-4 h-4 text-[var(--neon-cyan)]" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold text-[var(--text-primary)] tracking-wide truncate">
              {agent.name}
            </span>
            <span className="text-[9px] text-[var(--text-faint)] font-mono">:{port}</span>
          </div>
          <p className="text-[10px] text-[var(--text-dim)] leading-tight mt-0.5 line-clamp-2">
            {agent.description}
          </p>
          <div className="flex items-center gap-1 mt-1.5">
            <span className="text-[9px] text-[var(--neon-magenta)] tracking-wider">
              {agent.actions_count} ACTIONS
            </span>
            <span className="status-dot status-dot-online ml-auto" />
          </div>
        </div>
      </div>
    </motion.div>
  );
}

// ============================================================
// LEFT — SYSTEM PANEL (LLM chain visualization)
// ============================================================
function SystemPanel({ aios }: { aios: ReturnType<typeof useAios> }) {
  const providers = ["MEGATRON-5.5", "OLLAMA", "OPENAI", "ANTHROPIC"];
  return (
    <div className="hud-panel hud-panel-magenta flex-shrink-0">
      <PanelHeader title="LLM CHAIN" subtitle="FALLBACK ORDER" icon={<Zap className="w-3.5 h-3.5" />} magenta />
      <div className="p-3 space-y-1.5">
        {providers.map((p, i) => {
          const active = aios.llmProvider === p || (aios.demoMode && i === 0);
          return (
            <div key={p} className="flex items-center gap-2 text-xs">
              <span className="text-[var(--text-faint)] font-mono w-4">{i + 1}.</span>
              <span className={`font-bold tracking-wider ${active ? "text-[var(--neon-magenta)] glow-magenta" : "text-[var(--text-dim)]"}`}>
                {p}
              </span>
              {active && <span className="status-dot status-dot-online ml-auto" />}
              {i < providers.length - 1 && (
                <span className="absolute right-3 text-[var(--text-faint)] text-[10px]">↓</span>
              )}
            </div>
          );
        })}
        <div className="pt-2 mt-2 border-t border-[rgba(255,0,212,0.15)] text-[10px] text-[var(--text-faint)] tracking-wider">
          APPROVAL: <span className="text-[var(--neon-green)] glow-green">ENABLED</span>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// CENTER — COMMAND PANEL
// ============================================================
function CommandPanel({
  input, setInput, submit, thinking, demoMode,
}: {
  input: string; setInput: (v: string) => void; submit: () => void; thinking: boolean; demoMode: boolean;
}) {
  const taRef = useRef<HTMLTextAreaElement>(null);

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const examples = [
    "Recuerda que mi color favorito es el azul marino",
    "Enciende la luz del salon",
    "Organiza mi escritorio",
    "Entra a wikipedia.org y dime que dice",
    "Envia un correo a Carlos",
  ];

  return (
    <div className="hud-panel flex-shrink-0">
      <PanelHeader title="COMMAND INTERFACE" subtitle={demoMode ? "DEMO MODE — TYPE ANYTHING" : "AWAITING INPUT"} icon={<Send className="w-3.5 h-3.5" />} />
      <div className="p-3">
        <div className="relative">
          <span className="absolute left-3 top-3 text-[var(--neon-cyan)] glow-cyan font-bold text-sm">
            ❯
          </span>
          <textarea
            ref={taRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Describe la tarea en lenguaje natural..."
            rows={2}
            className="term-input w-full pl-8 pr-3 py-2.5 text-sm resize-none futuristic-scroll"
            disabled={thinking}
          />
          {thinking && (
            <div className="absolute right-3 top-3 flex items-center gap-1.5 text-[10px] text-[var(--neon-amber)]">
              <div className="w-3 h-3 border border-[var(--neon-amber)] border-t-transparent rounded-full spin-slow" />
              THINKING
            </div>
          )}
        </div>
        <div className="flex items-center justify-between mt-2">
          <div className="flex flex-wrap gap-1">
            {examples.map((ex) => (
              <button
                key={ex}
                onClick={() => setInput(ex)}
                disabled={thinking}
                className="text-[9px] px-1.5 py-0.5 border border-[rgba(0,240,255,0.15)] text-[var(--text-dim)] hover:text-[var(--neon-cyan)] hover:border-[var(--neon-cyan)] transition-colors tracking-wider uppercase"
              >
                {ex.length > 30 ? ex.slice(0, 30) + "..." : ex}
              </button>
            ))}
          </div>
          <button
            onClick={submit}
            disabled={thinking || !input.trim()}
            className="btn-neon px-4 py-1.5 text-xs"
          >
            ▶ EXECUTE
          </button>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// CENTER — PLAN PANEL (thought + steps timeline)
// ============================================================
function PlanPanel({
  thought, steps, thinking,
}: {
  thought: string | null; steps: PlanStep[]; thinking: boolean;
}) {
  return (
    <div className="hud-panel flex-1 min-h-0 flex flex-col">
      <PanelHeader title="EXECUTION PLAN" subtitle={thought ? "LLM DISPATCHED" : "IDLE"} icon={<Brain className="w-3.5 h-3.5" />} />

      <div className="flex-1 overflow-y-auto futuristic-scroll p-3 space-y-3">
        {/* Thought bubble */}
        <AnimatePresence mode="wait">
          {thought && (
            <motion.div
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="border border-[rgba(255,0,212,0.3)] bg-[rgba(255,0,212,0.04)] p-3"
            >
              <div className="flex items-center gap-2 mb-1.5">
                <Brain className="w-3 h-3 text-[var(--neon-magenta)]" />
                <span className="text-[10px] text-[var(--neon-magenta)] glow-magenta tracking-widest uppercase font-bold">
                  Hermes Thought
                </span>
              </div>
              <p className="text-xs text-[var(--text-primary)] leading-relaxed italic">
                "{thought}"
              </p>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Thinking state */}
        {thinking && !thought && (
          <div className="flex items-center justify-center py-8 gap-3">
            <div className="w-5 h-5 border-2 border-[var(--neon-cyan)] border-t-transparent rounded-full spin-slow" />
            <span className="text-xs text-[var(--neon-cyan)] glow-cyan tracking-widest">
              HERMES IS THINKING<span className="cursor-blink" />
            </span>
          </div>
        )}

        {/* Steps timeline */}
        <div className="space-y-2">
          <AnimatePresence>
            {steps.map((step) => (
              <StepCard key={step.step_id} step={step} />
            ))}
          </AnimatePresence>
        </div>

        {!thought && !thinking && steps.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <Brain className="w-12 h-12 text-[var(--text-faint)] mb-3" />
            <p className="text-xs text-[var(--text-faint)] tracking-widest uppercase">
              Awaiting your command
            </p>
            <p className="text-[10px] text-[var(--text-faint)] mt-1">
              The execution plan will appear here
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function StepCard({ step }: { step: PlanStep }) {
  const Icon = AGENT_ICONS[step.agent] || Activity;
  const statusConfig = {
    running: { color: "var(--neon-cyan)", label: "EXECUTING", icon: <div className="w-3 h-3 border border-[var(--neon-cyan)] border-t-transparent rounded-full spin-slow" /> },
    pending: { color: "var(--neon-amber)", label: "PENDING APPROVAL", icon: <AlertTriangle className="w-3 h-3" /> },
    done: { color: "var(--neon-green)", label: "DONE", icon: <CheckCircle2 className="w-3 h-3" /> },
    rejected: { color: "var(--neon-red)", label: "REJECTED", icon: <XCircle className="w-3 h-3" /> },
    timeout: { color: "var(--neon-red)", label: "TIMEOUT", icon: <Clock className="w-3 h-3" /> },
    error: { color: "var(--neon-red)", label: "ERROR", icon: <AlertTriangle className="w-3 h-3" /> },
    approved: { color: "var(--neon-cyan)", label: "APPROVED", icon: <CheckCircle2 className="w-3 h-3" /> },
  }[step.status];

  const isPending = step.status === "pending";
  const isRunning = step.status === "running";

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      className={`relative border bg-black/40 p-3 ${isPending ? "pulse-amber border-[var(--neon-amber)]" : ""}`}
      style={{
        borderColor: isPending ? undefined : `${statusConfig.color}40`,
      }}
    >
      {isRunning && <div className="sweep-line" />}
      <div className="flex items-start gap-3">
        {/* Step number */}
        <div
          className="flex-shrink-0 w-7 h-7 flex items-center justify-center border font-bold text-xs"
          style={{ borderColor: statusConfig.color, color: statusConfig.color }}
        >
          {String(step.step).padStart(2, "0")}
        </div>

        {/* Agent + action */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <Icon className="w-3.5 h-3.5" style={{ color: statusConfig.color }} />
            <span className="text-xs font-bold text-[var(--text-primary)] tracking-wide">
              {step.agent}
            </span>
            <span className="text-[var(--text-faint)] text-xs">→</span>
            <span className="text-xs font-mono" style={{ color: statusConfig.color }}>
              {step.action}()
            </span>
            <div className="ml-auto flex items-center gap-1.5" style={{ color: statusConfig.color }}>
              {statusConfig.icon}
              <span className="text-[9px] font-bold tracking-widest">
                {statusConfig.label}
              </span>
            </div>
          </div>

          {/* Parameters */}
          <div className="mt-1.5 text-[10px] font-mono text-[var(--text-dim)] break-all">
            <span className="text-[var(--text-faint)]">params:</span>{" "}
            {JSON.stringify(step.parameters)}
          </div>

          {/* Result */}
          {step.result != null && (
            <div className="mt-1.5 text-[10px] font-mono break-all border-l-2 pl-2" style={{ borderColor: `${statusConfig.color}60` }}>
              <span className="text-[var(--text-faint)]">result:</span>{" "}
              <span style={{ color: statusConfig.color }}>
                {typeof step.result === "string" ? step.result : JSON.stringify(step.result)}
              </span>
            </div>
          )}

          {/* Error */}
          {step.error && (
            <div className="mt-1.5 text-[10px] text-[var(--neon-red)] break-all">
              ✗ {step.error}
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}

// ============================================================
// RIGHT — APPROVALS PANEL
// ============================================================
function ApprovalsPanel({
  pending, onApprove, onReject,
}: {
  pending: PendingApproval[];
  onApprove: (id: string) => void;
  onReject: (id: string, reason: string) => void;
}) {
  return (
    <div className="hud-panel flex-shrink-0">
      <PanelHeader
        title="HUMAN APPROVAL"
        subtitle={pending.length > 0 ? `${pending.length} PENDING` : "CLEAR"}
        icon={<AlertTriangle className="w-3.5 h-3.5" />}
        magenta={pending.length > 0}
      />
      <div className="p-2 space-y-2 max-h-64 overflow-y-auto futuristic-scroll">
        <AnimatePresence>
          {pending.map((p) => (
            <ApprovalCard key={p.step_id} approval={p} onApprove={onApprove} onReject={onReject} />
          ))}
        </AnimatePresence>
        {pending.length === 0 && (
          <div className="text-center py-4 text-[10px] text-[var(--text-faint)] tracking-widest">
            NO APPROVALS REQUIRED
          </div>
        )}
      </div>
    </div>
  );
}

function ApprovalCard({
  approval, onApprove, onReject,
}: {
  approval: PendingApproval;
  onApprove: (id: string) => void;
  onReject: (id: string, reason: string) => void;
}) {
  const Icon = AGENT_ICONS[approval.agent] || Activity;
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.95 }}
      className="border border-[var(--neon-amber)] pulse-amber p-2.5 bg-[rgba(255,184,0,0.04)]"
    >
      <div className="flex items-center gap-2 mb-1.5">
        <Icon className="w-3.5 h-3.5 text-[var(--neon-amber)]" />
        <span className="text-xs font-bold text-[var(--neon-amber)] glow-cyan tracking-wide">
          {approval.agent} → {approval.action}
        </span>
      </div>
      <div className="text-[10px] font-mono text-[var(--text-dim)] break-all mb-2">
        {JSON.stringify(approval.parameters)}
      </div>
      <div className="flex gap-1.5">
        <button
          onClick={() => onApprove(approval.step_id)}
          className="btn-neon btn-neon-green flex-1 py-1 text-[10px]"
        >
          ✓ APPROVE
        </button>
        <button
          onClick={() => onReject(approval.step_id, "user rejected")}
          className="btn-neon btn-neon-red flex-1 py-1 text-[10px]"
        >
          ✗ REJECT
        </button>
      </div>
    </motion.div>
  );
}

// ============================================================
// RIGHT — LOGS PANEL
// ============================================================
function LogsPanel({ logs }: { logs: LogEvent[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [logs]);

  return (
    <div className="hud-panel flex-1 min-h-0 flex flex-col">
      <PanelHeader title="EVENT FEED" subtitle="REAL-TIME" icon={<Activity className="w-3.5 h-3.5" />} />
      <div ref={containerRef} className="flex-1 overflow-y-auto futuristic-scroll p-2 space-y-0.5 font-mono text-[10px]">
        <AnimatePresence initial={false}>
          {logs.map((log) => (
            <LogLine key={log.id} log={log} />
          ))}
        </AnimatePresence>
        {logs.length === 0 && (
          <div className="text-center py-8 text-[var(--text-faint)]">
            Waiting for events<span className="cursor-blink" />
          </div>
        )}
      </div>
    </div>
  );
}

function LogLine({ log }: { log: LogEvent }) {
  const time = new Date(log.timestamp).toLocaleTimeString("es-ES", { hour12: false });
  const color = useMemo(() => {
    if (log.event.includes("error") || log.event.includes("reject")) return "var(--neon-red)";
    if (log.event.includes("approve")) return "var(--neon-green)";
    if (log.event.includes("pending")) return "var(--neon-amber)";
    if (log.event.includes("done") || log.event.includes("success") || log.event.includes("connected")) return "var(--neon-green)";
    if (log.event.includes("llm") || log.event.includes("thought")) return "var(--neon-magenta)";
    if (log.event.includes("step")) return "var(--neon-cyan)";
    return "var(--text-dim)";
  }, [log.event]);

  const summary = useMemo(() => {
    const d = log.data;
    if (log.event === "user.command") return `> ${d.text}`;
    if (log.event === "step.start") return `[${d.agent}] ${d.action}()`;
    if (log.event === "step.done") return `[done] ${typeof d.result === "string" ? d.result : JSON.stringify(d.result)}`;
    if (log.event === "step.pending_approval") return `[${d.agent}] ${d.action}() REQUIRES APPROVAL`;
    if (log.event === "demo.mode.activated") return "DEMO MODE: no kernel detected";
    if (log.event === "ws.connected") return `WebSocket connected to ${d.url}`;
    if (log.event === "llm.response") return "LLM responded";
    if (log.event === "system.boot") return `${(d.agents as unknown[] | undefined)?.length || 0} agents discovered`;
    if (log.event === "step.approved") return "Approved by human";
    if (log.event === "step.rejected") return `Rejected: ${d.reason || ""}`;
    return JSON.stringify(d).slice(0, 80);
  }, [log]);

  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      className="flex gap-2 leading-tight slide-in"
    >
      <span className="text-[var(--text-faint)] flex-shrink-0 tabular-nums">{time}</span>
      <span style={{ color }} className="font-bold uppercase tracking-wide flex-shrink-0">
        {log.event}
      </span>
      <span className="text-[var(--text-dim)] truncate">{summary}</span>
    </motion.div>
  );
}

// ============================================================
// SHARED — Panel header
// ============================================================
function PanelHeader({
  title, subtitle, icon, magenta = false,
}: {
  title: string; subtitle?: string; icon?: React.ReactNode; magenta?: boolean;
}) {
  const color = magenta ? "var(--neon-magenta)" : "var(--neon-cyan)";
  return (
    <div
      className="flex items-center justify-between px-3 py-1.5 border-b"
      style={{ borderColor: `${color}25`, background: `${color}06` }}
    >
      <div className="flex items-center gap-2">
        {icon && <span style={{ color }}>{icon}</span>}
        <span className="text-[10px] font-bold tracking-[0.2em] uppercase" style={{ color }}>
          {title}
        </span>
      </div>
      {subtitle && (
        <span className="text-[9px] text-[var(--text-faint)] tracking-widest uppercase">
          {subtitle}
        </span>
      )}
    </div>
  );
}
