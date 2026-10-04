import { useEffect, useState } from 'react'
import type useRuntime from './useRuntime'
import type { VoiceState } from './useRuntime'
import RuntimeConnection from './RuntimeConnection'
import { nativeAction, nativeSurface } from './native'

const states: Record<VoiceState, { title: string; helper: string; short: string; mic: string }> = {
  idle: { title: 'L.O.Q.I. is online', helper: 'Waiting for “Hey Loki”', short: 'Waiting for “Hey Loki”', mic: 'Microphone ready' },
  listening: { title: 'Listening', helper: 'Go ahead. I’m listening.', short: 'Listening', mic: 'Microphone active' },
  processing: { title: 'Understanding your request', helper: 'Processing on this PC.', short: 'Understanding your request', mic: 'Microphone ready' },
  speaking: { title: 'L.O.Q.I. is speaking', helper: 'You can interrupt at any time.', short: 'Speaking', mic: 'Microphone ready' },
  paused: { title: 'Listening is paused', helper: 'Take your time. I’ll be here.', short: 'Listening is paused', mic: 'Microphone paused' },
  unavailable: { title: 'Microphone unavailable', helper: 'Check your input device.', short: 'Microphone unavailable', mic: 'Microphone unavailable' },
}

function Icon({ name }: { name: 'pause' | 'play' | 'stop' | 'expand' | 'close' | 'mic' | 'shield' | 'widget' }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === 'pause' && <><path d="M9 6v12M15 6v12" strokeWidth="2.5" /></>}
    {name === 'play' && <path d="m9 5 10 7-10 7Z" />}
    {name === 'stop' && <rect x="6" y="6" width="12" height="12" rx="2" />}
    {name === 'close' && <path d="m7 7 10 10M17 7 7 17" />}
    {name === 'expand' && <path d="M8 4H4v4m12-4h4v4M4 16v4h4m12-4v4h-4" />}
    {name === 'widget' && <><rect x="3" y="5" width="18" height="14" rx="3" /><rect x="12" y="12" width="7" height="5" rx="1" /></>}
    {name === 'mic' && <><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M6 11a6 6 0 0 0 12 0M12 17v4m-3 0h6" /></>}
    {name === 'shield' && <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z" /><path d="m9 12 2 2 4-4" /></>}
  </svg>
}

function Aperture({ state, small = false, id, level = 0 }: { state: VoiceState; small?: boolean; id: string; level?: number }) {
  const quiet = state === 'paused' || state === 'unavailable'
  return <svg className={`aperture ${small ? 'aperture-small' : ''} ${quiet ? 'aperture-quiet' : ''}`} viewBox="0 0 280 280" fill="none" aria-hidden="true">
    <defs><linearGradient id={id} x1="45" y1="40" x2="240" y2="245" gradientUnits="userSpaceOnUse"><stop stopColor="#43D6C4" /><stop offset=".48" stopColor="#19B7A5" /><stop offset="1" stopColor="#328EE8" /></linearGradient></defs>
    {!small && <><circle cx="140" cy="140" r="135" className="aperture-guide" /><circle cx="140" cy="140" r="113" className="aperture-guide" /><path d="M140 0v7m0 266v7M0 140h7m266 0h7" className="aperture-guide" /></>}
    <g className="aperture-response" transform={`translate(140 140) scale(${1 + ((state === 'listening' || state === 'speaking') ? level * .035 : 0)}) translate(-140 -140)`} stroke={quiet ? 'var(--quiet-aperture)' : `url(#${id})`} strokeLinecap="round">
      <circle cx="140" cy="140" r="94" strokeWidth="13" strokeDasharray="430 161" transform="rotate(-44 140 140)" />
      <circle cx="140" cy="140" r="72" strokeWidth="12" strokeDasharray="305 148" transform="rotate(132 140 140)" opacity=".84" />
      <circle cx="140" cy="140" r="50" strokeWidth="11" strokeDasharray="222 92" transform="rotate(-48 140 140)" opacity=".68" />
      <path d={state === 'paused' ? 'M134 131v18m12-18v18' : state === 'unavailable' ? 'm134 134 12 12m0-12-12 12' : 'M140 130v20'} strokeWidth="6" />
    </g>
  </svg>
}

function AssistantView({ runtime }: { runtime: ReturnType<typeof useRuntime> }) {
  const [previewState, setState] = useState<VoiceState>('idle')
  const [previewCloud, setPreviewCloud] = useState(false)
  const state = runtime.configured ? runtime.state : previewState
  const disconnected = runtime.configured && !runtime.connected
  const route = runtime.configured ? runtime.route : (state === 'processing' || state === 'speaking') ? previewCloud ? 'cloud' : 'local' : null
  const [widgetVisible, setWidgetVisible] = useState(true)
  const [compact, setCompact] = useState(nativeSurface === 'widget')
  const offline = runtime.configured && (!runtime.connected || !runtime.online)
  const current = offline ? { title: disconnected ? 'Voice runtime disconnected' : runtime.runtimeError.startsWith('Starting') ? 'L.O.Q.I. is starting' : 'Voice runtime unavailable', helper: runtime.runtimeError || runtime.error || 'Waiting for the local assistant to connect.', short: disconnected ? 'Runtime disconnected' : 'Assistant unavailable', mic: 'Microphone status unknown' } : { ...states[state], ...(route === 'cloud' && state === 'processing' ? { title: 'Getting a cloud answer', short: 'Getting a cloud answer', helper: 'Preparing a response to your request.' } : {}), ...(runtime.configured ? { mic: runtime.microphoneReady ? state === 'listening' ? 'Microphone active' : 'Microphone ready' : state === 'paused' ? 'Microphone paused' : 'Microphone unavailable' } : {}) }
  const paused = state === 'paused'
  const speaking = state === 'speaking'
  const unavailable = state === 'unavailable' || offline
  const micTitles: Record<string, string> = { mic_disconnected: 'Microphone disconnected', mic_busy: 'Microphone busy or blocked', mic_unavailable: 'Microphone unavailable' }
  const feedback = !offline && state === 'idle' ? runtime.feedback : null
  const micError = !offline && state === 'unavailable' ? micTitles[runtime.errorCode] : ''
  const request = runtime.showRequest ? runtime.request : ''
  const actionLabel = speaking ? 'Stop speaking' : paused ? 'Resume listening' : 'Pause listening'
  const actionIcon = speaking ? 'stop' : paused ? 'play' : 'pause'
  const control = () => runtime.configured ? runtime.send(speaking ? 'stop' : paused ? 'resume' : 'pause') : setState(speaking || paused ? 'idle' : 'paused')
  const confirmation = runtime.confirmation
  const approve = (approved: boolean) => { if (confirmation) runtime.send('confirm', { id: confirmation.id, approved }) }
  const openFull = () => nativeSurface === 'widget' ? nativeAction('open_fullscreen') : setCompact(false)
  const expandedWidget = state === 'processing' || speaking || unavailable || Boolean(confirmation) || Boolean(request) || Boolean(feedback)
  useEffect(() => {
    if (nativeSurface !== 'widget') return
    const resize = () => nativeAction('resize_widget', confirmation ? 300 : expandedWidget ? 150 : 84)
    resize()
    window.addEventListener('pywebviewready', resize)
    return () => window.removeEventListener('pywebviewready', resize)
  }, [expandedWidget, confirmation?.id])
  useEffect(() => {
    const matches = (event: KeyboardEvent, shortcut: string) => {
      if (!shortcut) return false
      const parts = shortcut.toLowerCase().split('+').map(part => part.trim())
      const key = parts.pop()
      return event.ctrlKey === parts.includes('ctrl') && event.altKey === parts.includes('alt') && event.shiftKey === parts.includes('shift') && event.metaKey === parts.includes('win') && (key === 'space' ? event.code === 'Space' : event.key.toLowerCase() === key)
    }
    const shortcut = (event: KeyboardEvent) => {
      if (event.repeat || event.isComposing) return
      const target = event.target as HTMLElement
      if (target instanceof HTMLElement && target.closest('input, textarea, select, [contenteditable="true"]')) return
      if (event.key === 'Escape' && (confirmation || speaking)) {
        event.preventDefault()
        if (confirmation) approve(false)
        else control()
      } else if (!nativeSurface && matches(event, runtime.shortcuts.toggle) && !offline && !unavailable) {
        event.preventDefault()
        if (runtime.configured) runtime.send(paused ? 'resume' : 'pause')
        else setState(paused ? 'idle' : 'paused')
      } else if (!nativeSurface && matches(event, runtime.shortcuts.open)) {
        event.preventDefault()
        setCompact(false)
        setWidgetVisible(true)
      }
    }
    window.addEventListener('keydown', shortcut)
    return () => window.removeEventListener('keydown', shortcut)
  }, [confirmation?.id, state, offline, runtime.shortcuts.toggle, runtime.shortcuts.open])
  const approvalControls = confirmation && <section className="approval" aria-label="Close application confirmation"><h2>{confirmation.title}</h2><p>{confirmation.detail}</p><div className="approval-actions"><button className="cancel-approval" onClick={() => approve(false)}>Cancel</button><button className="confirm-approval" onClick={() => approve(true)}>Close all windows</button></div><span className="approval-note">No response cancels the action.</span></section>

  return <div className={`app state-${state} ${compact ? 'compact-mode' : ''} ${nativeSurface ? `native-${nativeSurface}` : ''}`}>
    <header className="header">
      <a className="identity" href="#" onClick={event => { event.preventDefault(); openFull() }} aria-label="L.O.Q.I. full-screen view">
        <Aperture state="idle" small id="brand-gradient" /><span>L.O.Q.I<span className="wordmark-period">.</span></span><span className="identity-divider" /><span className="identity-caption">Local Operations & Query Interface</span>
      </a>
      <div className="header-actions"><span className="process-status"><span className={`status-dot ${offline ? 'offline-dot' : ''}`} />{offline ? 'Offline' : 'Online'} {!runtime.configured && <span className="preview-tag">preview</span>}</span><button className="icon-button view-button" title={compact ? 'Open full-screen view' : 'Show compact view'} aria-label={compact ? 'Open full-screen view' : 'Show compact view'} onClick={() => { if (nativeSurface) nativeAction('show_widget'); else { setCompact(!compact); setWidgetVisible(true) } }}><Icon name={compact ? 'expand' : 'widget'} /></button></div>
    </header>

    {!compact && <main className="main">
      <div className={`microphone-status ${unavailable ? 'error-text' : ''}`}><Icon name="mic" /><span>{state === 'listening' && runtime.microphoneName ? `Listening on ${runtime.microphoneName}` : current.mic}{runtime.microphoneReady && state !== 'listening' && runtime.microphoneName && <span className="device-name"> · {runtime.microphoneName}</span>}</span></div>
      <div className="voice-focus"><Aperture state={unavailable ? 'unavailable' : state} id="main-gradient" level={runtime.connected ? runtime.audioLevel : 0} /><div className="state-copy" aria-live="polite" aria-atomic="true"><h1 className={feedback ? 'feedback-title' : ''}>{feedback?.text || micError || current.title}</h1><p>{micError ? runtime.runtimeError : current.helper}</p></div>
        {route && <span className="route-label" title={route === 'cloud' ? 'Transcript text and relevant text context may be sent to the cloud. Raw microphone audio stays local.' : 'This request is handled on your PC.'}><span className="status-dot" />{route === 'cloud' ? 'Cloud' : 'Local'}</span>}
        {route === 'cloud' && <p className="cloud-disclosure">Transcript text may be sent; microphone audio stays local.</p>}
        {route === 'cloud' && runtime.cloudError && <p className="cloud-error" role="status">{runtime.cloudError}</p>}
        {request && <p className="current-request">“{request}”</p>}
        {approvalControls}
        {!unavailable && !confirmation && <button className={`primary-control ${speaking ? 'stop-control' : ''}`} onClick={control}><Icon name={actionIcon} />{actionLabel}</button>}
        {unavailable && !offline && nativeSurface && <button className="primary-control" onClick={() => nativeAction('open_sound_settings')}><Icon name="mic" />Open sound settings</button>}
        <span className="control-note">{offline ? 'Controls become available when the runtime is ready.' : unavailable ? 'Choose an available microphone in Windows sound settings.' : paused ? 'Resume when you’re ready.' : speaking ? 'Stops this response, not the assistant.' : 'Wake-word listening pauses until you resume.'}</span>
        {!unavailable && !confirmation && (speaking || runtime.shortcuts.toggle) && <div className="shortcut-hint"><span>{speaking ? 'Stop response' : 'Pause / resume'}</span><kbd>{speaking ? 'Esc' : runtime.shortcuts.toggle.split('+').join(' + ')}</kbd></div>}
      </div>
    </main>}

    {widgetVisible && nativeSurface !== 'full' && <aside className={`widget ${expandedWidget ? 'widget-expanded' : ''} ${confirmation ? 'widget-approval' : ''}`} aria-label="L.O.Q.I. desktop widget">
      <button className="widget-body" onClick={openFull} title="Open full-screen view"><Aperture state={unavailable ? 'unavailable' : state} small id="widget-gradient" level={runtime.connected ? runtime.audioLevel : 0} /><span className="widget-copy"><span className="widget-identity pywebview-drag-region">L.O.Q.I. <span className="widget-online"><span className={`status-dot ${offline ? 'offline-dot' : ''}`} />{offline ? 'Offline' : runtime.configured ? 'Online' : 'Preview'}</span></span><span className="widget-state">{feedback?.text || micError || current.short}</span>{expandedWidget && <span className="widget-helper">{micError ? runtime.runtimeError : unavailable ? current.helper : (route ? (route === 'cloud' ? 'Cloud · ' : 'Local · ') : '') + current.helper}</span>}</span></button>
      <div className="widget-controls">{!unavailable && !confirmation && <button className={`icon-button widget-primary ${speaking ? 'stop-control' : ''}`} onClick={control} title={actionLabel} aria-label={actionLabel}><Icon name={actionIcon} /></button>}<button className="dismiss-widget" title="Hide widget (L.O.Q.I. stays running)" aria-label="Hide widget (L.O.Q.I. stays running)" onClick={() => { if (nativeSurface) nativeAction('hide_widget'); else { setWidgetVisible(false); setCompact(false) } }}><Icon name="close" /></button></div>
      {request && <p className="widget-request" title={request}>“{request}”</p>}
      {confirmation && <div className="widget-approval-content">{approvalControls}</div>}
    </aside>}
    {!widgetVisible && <button className="restore-widget" onClick={() => setWidgetVisible(true)}><Icon name="widget" />Show widget</button>}

    <footer className="footer">
      <span className="footer-product">L.O.Q.I. <span>Local-first. Quietly capable.</span></span>
      <div className="footer-utilities">
        {runtime.connected && <div className="utility-preferences">
          <label title={runtime.soundCuesAvailable ? 'Play a brief tone before capture and a different tone when capture ends.' : 'Listening cues require the Windows runtime.'}><input type="checkbox" checked={runtime.soundCues} disabled={!runtime.soundCuesAvailable} onChange={event => runtime.send('preferences', { soundCues: event.target.checked })} />Listening sounds</label>
          <label title="Show only the current request. It clears when the interaction ends."><input type="checkbox" checked={runtime.showRequest} onChange={event => runtime.send('preferences', { showRequest: event.target.checked })} />Request text</label>
          {runtime.shortcuts.open && <span className="open-shortcut">Open assistant <kbd>{runtime.shortcuts.open.split('+').join(' + ')}</kbd><span className="sr-only">{nativeSurface ? 'Global Windows shortcut' : 'Works while this page is focused'}</span></span>}
        </div>}
        <div className="preview-controls"><span className="runtime-note">{runtime.configured ? runtime.connected ? 'Connected to voice runtime' : 'Voice runtime unavailable · Reconnecting' : 'Interface preview · Runtime not connected'}</span>{!runtime.configured && <label className="state-select"><span className="sr-only">Preview assistant state</span><select value={state === 'processing' && previewCloud ? 'cloud' : state} onChange={event => { setPreviewCloud(event.target.value === 'cloud'); setState(event.target.value === 'cloud' ? 'processing' : event.target.value as VoiceState) }}><option value="idle">Idle / ready</option><option value="listening">Listening</option><option value="processing">Processing locally</option><option value="cloud">Cloud response</option><option value="speaking">Speaking</option><option value="paused">Paused</option><option value="unavailable">Mic unavailable</option></select></label>}</div>
        {runtime.shortcutError && <span className="shortcut-warning" role="status">Shortcut unavailable: {runtime.shortcutError}</span>}
      </div>
    </footer>
  </div>
}

export default function App() {
  return <RuntimeConnection>{runtime => <AssistantView runtime={runtime} />}</RuntimeConnection>
}
