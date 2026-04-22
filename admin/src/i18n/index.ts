import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './en.json'
import zh from './zh.json'

const LANG_STORAGE_KEY = 'sitetracker-admin-lang'

function readStoredLanguage(): 'en' | 'zh' {
  if (typeof window === 'undefined') return 'en'
  const stored = window.localStorage.getItem(LANG_STORAGE_KEY)
  return stored === 'zh' ? 'zh' : 'en'
}

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    zh: { translation: zh },
  },
  lng: readStoredLanguage(),
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

export default i18n

export function setLanguage(lang: 'en' | 'zh') {
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(LANG_STORAGE_KEY, lang)
  }
  void i18n.changeLanguage(lang)
}
