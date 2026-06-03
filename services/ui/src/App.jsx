import { useState } from 'react'
import SensorCharts from './components/SensorCharts'
import ChatPanel from './components/ChatPanel'
import DeviceStatus from './components/DeviceStatus'

// ── Alarm phase visual styles for the full-page background ────────────────────
const ALARM_STYLES = {
  Background: {
    bgClass: 'bg-gray-950',
    overlay: null,
    headerBg: 'bg-gray-900/80',
    headerBorder: 'border-gray-800',
    banner: null,
  },
  Nuisance: {
    bgClass: 'bg-gray-950',
    overlay: 'bg-yellow-500/5',
    headerBg: 'bg-yellow-900/30',
    headerBorder: 'border-yellow-700/50',
    banner: { emoji: '🟡', text: 'NUISANCE DETECTED — Phát hiện nhiễu', color: 'text-yellow-300', bg: 'bg-yellow-900/60 border-yellow-600/50' },
  },
  Fire: {
    bgClass: 'bg-gray-950',
    overlay: 'bg-red-500/8 animate-pulse',
    headerBg: 'bg-red-900/40',
    headerBorder: 'border-red-600/60',
    banner: { emoji: '🔴', text: 'FIRE DETECTED — Phát hiện cháy!', color: 'text-red-300', bg: 'bg-red-900/70 border-red-500/60' },
  },
}

/**
 * Root application component.
 *
 * Layout:
 *   ┌─────────────────────────────────────────┐
 *   │  Header (title + status)                │
 *   ├──────────────────────┬──────────────────┤
 *   │  Grafana charts      │  Device controls │
 *   │  (left 60%)          │  + Chat panel    │
 *   │                      │  (right 40%)     │
 *   └──────────────────────┴──────────────────┘
 */
export default function App() {
  /**
   * Message injected from DeviceStatus into ChatPanel
   * so control feedback appears inline in the chat thread.
   */
  const [injectedMessage, setInjectedMessage] = useState(null)

  /**
   * Current alarm phase from SensorCharts — drives the full-page
   * background visual (Background / Nuisance / Fire).
   */
  const [alarmPhase, setAlarmPhase] = useState('Background')

  const alarm = ALARM_STYLES[alarmPhase] || ALARM_STYLES.Background

  return (
    <div className={`flex flex-col h-screen ${alarm.bgClass} text-white overflow-hidden relative transition-colors duration-700`}>
      {/* ── Full-page alarm overlay (Nuisance / Fire glow) ──── */}
      {alarm.overlay && (
        <div className={`absolute inset-0 ${alarm.overlay} pointer-events-none z-0 transition-opacity duration-700`} />
      )}

      {/* ── Alarm banner ────────────────────────────────────── */}
      {alarm.banner && (
        <div className={`flex items-center justify-center gap-2 px-4 py-2 border-b ${alarm.banner.bg} backdrop-blur-sm z-10 shrink-0`}>
          <span className="text-lg">{alarm.banner.emoji}</span>
          <span className={`text-sm font-bold ${alarm.banner.color} tracking-wide`}>
            {alarm.banner.text}
          </span>
          <span className="text-lg">{alarm.banner.emoji}</span>
        </div>
      )}

      {/* ── Header ──────────────────────────────────────────── */}
      <header className={`flex items-center gap-3 px-6 py-3 border-b ${alarm.headerBorder} ${alarm.headerBg} backdrop-blur-sm shrink-0 z-10 transition-colors duration-700`}>
        <div className="flex items-center gap-2">
          <span className="text-xl">🔥</span>
          <div>
            <h1 className="text-white font-bold text-sm leading-none">Early Fire Alarm</h1>
            <p className="text-gray-500 text-[10px] mt-0.5">ESP32 · InfluxDB · Ollama</p>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-4">
          <StatusDot label="MQTT" />
          <StatusDot label="API" />
          <StatusDot label="Grafana" />
        </div>
      </header>

      {/* ── Main area ───────────────────────────────────────── */}
      <main className="flex flex-1 gap-4 p-4 overflow-hidden min-h-0 z-10">

        {/* Left: Sensor charts */}
        <section className="flex-[3] min-w-0 min-h-0">
          <SensorCharts onAlarmPhaseChange={setAlarmPhase} />
        </section>

        {/* Right: Devices + Chat */}
        <section className="flex-[2] flex flex-col gap-3 min-w-0 min-h-0">
          {/* Device control cards */}
          <div className="bg-gray-900/70 rounded-2xl border border-gray-800 p-4 shrink-0">
            <DeviceStatus onMessage={setInjectedMessage} />
          </div>

          {/* Chat panel */}
          <div className="bg-gray-900/70 rounded-2xl border border-gray-800 flex-1 overflow-hidden flex flex-col min-h-0">
            <ChatPanel injectMessage={injectedMessage} />
          </div>
        </section>
      </main>
    </div>
  )
}

/**
 * Simple animated status indicator dot for the header.
 *
 * @param {{ label: string }} props
 */
function StatusDot({ label }) {
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
      <span className="text-gray-500 text-xs">{label}</span>
    </div>
  )
}
