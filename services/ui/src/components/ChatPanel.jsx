import { useState, useRef, useEffect, useCallback } from 'react'
import { API_BASE_URL } from '../config'

/** @typedef {{ role: 'user'|'assistant'|'system', text: string, ts: number }} Message */

const SUGGESTED_PROMPTS = [
  'Đọc dữ liệu cảm biến hiện tại',
  'Nhiệt độ có nguy hiểm không?',
  'Bật quạt giúp tôi',
  'Tắt tất cả thiết bị',
]

/**
 * Formats a Date object as HH:MM.
 *
 * @param {number} ts - Unix timestamp in ms
 * @returns {string}
 */
function formatTime(ts) {
  return new Date(ts).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
}

/**
 * Full-featured chat panel connected to the /analyze LLM endpoint.
 *
 * Features:
 * - Persistent thread_id so the LLM agent remembers conversation context
 * - Streaming-style typing indicator while waiting for response
 * - Suggested prompts for quick actions
 * - External messages injected via the `injectMessage` prop (e.g. from DeviceStatus)
 *
 * @param {{ injectMessage: string|null }} props
 *   injectMessage — when set, this text is appended as a system notification.
 */
export default function ChatPanel({ injectMessage }) {
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      text: 'Xin chào! Tôi là trợ lý AI cho hệ thống cảnh báo cháy. Bạn có thể hỏi tôi về dữ liệu cảm biến hoặc yêu cầu điều khiển thiết bị.',
      ts: Date.now(),
    },
  ])
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  /** A fixed thread ID keeps conversation memory across messages. */
  const threadId = useRef('dashboard-' + Date.now())
  const bottomRef = useRef(null)
  const inputRef = useRef(null)

  /** Auto-scroll to the latest message whenever messages change. */
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  /** Inject device-control notifications from the parent component. */
  useEffect(() => {
    if (!injectMessage) return
    setMessages((prev) => [
      ...prev,
      { role: 'system', text: injectMessage, ts: Date.now() },
    ])
  }, [injectMessage])

  /**
   * Send the user's question to the /analyze endpoint and append
   * both the user message and the assistant response to the chat.
   *
   * @param {string} question
   */
  const sendMessage = useCallback(async (question) => {
    const trimmed = question.trim()
    if (!trimmed || isLoading) return

    setMessages((prev) => [
      ...prev,
      { role: 'user', text: trimmed, ts: Date.now() },
    ])
    setInput('')
    setIsLoading(true)

    try {
      const res = await fetch(`${API_BASE_URL}/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: trimmed, thread_id: threadId.current }),
      })

      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()

      const text = data.response ?? data.error ?? 'Không có phản hồi.'
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', text, ts: Date.now() },
      ])
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', text: `⚠️ Lỗi kết nối API: ${err.message}`, ts: Date.now() },
      ])
    } finally {
      setIsLoading(false)
      inputRef.current?.focus()
    }
  }, [isLoading])

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage(input)
    }
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-700/50">
        <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
        <span className="text-white/90 text-sm font-semibold">Trợ lý AI</span>
        <span className="ml-auto text-gray-500 text-xs">Ollama • /analyze</span>
      </div>

      {/* Message list */}
      <div className="flex-1 overflow-y-auto chat-scroll px-4 py-3 space-y-3 min-h-0">
        {messages.map((msg, i) => (
          <MessageBubble key={i} msg={msg} />
        ))}

        {/* Typing indicator */}
        {isLoading && (
          <div className="flex items-end gap-2">
            <div className="w-6 h-6 rounded-full bg-indigo-600 flex items-center justify-center text-xs shrink-0">
              AI
            </div>
            <div className="bg-gray-800 rounded-2xl rounded-bl-sm px-4 py-2.5 flex gap-1 items-center">
              {[0, 1, 2].map((n) => (
                <span
                  key={n}
                  className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
                  style={{ animationDelay: `${n * 150}ms` }}
                />
              ))}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Suggested prompts (only visible when chat is empty or last message is from assistant) */}
      {!isLoading && messages[messages.length - 1]?.role !== 'user' && (
        <div className="px-4 py-2 flex flex-wrap gap-1.5">
          {SUGGESTED_PROMPTS.map((prompt) => (
            <button
              key={prompt}
              onClick={() => sendMessage(prompt)}
              className="text-[11px] bg-gray-800/80 hover:bg-gray-700 border border-gray-700 text-gray-300 hover:text-white px-2.5 py-1 rounded-full transition-colors cursor-pointer"
            >
              {prompt}
            </button>
          ))}
        </div>
      )}

      {/* Input area */}
      <div className="px-4 pb-4 pt-2">
        <div className="flex items-end gap-2 bg-gray-800/80 border border-gray-700 rounded-xl px-3 py-2">
          <textarea
            ref={inputRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Nhập câu hỏi hoặc lệnh…"
            className="flex-1 bg-transparent text-white text-sm placeholder-gray-500 resize-none outline-none max-h-28 leading-relaxed"
          />
          <button
            onClick={() => sendMessage(input)}
            disabled={!input.trim() || isLoading}
            className={`
              shrink-0 w-8 h-8 rounded-lg flex items-center justify-center transition-colors cursor-pointer
              ${input.trim() && !isLoading
                ? 'bg-indigo-600 hover:bg-indigo-500 text-white'
                : 'bg-gray-700 text-gray-600 cursor-not-allowed'}
            `}
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
              <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
            </svg>
          </button>
        </div>
        <p className="text-gray-600 text-[10px] mt-1.5 text-center">
          Enter để gửi • Shift+Enter xuống dòng
        </p>
      </div>
    </div>
  )
}

/**
 * Renders a single chat message bubble.
 *
 * @param {{ msg: Message }} props
 */
function MessageBubble({ msg }) {
  if (msg.role === 'system') {
    return (
      <div className="flex justify-center">
        <span className="text-[10px] text-gray-500 bg-gray-800/60 border border-gray-700/50 px-3 py-1 rounded-full">
          {msg.text}
        </span>
      </div>
    )
  }

  const isUser = msg.role === 'user'

  return (
    <div className={`flex items-end gap-2 ${isUser ? 'flex-row-reverse' : ''}`}>
      {/* Avatar */}
      <div
        className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0
          ${isUser ? 'bg-indigo-500' : 'bg-gray-700 text-gray-300'}`}
      >
        {isUser ? 'U' : 'AI'}
      </div>

      {/* Bubble */}
      <div
        className={`
          max-w-[80%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap
          ${isUser
            ? 'bg-indigo-600 text-white rounded-br-sm'
            : 'bg-gray-800 text-gray-200 rounded-bl-sm'}
        `}
      >
        {msg.text}
        <span className={`block text-[10px] mt-1 ${isUser ? 'text-indigo-300' : 'text-gray-600'}`}>
          {formatTime(msg.ts)}
        </span>
      </div>
    </div>
  )
}
