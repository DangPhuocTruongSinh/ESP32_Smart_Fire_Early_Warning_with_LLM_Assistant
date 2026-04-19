import { useEffect, useState, useCallback } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { API_BASE_URL } from "../config";

/** @typedef {{ time: string, temperature: number|null, humidity: number|null, gas: number|null }} DataPoint */

const CHART_CONFIGS = [
  {
    key: "temperature",
    label: "Nhiệt độ",
    unit: "°C",
    icon: "🌡️",
    color: "#f97316",
    gradientId: "tempGrad",
    gradientColor: "#f97316",
    domain: [0, 60],
    // dangerThreshold: 45,
    dangerThreshold: null,
  },
  {
    key: "humidity",
    label: "Độ ẩm",
    unit: "%",
    icon: "💧",
    color: "#38bdf8",
    gradientId: "humGrad",
    gradientColor: "#38bdf8",
    domain: [0, 100],
    dangerThreshold: null,
  },
  {
    key: "gas",
    label: "Khí Gas",
    unit: "",
    icon: "💨",
    color: "#fbbf24",
    gradientId: "gasGrad",
    gradientColor: "#fbbf24",
    domain: [0, 4095],
    // dangerThreshold: 2000,
    dangerThreshold: null,
  },
];

/**
 * Formats an ISO timestamp string to "HH:mm" for the X-axis tick label.
 *
 * @param {string} isoStr
 * @returns {string}
 */
function toHHMM(isoStr) {
  return new Date(isoStr).toLocaleTimeString("vi-VN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Custom Recharts tooltip that shows sensor reading + unit.
 */
function SensorTooltip({ active, payload, label, unit }) {
  if (!active || !payload?.length) return null;
  const value = payload[0]?.value;
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-xs shadow-xl">
      <p className="text-gray-400 mb-1">{label}</p>
      <p className="text-white font-semibold">
        {value != null ? `${value}${unit}` : "N/A"}
      </p>
    </div>
  );
}

/**
 * Single sensor chart card using Recharts AreaChart.
 *
 * @param {{ config: object, data: DataPoint[], loading: boolean }} props
 */
function SensorCard({ config, data, loading }) {
  const latest = data.length > 0 ? data[data.length - 1][config.key] : null;
  const isDanger =
    config.dangerThreshold != null &&
    latest != null &&
    latest > config.dangerThreshold;

  return (
    <div
      className={`
      flex flex-col rounded-xl border overflow-hidden bg-gray-900/80 transition-colors
      ${isDanger ? "border-red-500/60" : "border-gray-700/50"}
    `}
    >
      {/* Card header */}
      <div
        className={`
        flex items-center justify-between px-4 py-2.5 border-b
        ${isDanger ? "border-red-500/30 bg-red-500/10" : "border-gray-700/40 bg-gray-800/40"}
      `}
      >
        <div className="flex items-center gap-2">
          <span>{config.icon}</span>
          <span className="text-white/90 text-xs font-semibold">
            {config.label}
          </span>
          {isDanger && (
            <span className="text-red-400 text-[10px] font-bold animate-pulse">
              ⚠ CẢNH BÁO
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {loading && (
            <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
          )}
          <span
            className="text-lg font-bold tabular-nums"
            style={{ color: isDanger ? "#f87171" : config.color }}
          >
            {latest != null ? `${latest}${config.unit}` : "—"}
          </span>
        </div>
      </div>

      {/* Chart */}
      <div className="flex-1 px-1 pt-2 pb-1 min-h-0">
        {data.length === 0 && !loading ? (
          <div className="flex items-center justify-center h-full text-gray-600 text-xs">
            Chưa có dữ liệu
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={data}
              margin={{ top: 4, right: 8, left: -20, bottom: 0 }}
            >
              <defs>
                <linearGradient
                  id={config.gradientId}
                  x1="0"
                  y1="0"
                  x2="0"
                  y2="1"
                >
                  <stop
                    offset="5%"
                    stopColor={config.gradientColor}
                    stopOpacity={0.3}
                  />
                  <stop
                    offset="95%"
                    stopColor={config.gradientColor}
                    stopOpacity={0.02}
                  />
                </linearGradient>
              </defs>
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="#1f2937"
                vertical={false}
              />
              <XAxis
                dataKey="time"
                tickFormatter={toHHMM}
                tick={{ fill: "#6b7280", fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                domain={config.domain}
                tick={{ fill: "#6b7280", fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                width={40}
              />
              <Tooltip
                content={(props) => (
                  <SensorTooltip {...props} unit={config.unit} />
                )}
                labelFormatter={toHHMM}
              />
              {/* Danger threshold reference line */}
              {config.dangerThreshold != null && (
                <Area
                  type="monotone"
                  dataKey={() => config.dangerThreshold}
                  stroke="#ef4444"
                  strokeWidth={1}
                  strokeDasharray="4 4"
                  fill="none"
                  dot={false}
                  activeDot={false}
                  legendType="none"
                />
              )}
              <Area
                type="monotone"
                dataKey={config.key}
                stroke={config.color}
                strokeWidth={2}
                fill={`url(#${config.gradientId})`}
                dot={false}
                activeDot={{ r: 4, fill: config.color, strokeWidth: 0 }}
                connectNulls
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

/**
 * Fetches sensor history from /sensor/history and renders 3 area charts
 * (temperature, humidity, gas). Auto-refreshes every 10 seconds.
 */
export default function SensorCharts() {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [fetchError, setFetchError] = useState(null);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/sensor/history?minutes=30`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setData(json.data ?? []);
      setLastUpdated(new Date());
      setFetchError(null);
    } catch (err) {
      setFetchError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchHistory();
    const interval = setInterval(fetchHistory, 10_000);
    return () => clearInterval(interval);
  }, [fetchHistory]);

  return (
    <div className="flex flex-col h-full gap-3">
      {/* Section header */}
      <div className="flex items-center justify-between px-1 shrink-0">
        <h2 className="text-white/80 text-sm font-semibold">
          Dữ liệu cảm biến · 30 phút qua
        </h2>
        <div className="flex items-center gap-2">
          {fetchError && (
            <span className="text-red-400 text-xs">⚠ {fetchError}</span>
          )}
          {lastUpdated && !fetchError && (
            <span className="text-gray-600 text-[10px]">
              Cập nhật{" "}
              {lastUpdated.toLocaleTimeString("vi-VN", {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              })}
            </span>
          )}
          <button
            onClick={fetchHistory}
            className="text-gray-500 hover:text-white transition-colors text-xs cursor-pointer"
            title="Làm mới"
          >
            ↺
          </button>
        </div>
      </div>

      {/* 3 charts stacked vertically */}
      <div className="grid grid-rows-3 flex-1 gap-3 min-h-0">
        {CHART_CONFIGS.map((config) => (
          <SensorCard
            key={config.key}
            config={config}
            data={data}
            loading={loading}
          />
        ))}
      </div>
    </div>
  );
}
