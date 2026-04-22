import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import storage from './storage';
import en from './en.json';
import zh from './zh.json';

const KEY = 'sitetracker_lang';
export type Lang = 'en' | 'zh';

export async function initI18n(): Promise<void> {
  const saved = ((await storage.getItem(KEY)) as Lang | null) ?? 'en';
  if (!i18n.isInitialized) {
    await i18n.use(initReactI18next).init({
      resources: {
        en: { translation: en },
        zh: { translation: zh },
      },
      lng: saved,
      fallbackLng: 'en',
      interpolation: { escapeValue: false },
      compatibilityJSON: 'v4',
    });
  } else {
    await i18n.changeLanguage(saved);
  }
}

export async function setLanguage(lang: Lang): Promise<void> {
  await storage.setItem(KEY, lang);
  await i18n.changeLanguage(lang);
}

export default i18n;
