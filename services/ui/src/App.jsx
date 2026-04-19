import { useState } from 'react'
import SensorCharts from './components/SensorCharts'
import ChatPanel from './components/ChatPanel'
import DeviceStatus from './components/DeviceStatus'

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

  return (
    <div className="flex flex-col h-screen bg-gray-950 text-white overflow-hidden">
      {/* ── Header ──────────────────────────────────────────── */}
      <header className="flex items-center gap-3 px-6 py-3 border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm shrink-0">
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
      <main className="flex flex-1 gap-4 p-4 overflow-hidden min-h-0">

        {/* Left: Sensor charts */}
        <section className="flex-[3] min-w-0 min-h-0">
          <SensorCharts />
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
