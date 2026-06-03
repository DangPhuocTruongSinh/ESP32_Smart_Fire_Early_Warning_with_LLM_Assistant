import { useEffect, useState, useCallback, useRef } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceArea,
  ReferenceLine,
} from "recharts";
import { API_BASE_URL } from "../config";

/** @typedef {{ time: string, temperature: number|null, humidity: number|null, gas: number|null, ground_truth?: string, ml_class?: string }} DataPoint */

// ── Label color palette ──────────────────────────────────────────────────────
const LABEL_STYLES = {
  Background: { bg: "rgba(34,197,94,0.07)",  border: "#22c55e", text: "#4ade80", emoji: "🟢" },
  Nuisance:   { bg: "rgba(234,179,8,0.10)",  border: "#eab308", text: "#facc15", emoji: "🟡" },
  Fire:       { bg: "rgba(239,68,68,0.12)",  border: "#ef4444", text: "#f87171", emoji: "🔴" },
};

const CHART_CONFIGS = [
  {
    key: "temperature",
    label: "Nhiệt độ",
    unit: "°C",
    icon: "🌡️",
    color: "#f97316",
    gradientId: "tempGrad",
    domain: [0, 60],
    dangerThreshold: null,
  },
  {
    key: "humidity",
    label: "Độ ẩm",
    unit: "%",
    icon: "💧",
    color: "#38bdf8",
    gradientId: "humGrad",
    domain: [0, 100],
    dangerThreshold: null,
  },
  {
    key: "gas",
    label: "Khí Gas / PM_Total",
    unit: "",
    icon: "💨",
    color: "#fbbf24",
    gradientId: "gasGrad",
    domain: [0, "auto"],
    dangerThreshold: null,
  },
];

