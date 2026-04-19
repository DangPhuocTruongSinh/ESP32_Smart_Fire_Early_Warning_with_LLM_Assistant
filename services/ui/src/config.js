/** Central configuration for the Fire Alarm Dashboard UI. */

/** FastAPI backend base URL */
export const API_BASE_URL = "http://localhost:8000";

/** Available devices (must match ALLOWED_DEVICES in chat_api.py) */
export const DEVICES = [
  { id: "fan",   name: "Quạt",     icon: "💨" },
  { id: "ac",    name: "Máy lạnh", icon: "❄️" },
  { id: "stove", name: "Bếp điện", icon: "🔥", requiresConfirmation: true },
];
