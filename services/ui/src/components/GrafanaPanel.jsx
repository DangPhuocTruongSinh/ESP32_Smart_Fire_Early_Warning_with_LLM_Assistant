import {
  GRAFANA_BASE_URL,
  GRAFANA_DASHBOARD_UID,
  GRAFANA_DASHBOARD_SLUG,
  GRAFANA_ORG_ID,
  GRAFANA_PUBLIC_TOKEN,
  GRAFANA_TIME_RANGE,
  PANEL_IDS,
} from '../config'

/**
 * Builds the iframe src URL for a single panel using Grafana's
 * public-dashboard embed format.
 *
 * @param {string} panelKey - One of 'temperature' | 'humidity' | 'gas'
 * @returns {string} Full embed URL
 */
function buildPanelUrl(panelKey) {
  const panelId = PANEL_IDS[panelKey]
  const base = `${GRAFANA_BASE_URL}/public-dashboards/${GRAFANA_PUBLIC_TOKEN}`
  return panelId
    ? `${base}?panelId=${panelId}&${GRAFANA_TIME_RANGE}`
    : `${base}?${GRAFANA_TIME_RANGE}`
}

/**
 * Builds the iframe src URL for the full authenticated dashboard
 * in kiosk (no UI chrome) mode.
 *
 * @returns {string} Full embed URL
 */
function buildDashboardUrl() {
  return (
    `${GRAFANA_BASE_URL}/d/${GRAFANA_DASHBOARD_UID}/${GRAFANA_DASHBOARD_SLUG}` +
    `?orgId=${GRAFANA_ORG_ID}&${GRAFANA_TIME_RANGE}&kiosk=tv`
  )
}

const PANEL_META = {
  temperature: { label: 'Nhiệt độ', unit: '°C', color: 'from-orange-500/20 to-red-500/10', border: 'border-orange-500/30', icon: '🌡️' },
  humidity:    { label: 'Độ ẩm',    unit: '%',   color: 'from-blue-500/20 to-cyan-500/10',   border: 'border-blue-500/30',   icon: '💧' },
  gas:         { label: 'Khí Gas',  unit: 'ppm', color: 'from-yellow-500/20 to-amber-500/10', border: 'border-yellow-500/30', icon: '💨' },
}

/**
 * Renders a single Grafana panel as an iframe, or a setup notice
 * when the public token is not yet configured.
 *
 * @param {{ panelKey: 'temperature'|'humidity'|'gas' }} props
 */
function SinglePanel({ panelKey }) {
  const meta = PANEL_META[panelKey]

  if (!GRAFANA_PUBLIC_TOKEN) {
    return (
      <div className={`flex flex-col items-center justify-center h-full bg-gradient-to-br ${meta.color} rounded-xl border ${meta.border} p-4`}>
        <span className="text-3xl mb-2">{meta.icon}</span>
        <p className="text-white/80 font-medium text-sm">{meta.label}</p>
        <p className="text-white/40 text-xs mt-1 text-center">
          Cần Public Dashboard token
        </p>
      </div>
    )
  }

  return (
    <div className={`flex flex-col h-full rounded-xl border ${meta.border} overflow-hidden bg-gray-900`}>
      <div className={`flex items-center gap-2 px-3 py-2 bg-gradient-to-r ${meta.color} border-b ${meta.border}`}>
        <span>{meta.icon}</span>
        <span className="text-white text-xs font-semibold">{meta.label}</span>
      </div>
      <iframe
        src={buildPanelUrl(panelKey)}
        className="flex-1 w-full border-0"
        title={meta.label}
        loading="lazy"
      />
    </div>
  )
}

/**
 * Main Grafana section.
 *
 * - If GRAFANA_PUBLIC_TOKEN is set: renders 3 individual panels.
 * - Otherwise: renders a setup banner + the full dashboard in one iframe.
 */
export default function GrafanaPanel() {
  const hasToken = Boolean(GRAFANA_PUBLIC_TOKEN)

  return (
    <div className="flex flex-col h-full gap-3">
      {/* Setup banner shown when public token is not configured */}
      {!hasToken && (
        <div className="flex items-start gap-3 bg-amber-500/10 border border-amber-500/30 rounded-xl px-4 py-3 text-xs text-amber-300">
          <span className="mt-0.5 shrink-0">⚠️</span>
          <span>
            Để embed từng panel riêng lẻ, hãy bật{' '}
            <strong>Public Dashboard</strong> trong Grafana Cloud, sau đó điền{' '}
            <code className="bg-black/30 px-1 rounded">GRAFANA_PUBLIC_TOKEN</code>{' '}
            và <code className="bg-black/30 px-1 rounded">PANEL_IDS</code> trong{' '}
            <code className="bg-black/30 px-1 rounded">src/config.js</code>.
            <br />
            Hiện tại đang hiển thị toàn bộ dashboard (yêu cầu đăng nhập Grafana).
          </span>
        </div>
      )}

      {hasToken ? (
        /* Individual panels layout */
        <div className="grid grid-rows-3 flex-1 gap-3 min-h-0">
          {(['temperature', 'humidity', 'gas']).map((key) => (
            <SinglePanel key={key} panelKey={key} />
          ))}
        </div>
      ) : (
        /* Full dashboard fallback */
        <div className="flex-1 rounded-xl border border-gray-700 overflow-hidden bg-gray-900 min-h-0">
          <iframe
            src={buildDashboardUrl()}
            className="w-full h-full border-0"
            title="Grafana Dashboard"
            loading="lazy"
          />
        </div>
      )}
    </div>
  )
}
