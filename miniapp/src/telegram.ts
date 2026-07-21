type Haptic = {
  impactOccurred?: (style: 'light' | 'medium' | 'heavy') => void
  notificationOccurred?: (type: 'error' | 'success' | 'warning') => void
  selectionChanged?: () => void
}

type TelegramWebApp = {
  initData: string
  colorScheme: 'light' | 'dark'
  ready: () => void
  expand: () => void
  setHeaderColor?: (color: string) => void
  setBackgroundColor?: (color: string) => void
  HapticFeedback?: Haptic
}

declare global {
  interface Window { Telegram?: { WebApp?: TelegramWebApp } }
}

export const tg = window.Telegram?.WebApp

export function initializeTelegram() {
  tg?.ready()
  tg?.expand()
  document.documentElement.dataset.theme = tg?.colorScheme || 'light'
}

export function hapticSuccess() { tg?.HapticFeedback?.notificationOccurred?.('success') }
export function hapticSelection() { tg?.HapticFeedback?.selectionChanged?.() }
