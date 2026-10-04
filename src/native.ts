type NativeApi = { open_fullscreen: () => Promise<void>; hide_widget: () => Promise<void>; show_widget: () => Promise<void>; resize_widget: (height: number) => Promise<void>; open_sound_settings: () => Promise<void> }
declare global {
  interface Window { pywebview?: { api: NativeApi } }
}
export const nativeSurface = new URLSearchParams(window.location.hash.slice(1)).get('surface')
export function nativeAction(action: keyof NativeApi, height?: number) {
  const api = window.pywebview?.api
  if (!api) return
  const result = action === 'resize_widget' ? api.resize_widget(height || 84) : api[action]()
  void result.catch(() => console.warn(`Native action unavailable: ${action}`))
}
