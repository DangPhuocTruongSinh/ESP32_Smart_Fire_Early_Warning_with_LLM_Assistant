import { useEffect, useState, useCallback } from 'react'
import { API_BASE_URL, DEVICES } from '../config'

const ACTION_LABELS = { on: 'Bật', off: 'Tắt' }

/**
 * Displays status and on/off toggle cards for each registered device.
 * Polls /devices/status every 5 s and sends commands via /control/direct.
 *
 * @param {{ onMessage: (text: string) => void }} props
 *   onMessage — callback to inject a notification into the chat panel.
 */
export default function DeviceStatus({ onMessage }) {
  const [devices, setDevices] = useState([])
  const [loading, setLoading] = useState({})
  const [error, setError] = useState(null)

  /** Fetch current device states from the API. */
  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/devices/status`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setDevices(data.devices)
      setError(null)
    } catch (err) {
      setError('Không thể kết nối API (' + err.message + ')')
    }
  }, [])

  useEffect(() => {
    fetchStatus()
    const interval = setInterval(fetchStatus, 5000)
    return () => clearInterval(interval)
  }, [fetchStatus])

  /**
   * Send a control command for one device.
   *
   * @param {string} deviceId
   * @param {'on'|'off'} action
   */
  async function sendCommand(deviceId, action) {
    const device = DEVICES.find((d) => d.id === deviceId)

    if (device?.requiresConfirmation && action === 'on') {
      const confirmed = window.confirm(
        `⚠️ Bạn có chắc muốn BẬT ${device.name}?`
      )
      if (!confirmed) return
    }

    setLoading((prev) => ({ ...prev, [deviceId]: action }))

    try {
      const res = await fetch(`${API_BASE_URL}/control/direct`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ commands: [{ device_id: deviceId, action }] }),
      })
      const data = await res.json()
      const resultMsg = Object.values(data.results ?? {})[0] ?? ''
      onMessage?.(`[Điều khiển thiết bị] ${resultMsg}`)
      await fetchStatus()
    } catch (err) {
      onMessage?.(`[Lỗi] Không gửi được lệnh: ${err.message}`)
    } finally {
      setLoading((prev) => ({ ...prev, [deviceId]: null }))
    }
  }

  /** Merge API state with static device metadata from config. */
  const mergedDevices = DEVICES.map((cfg) => {
    const apiDevice = devices.find((d) => d.id === cfg.id)
    return { ...cfg, state: apiDevice?.state ?? 'unknown' }
  })

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-white/90 text-sm font-semibold">Thiết bị</h2>
        {error && (
          <span className="text-red-400 text-xs">{error}</span>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2">
        {mergedDevices.map((device) => {
          const isOn = device.state === 'on'
          const isOff = device.state === 'off'
          const isUnknown = !isOn && !isOff
          const busyOn  = loading[device.id] === 'on'
          const busyOff = loading[device.id] === 'off'

          return (
            <div
              key={device.id}
              className={`
                flex flex-col items-center gap-2 rounded-xl border px-3 py-3 transition-colors
                ${isOn
                  ? 'bg-emerald-500/15 border-emerald-500/40'
                  : 'bg-gray-800/60 border-gray-700/50'}
              `}
            >
              {/* Icon + name */}
              <span className="text-2xl">{device.icon}</span>
              <span className="text-white/80 text-xs font-medium text-center leading-tight">
                {device.name}
              </span>

              {/* State badge */}
              <span
                className={`text-[10px] font-semibold px-2 py-0.5 rounded-full
                  ${isOn      ? 'bg-emerald-500/30 text-emerald-300'
                  : isOff     ? 'bg-gray-600/50 text-gray-400'
                              : 'bg-gray-700/50 text-gray-500'}`}
              >
                {isOn ? 'BẬT' : isOff ? 'TẮT' : '—'}
              </span>

              {/* Control buttons */}
              <div className="flex gap-1 w-full">
                <button
                  onClick={() => sendCommand(device.id, 'on')}
                  disabled={isOn || busyOn}
                  className={`
                    flex-1 text-[10px] font-medium rounded-lg py-1 transition-colors
                    ${isOn || busyOn
                      ? 'bg-emerald-700/30 text-emerald-600 cursor-not-allowed'
                      : 'bg-emerald-600/80 hover:bg-emerald-500 text-white cursor-pointer'}
                  `}
                >
                  {busyOn ? '…' : ACTION_LABELS.on}
                </button>
                <button
                  onClick={() => sendCommand(device.id, 'off')}
                  disabled={isOff || busyOff || isUnknown}
                  className={`
                    flex-1 text-[10px] font-medium rounded-lg py-1 transition-colors
                    ${isOff || busyOff || isUnknown
                      ? 'bg-gray-700/30 text-gray-600 cursor-not-allowed'
                      : 'bg-gray-600/80 hover:bg-gray-500 text-white cursor-pointer'}
                  `}
                >
                  {busyOff ? '…' : ACTION_LABELS.off}
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
