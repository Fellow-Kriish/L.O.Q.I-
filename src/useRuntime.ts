import { useEffect, useRef, useState } from 'react'

export type VoiceState = 'idle' | 'listening' | 'processing' | 'speaking' | 'paused' | 'unavailable'
type Confirmation = { id: string; title: string; detail: string; expiresAt: number }
type Feedback = { id: string; text: string; kind: string; expiresAt: number }
type Snapshot = { state: VoiceState; online: boolean; microphoneReady: boolean; route: 'local' | 'cloud' | null; audioLevel: number; request: string; confirmation: Confirmation | null; runtimeError: string; microphoneName: string; errorCode: string; cloudError: string; feedback: Feedback | null; soundCues: boolean; soundCuesAvailable: boolean; showRequest: boolean; shortcuts: { toggle: string; open: string }; shortcutError: string }
const initial: Snapshot = { state: 'idle', online: false, microphoneReady: false, route: null, audioLevel: 0, request: '', confirmation: null, runtimeError: '', microphoneName: '', errorCode: '', cloudError: '', feedback: null, soundCues: false, soundCuesAvailable: false, showRequest: true, shortcuts: { toggle: 'Ctrl+Alt+Space', open: 'Ctrl+Alt+L' }, shortcutError: '' }
const validStates: VoiceState[] = ['idle', 'listening', 'processing', 'speaking', 'paused', 'unavailable']

export default function useRuntime() {
  const parameters = new URLSearchParams(window.location.hash.slice(1))
  const preview = new URLSearchParams(window.location.search).get('preview') === '1'
  const url = preview ? undefined : parameters.get('runtime') || import.meta.env.VITE_LOQI_RUNTIME_URL || 'ws://127.0.0.1:8765/'
  const [snapshot, setSnapshot] = useState<Snapshot>(initial)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState('')
  const socketRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!url) return
    let disposed = false
    let retry: ReturnType<typeof setTimeout>
    let socket: WebSocket | null = null
    const connect = () => {
      try {
        socket = new WebSocket(url)
        socketRef.current = socket
        socket.onmessage = event => {
          try {
            const data = JSON.parse(event.data)
            if (data.type !== 'status' || !validStates.includes(data.state) || typeof data.microphoneReady !== 'boolean' || typeof data.online !== 'boolean') return
            const active = data.state === 'processing' || data.state === 'speaking'
            const approval = data.confirmation
            const confirmation = approval && typeof approval.id === 'string' && typeof approval.title === 'string' && typeof approval.detail === 'string' && typeof approval.expiresAt === 'number' && Number.isFinite(approval.expiresAt) && approval.expiresAt > Date.now() ? { id: approval.id, title: approval.title.slice(0, 150), detail: approval.detail.slice(0, 300), expiresAt: approval.expiresAt } : null
            const notice = data.feedback
            const feedback = notice && typeof notice.id === 'string' && typeof notice.text === 'string' && typeof notice.expiresAt === 'number' && Number.isFinite(notice.expiresAt) && notice.expiresAt > Date.now() ? { id: notice.id, text: notice.text.slice(0, 180), kind: typeof notice.kind === 'string' ? notice.kind : 'success', expiresAt: notice.expiresAt } : null
            const text = (value: unknown) => typeof value === 'string' ? value.slice(0, 200) : ''
            setSnapshot({ state: data.state, online: data.online, microphoneReady: data.microphoneReady, route: active && (data.route === 'local' || data.route === 'cloud') ? data.route : null, audioLevel: typeof data.audioLevel === 'number' && Number.isFinite(data.audioLevel) ? Math.max(0, Math.min(1, data.audioLevel)) : 0, request: active && typeof data.request === 'string' ? data.request.slice(0, 180) : '', confirmation, runtimeError: text(data.error), microphoneName: text(data.microphoneName), errorCode: text(data.errorCode), cloudError: text(data.cloudError), feedback, soundCues: data.soundCues === true, soundCuesAvailable: data.soundCuesAvailable === true, showRequest: data.showRequest !== false, shortcuts: data.shortcuts && typeof data.shortcuts.toggle === 'string' && typeof data.shortcuts.open === 'string' ? { toggle: text(data.shortcuts.toggle), open: text(data.shortcuts.open) } : initial.shortcuts, shortcutError: text(data.shortcutError) })
            setConnected(true)
            setError('')
          } catch { setError('The runtime sent an unreadable update.') }
        }
        socket.onerror = () => setError('Cannot reach the voice runtime.')
        socket.onclose = event => {
          if (disposed) return
          setConnected(false)
          setSnapshot(initial)
          if (event.code === 1008) setError('Runtime authorization required. Use the authenticated URL from the Python bridge.')
          retry = setTimeout(connect, 3000)
        }
      } catch { setError('Invalid runtime connection URL.'); setConnected(false) }
    }
    connect()
    return () => { disposed = true; clearTimeout(retry); socket?.close(); socketRef.current = null }
  }, [url])

  const send = (action: 'pause' | 'resume' | 'stop' | 'confirm' | 'preferences', payload?: { id?: string; approved?: boolean; soundCues?: boolean; showRequest?: boolean }) => {
    if (!connected || socketRef.current?.readyState !== WebSocket.OPEN) return
    socketRef.current.send(JSON.stringify({ type: 'command', action, ...payload }))
  }
  useEffect(() => {
    if (!snapshot.confirmation) return
    const timer = setTimeout(() => setSnapshot(previous => ({ ...previous, confirmation: null })), Math.max(0, snapshot.confirmation.expiresAt - Date.now()))
    return () => clearTimeout(timer)
  }, [snapshot.confirmation?.id])
  useEffect(() => {
    if (!snapshot.feedback) return
    const timer = setTimeout(() => setSnapshot(previous => ({ ...previous, feedback: null })), Math.max(0, snapshot.feedback.expiresAt - Date.now()))
    return () => clearTimeout(timer)
  }, [snapshot.feedback?.id])
  return { ...snapshot, configured: Boolean(url), connected, error, send }
}