/** Formats ISO timestamp → "HH:mm" */
function toHHMM(isoStr) {
  return new Date(isoStr).toLocaleTimeString("vi-VN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Detect label-change transition points in the data array.
 * Returns array of { time, label } at each transition boundary.
 */
function detectTransitions(data) {
  const transitions = [];
  for (let i = 1; i < data.length; i++) {
    if (
      data[i].ground_truth &&
      data[i - 1].ground_truth &&
      data[i].ground_truth !== data[i - 1].ground_truth
    ) {
      transitions.push({ time: data[i].time, label: data[i].ground_truth });
    }
  }
  return transitions;
}

/**
 * Build contiguous label segments for ReferenceArea coloring.
 * Returns array of { startTime, endTime, label }.
 */
function buildLabelSegments(data) {
  if (!data.length) return [];
  // Need at least one data point with ground_truth to show segments
  if (!data.some(d => d.ground_truth)) return [];

  const segments = [];
  // Use "Background" as fallback for data points without ground_truth
  let segStart = data[0].time;
  let segLabel = data[0].ground_truth || "Background";

  for (let i = 1; i < data.length; i++) {
    const cur = data[i].ground_truth || "Background";
    if (cur !== segLabel) {
      segments.push({ startTime: segStart, endTime: data[i - 1].time, label: segLabel });
      segStart = data[i].time;
      segLabel = cur;
    }
  }
  // Push final segment
  segments.push({ startTime: segStart, endTime: data[data.length - 1].time, label: segLabel });
  return segments;
}

/** Custom Recharts tooltip */
function SensorTooltip({ active, payload, label, unit }) {
  if (!active || !payload?.length) return null;
  const pt = payload[0]?.payload;
  const value = payload[0]?.value;
  const gt = pt?.ground_truth;
  const ml = pt?.ml_class;
  const style = gt ? LABEL_STYLES[gt] : null;

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-xs shadow-xl min-w-[120px]">
      <p className="text-gray-400 mb-1">{toHHMM(label)}</p>
      <p className="text-white font-semibold mb-1">
        {value != null ? `${value}${unit}` : "N/A"}
      </p>
      {gt && (
        <p style={{ color: style?.text }} className="text-[10px]">
          {style?.emoji} GT: {gt}
        </p>
      )}
      {ml && ml !== "warming_up" && (
        <p className="text-[10px] text-gray-400">
          🧠 ML: {ml}
        </p>
      )}
    </div>
  );
}

/**
 * Single sensor chart card — supports ground_truth color-coded background
 * and transition ReferenceLine markers when demo data is present.
 */
function SensorCard({ config, data, loading }) {
  const latest    = data.length > 0 ? data[data.length - 1][config.key] : null;
  const currentGT = data.length > 0 ? data[data.length - 1]?.ground_truth : null;
  const currentML = data.length > 0 ? data[data.length - 1]?.ml_class : null;
  const isDemoMode = data.some(d => d.ground_truth);

  const isDanger =
    config.dangerThreshold != null &&
    latest != null &&
    latest > config.dangerThreshold;

  // Build label segments and transitions for demo coloring
  const segments    = isDemoMode ? buildLabelSegments(data) : [];
  const transitions = isDemoMode ? detectTransitions(data) : [];

  const gtStyle = currentGT ? LABEL_STYLES[currentGT] : null;
  const headerBorderClass = isDanger
    ? "border-red-500/30 bg-red-500/10"
    : "border-gray-700/40";

  return (
    <div
      className={`flex flex-col rounded-xl border overflow-hidden bg-gray-900/80 transition-colors
        ${isDanger ? "border-red-500/60" : "border-gray-700/50"}`}
      style={gtStyle && !isDanger ? { borderColor: `${gtStyle.border}40` } : {}}
    >
      {/* Card header */}
      <div
        className={`flex items-center justify-between px-4 py-2.5 border-b ${headerBorderClass}`}
        style={gtStyle && !isDanger ? { backgroundColor: gtStyle.bg } : {}}
      >
        <div className="flex items-center gap-2">
          <span>{config.icon}</span>
          <span className="text-white/90 text-xs font-semibold">{config.label}</span>
          {isDanger && (
            <span className="text-red-400 text-[10px] font-bold animate-pulse">⚠ CẢNH BÁO</span>
          )}
          {/* Demo mode: current phase badge */}
          {isDemoMode && currentGT && !isDanger && (
            <span
              className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full"
              style={{ color: gtStyle.text, backgroundColor: `${gtStyle.border}20` }}
            >
              {gtStyle.emoji} {currentGT}
            </span>
          )}
          {/* ML class badge — show only when different from GT */}
          {isDemoMode && currentML && currentML !== "warming_up" && currentML !== currentGT && (
            <span className="text-[10px] text-gray-400">
              🧠 {currentML}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {loading && <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />}
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
                <linearGradient id={config.gradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor={config.color} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={config.color} stopOpacity={0.02} />
                </linearGradient>
              </defs>

              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />

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
                content={(props) => <SensorTooltip {...props} unit={config.unit} />}
                labelFormatter={toHHMM}
              />

              {/* ── Demo mode: colored background segments by ground_truth ── */}
              {segments.map((seg, idx) => {
                const s = LABEL_STYLES[seg.label];
                if (!s) return null;
                return (
                  <ReferenceArea
                    key={idx}
                    x1={seg.startTime}
                    x2={seg.endTime}
                    fill={s.bg}
                    fillOpacity={1}
                    stroke="none"
                  />
                );
              })}

              {/* ── Demo mode: vertical transition markers ── */}
              {transitions.map((tr, idx) => {
                const s = LABEL_STYLES[tr.label];
                return (
                  <ReferenceLine
                    key={`tr-${idx}`}
                    x={tr.time}
                    stroke={s?.border ?? "#6b7280"}
                    strokeWidth={1.5}
                    strokeDasharray="4 3"
                    label={{
                      value: `→ ${tr.label}`,
                      position: "insideTopLeft",
                      fill: s?.text ?? "#9ca3af",
                      fontSize: 9,
                      fontWeight: 600,
                    }}
                  />
                );
              })}

              {/* Main data area */}
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
 * (temperature, humidity, gas/PM_Total). Auto-refreshes every 10 seconds.
 * In demo mode, charts show color-coded backgrounds by ground_truth label.
 */
export default function SensorCharts({ onAlarmPhaseChange }) {
  const [data, setData]               = useState([]);
  const [loading, setLoading]         = useState(true);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [fetchError, setFetchError]   = useState(null);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/sensor/history?minutes=10`);
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
    const interval = setInterval(fetchHistory, 5_000);
    return () => clearInterval(interval);
  }, [fetchHistory]);

  // Detect if we're in demo mode (any data point has ground_truth)
  const isDemoMode   = data.some(d => d.ground_truth);
  const currentPhase = data.length > 0 ? data[data.length - 1]?.ground_truth : null;

  // Alarm phase driven by ml_class (ESP32 CNN detection), NOT ground_truth.
  // This ensures the UI reacts to what the model actually detects,
  // even if it differs from the dataset label (ground_truth).
  const latestML = data.length > 0 ? data[data.length - 1]?.ml_class : null;
  const alarmPhase = (latestML && latestML !== "warming_up") ? latestML : "Background";

  // Notify parent (App.jsx) whenever the alarm phase changes
  const prevPhaseRef = useRef(null);
  useEffect(() => {
    if (alarmPhase !== prevPhaseRef.current) {
      prevPhaseRef.current = alarmPhase;
      onAlarmPhaseChange?.(alarmPhase);
    }
  }, [alarmPhase, onAlarmPhaseChange]);

  return (
    <div className="flex flex-col h-full gap-3">
      {/* Section header */}
      <div className="flex items-center justify-between px-1 shrink-0">
        <div className="flex items-center gap-2">
          <h2 className="text-white/80 text-sm font-semibold">
            Dữ liệu cảm biến · 10 phút qua
          </h2>
          {/* Demo mode indicator */}
          {isDemoMode && (
            <span className="text-[10px] bg-purple-900/50 text-purple-300 border border-purple-700/50 px-1.5 py-0.5 rounded-full font-semibold">
              🎬 DEMO
            </span>
          )}
        </div>
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

      {/* Demo mode: phase legend */}
      {isDemoMode && (
        <div className="flex items-center gap-3 px-1 shrink-0">
          {Object.entries(LABEL_STYLES).map(([label, s]) => (
            <div key={label} className="flex items-center gap-1">
              <div
                className="w-3 h-3 rounded-sm"
                style={{ backgroundColor: s.bg, border: `1px solid ${s.border}` }}
              />
              <span className="text-[10px] text-gray-400">{label}</span>
            </div>
          ))}
          {currentPhase && (
            <span
              className="ml-auto text-[10px] font-semibold"
              style={{ color: LABEL_STYLES[currentPhase]?.text }}
            >
              Phase hiện tại: {LABEL_STYLES[currentPhase]?.emoji} {currentPhase}
            </span>
          )}
        </div>
      )}

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
