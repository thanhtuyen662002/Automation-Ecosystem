// ── i18n — Vietnamese (default) + English ────────────────────────────────────
// Simple context-based i18n. No heavy library required for this scope.
// Add keys here; components call t('key').

export type Lang = 'vi' | 'en';

export const translations: Record<Lang, Record<string, string>> = {
  vi: {
    // Navigation
    'nav.command':     'Trung Tâm Lệnh',
    'nav.executive':   'Dashboard CEO',
    'nav.queue':       'Hàng Chờ Nội Dung',
    'nav.fleet':       'Sức Khỏe Đội',
    'nav.brain':       'Bộ Não CEO',
    'nav.niches':      'Hiệu Suất Ngách',
    'nav.overrides':   'Ghi Đè Chiến Lược',
    'nav.jobs':        'Pipeline Jobs',
    'nav.artifacts':   'Artifacts',
    'nav.accounts':    'Tài Khoản',
    'nav.identities':  'Danh Tính',
    'nav.settings':    'Cài Đặt',

    // Actions
    'action.approve':      'Đăng',
    'action.reject':       'Từ Chối',
    'action.force':        'Bắt Buộc Đăng',
    'action.freeze':       'Đóng Băng',
    'action.skip':         'Bỏ Qua',
    'action.clear':        'Xóa',
    'action.approve_all':  'Duyệt Tất Cả Giá Trị Cao',
    'action.apply':        'Áp Dụng',
    'action.login':        'Đăng Nhập',

    // Decision labels
    'decision.action_required': '🔥 CẦN HÀNH ĐỘNG NGAY',
    'decision.system_status':   '⚠ TRẠNG THÁI HỆ THỐNG',
    'decision.background':      '📊 THÔNG TIN NỀN',
    'decision.no_actions':      'Không có hành động cần thực hiện',
    'decision.if_skip':         'Nếu bỏ qua:',
    'decision.ev':              'Giá trị kỳ vọng',
    'decision.risk':            'Rủi ro',

    // Risk labels
    'risk.low':    'Thấp',
    'risk.medium': 'Trung Bình',
    'risk.high':   'Cao',

    // Status
    'status.execution_on':  'Máy Chạy: BẬT',
    'status.execution_off': 'Máy Chạy: TẮT',
    'status.accounts':      'tài khoản hoạt động',
    'status.pending':       'chờ duyệt',

    // Auth
    'auth.title':       'Đăng Nhập Hệ Thống',
    'auth.account':     'Tên Tài Khoản',
    'auth.license':     'License Key',
    'auth.login':       'Đăng Nhập',
    'auth.error':       'Tài khoản hoặc license key không đúng',

    // Settings
    'settings.language':    'Ngôn Ngữ',
    'settings.theme':       'Giao Diện',
    'settings.theme.dark':  'Dark Command',
    'settings.theme.light': 'Light SaaS',
    'settings.theme.neon':  'Neon Tech',
    'settings.execution':   'Bật Máy Thực Thi',
    'settings.auto_approve':'Tự Động Duyệt',
  },
  en: {
    'nav.command':     'Command Center',
    'nav.executive':   'Executive Dashboard',
    'nav.queue':       'Content Queue',
    'nav.fleet':       'Fleet Health',
    'nav.brain':       'CEO Brain',
    'nav.niches':      'Niche Performance',
    'nav.overrides':   'Strategy Overrides',
    'nav.jobs':        'Pipeline Jobs',
    'nav.artifacts':   'Artifacts',
    'nav.accounts':    'Accounts',
    'nav.identities':  'Identities',
    'nav.settings':    'Settings',

    'action.approve':      'Publish',
    'action.reject':       'Reject',
    'action.force':        'Force Publish',
    'action.freeze':       'Freeze',
    'action.skip':         'Skip',
    'action.clear':        'Clear',
    'action.approve_all':  'Approve All High-Value',
    'action.apply':        'Apply',
    'action.login':        'Login',

    'decision.action_required': '🔥 ACTION REQUIRED',
    'decision.system_status':   '⚠ SYSTEM STATUS',
    'decision.background':      '📊 BACKGROUND',
    'decision.no_actions':      'No actions required right now',
    'decision.if_skip':         'If you skip:',
    'decision.ev':              'Expected value',
    'decision.risk':            'Risk',

    'risk.low':    'Low',
    'risk.medium': 'Medium',
    'risk.high':   'High',

    'status.execution_on':  'Engine: ON',
    'status.execution_off': 'Engine: OFF',
    'status.accounts':      'active accounts',
    'status.pending':       'pending',

    'auth.title':   'System Login',
    'auth.account': 'Account',
    'auth.license': 'License Key',
    'auth.login':   'Login',
    'auth.error':   'Invalid account or license key',

    'settings.language':    'Language',
    'settings.theme':       'Theme',
    'settings.theme.dark':  'Dark Command',
    'settings.theme.light': 'Light SaaS',
    'settings.theme.neon':  'Neon Tech',
    'settings.execution':   'Enable Execution Engine',
    'settings.auto_approve':'Auto-Approve Content',
  },
};

// ── React context ─────────────────────────────────────────────────────────────
import React, { createContext, useContext, useState, useCallback } from 'react';

interface I18nCtx { lang: Lang; t: (key: string, fallback?: string) => string; setLang: (l: Lang) => void; }
const I18nContext = createContext<I18nCtx>({ lang: 'vi', t: k => k, setLang: () => {} });

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const stored = (localStorage.getItem('lang') ?? 'vi') as Lang;
  const [lang, setLangState] = useState<Lang>(stored);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    localStorage.setItem('lang', l);
  }, []);

  const t = useCallback((key: string, fallback?: string): string => {
    return translations[lang][key] ?? translations['en'][key] ?? fallback ?? key;
  }, [lang]);

  return <I18nContext.Provider value={{ lang, t, setLang }}>{children}</I18nContext.Provider>;
}

export const useI18n = () => useContext(I18nContext);
