import { createContext, Fragment, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { EN, EN_SERVER } from './locales/en'

export type Lang = 'zh' | 'en'

export const LANGUAGES: { value: Lang; label: string; hint: string }[] = [
  { value: 'zh', label: '简体中文', hint: 'Chinese (Simplified)' },
  { value: 'en', label: 'English', hint: '英语' },
]

const STORAGE_KEY = 'metjira.lang'

function initialLang(): Lang {
  const saved = localStorage.getItem(STORAGE_KEY)
  return saved === 'en' || saved === 'zh' ? saved : 'zh'
}

let current: Lang = initialLang()
document.documentElement.lang = current === 'zh' ? 'zh-CN' : 'en'

export function getLang(): Lang {
  return current
}

/** BCP 47 locale for `Intl` / `toLocale*String`. */
export function locale(): string {
  return current === 'zh' ? 'zh-CN' : 'en-US'
}

type Vars = Record<string, string | number | null | undefined>

function fill(text: string, vars?: Vars): string {
  if (!vars) return text
  return text.replace(/\{(\w+)\}/g, (match, name: string) => (name in vars ? String(vars[name] ?? '') : match))
}

/**
 * UI strings are written in Chinese and double as dictionary keys; placeholders use `{name}`.
 * Safe to call outside components: the app tree remounts when the language changes.
 */
export function t(text: string, vars?: Vars): string {
  return fill(current === 'en' ? (EN[text] ?? text) : text, vars)
}

/** Pick the Chinese or English variant of a bilingual label such as `TYPE_META[type]`. */
export function pick(meta: { zh: string; en: string }): string {
  return current === 'en' ? meta.en : meta.zh
}

const SERVER_PATTERNS = Object.entries(EN_SERVER)
  .filter(([zh]) => zh.includes('{'))
  .map(([zh, en]) => {
    const names: string[] = []
    const source = zh
      .split(/(\{\w+\})/)
      .map((part) => {
        const m = part.match(/^\{(\w+)\}$/)
        if (!m) return part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        names.push(m[1])
        return '([\\s\\S]*?)'
      })
      .join('')
    return { regex: new RegExp(`^${source}$`), names, en }
  })

/** Placeholders that carry system values; everything else (names, titles, keys) is user content. */
const TRANSLATED_SLOTS = new Set(['status', 'from', 'to', 'field', 'fields', 'options', 'type', 'priority', 'error'])

function fragment(name: string, text: string): string {
  if (!TRANSLATED_SLOTS.has(name)) return text
  const one = (part: string) => EN_SERVER[part] ?? EN[part] ?? part
  if (name === 'fields' || name === 'options') return text.split(/、|, /).map(one).join(', ')
  return name === 'error' ? tServer(text) : one(text)
}

/** Translate a message produced by the backend (errors, notifications); unknown text passes through. */
export function tServer(message: string | null | undefined): string {
  if (!message) return ''
  if (current !== 'en') return message
  const exact = EN_SERVER[message] ?? EN[message]
  if (exact) return exact
  for (const { regex, names, en } of SERVER_PATTERNS) {
    const m = message.match(regex)
    if (m) return fill(en, Object.fromEntries(names.map((name, i) => [name, fragment(name, m[i + 1])])))
  }
  return message
}

interface I18nValue {
  lang: Lang
  setLang: (lang: Lang) => void
  t: typeof t
}

const I18nContext = createContext<I18nValue>({ lang: current, setLang: () => {}, t })

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(current)
  const setLang = useCallback((next: Lang) => {
    current = next
    localStorage.setItem(STORAGE_KEY, next)
    document.documentElement.lang = next === 'zh' ? 'zh-CN' : 'en'
    setLangState(next)
  }, [])
  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang])
  return (
    <I18nContext.Provider value={value}>
      <Fragment key={lang}>{children}</Fragment>
    </I18nContext.Provider>
  )
}

export function useI18n(): I18nValue {
  return useContext(I18nContext)
}
