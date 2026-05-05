import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./en";
import vi from "./vi";

const STORAGE_KEY = "ae-language";
const savedLang = localStorage.getItem(STORAGE_KEY);
const defaultLng = savedLang === "en" || savedLang === "vi" ? savedLang : "vi";

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    vi: { translation: vi },
  },
  lng: defaultLng,
  fallbackLng: "en",
  interpolation: {
    // React already escapes values, so no need for i18next to do it
    escapeValue: false,
  },
});

/** Persist the chosen language on every change. */
i18n.on("languageChanged", (lng) => {
  localStorage.setItem(STORAGE_KEY, lng);
});

export default i18n;
