import { LANGUAGES, t, useI18n } from '../i18n'
import { FieldIcon, Modal } from './ui'

export function LanguageModal({ onClose }: { onClose: () => void }) {
  const { lang, setLang } = useI18n()
  return (
    <Modal title={t('语言设置')} icon={<FieldIcon name="language" size={18} />} size="narrow" onClose={onClose}>
      <div className="col" style={{ gap: 12 }}>
        <div className="small muted">{t('选择界面显示语言，设置保存在当前浏览器。')}</div>
        <div className="lang-options" role="radiogroup" aria-label={t('界面语言')}>
          {LANGUAGES.map((option) => (
            <button
              key={option.value}
              role="radio"
              aria-checked={lang === option.value}
              className={`lang-option ${lang === option.value ? 'active' : ''}`}
              onClick={() => {
                onClose()
                if (option.value !== lang) setLang(option.value)
              }}
            >
              <span className="lang-radio" />
              <span className="col" style={{ gap: 2, flex: 1, alignItems: 'flex-start' }}>
                <b>{option.label}</b>
                <span className="tiny muted">{option.hint}</span>
              </span>
              {lang === option.value && <span className="tiny accent-text">{t('当前')}</span>}
            </button>
          ))}
        </div>
        <div className="tiny faint">{t('AI 助手的回复也会使用所选语言。')}</div>
      </div>
    </Modal>
  )
}
