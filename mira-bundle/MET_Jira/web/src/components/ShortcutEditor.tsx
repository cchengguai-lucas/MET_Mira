import { type CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useApp } from '../context'
import { t } from '../i18n'
import {
  ACTION_BY_ID, ACTION_GROUPS, ACTIONS, type ActionId, BROWSER_COMBOS, type Bindings, ESC_KEY, KEY_BY_CODE, KEY_ROWS,
  type KeyDef, type Layer, LAYERS, SYSTEM_KEYS, comboKeys, comboOf, comboText, defaultBindings, splitCombo,
  useShortcutBindings,
} from '../shortcuts'
import { Modal, Segmented, Select } from './ui'

type Notice = { kind: 'ok' | 'error' | 'info'; text: string }
type Conflict = { combo: string; owner: ActionId; action: ActionId }

const LAYER_LABELS: Record<Layer, string> = { '': '单键', Shift: 'Shift +', Alt: 'Alt +' }
const SOUND_KEY = 'mira.keyboard.sound'

/** A short mechanical "thock" synthesized on the fly; no audio assets. */
function useClickSound(enabled: boolean) {
  const ctx = useRef<AudioContext | null>(null)
  return useCallback(() => {
    if (!enabled) return
    try {
      ctx.current ??= new AudioContext()
      const audio = ctx.current
      const now = audio.currentTime
      const osc = audio.createOscillator()
      const gain = audio.createGain()
      osc.type = 'triangle'
      osc.frequency.setValueAtTime(420, now)
      osc.frequency.exponentialRampToValueAtTime(140, now + 0.06)
      gain.gain.setValueAtTime(0.18, now)
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.08)
      osc.connect(gain).connect(audio.destination)
      osc.start(now)
      osc.stop(now + 0.09)
    } catch { /* audio unavailable */ }
  }, [enabled])
}

export function KeyChips({ combo }: { combo: string | null | undefined }) {
  const keys = comboKeys(combo)
  if (!keys.length) return <span className="tiny faint">{t('未绑定')}</span>
  return <span className="kb-chips">{keys.map((k, i) => <kbd key={i}>{k}</kbd>)}</span>
}

export function ShortcutEditor({ onClose }: { onClose: () => void }) {
  const { user, toast } = useApp()
  const { bindings, save } = useShortcutBindings()
  const [draft, setDraft] = useState<Bindings>(bindings)
  const [layer, setLayer] = useState<Layer>('')
  const [held, setHeld] = useState<Layer | null>(null)
  const [selected, setSelected] = useState<ActionId | null>(null)
  const [focus, setFocus] = useState<string | null>(null)
  const [conflict, setConflict] = useState<Conflict | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [down, setDown] = useState<Set<string>>(new Set())
  const [shake, setShake] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [sound, setSound] = useState(() => localStorage.getItem(SOUND_KEY) === '1')
  const click = useClickSound(sound)
  const actions = useMemo(() => ACTIONS.filter((a) => !a.adminOnly || user.is_admin), [user.is_admin])
  const dirty = JSON.stringify(draft) !== JSON.stringify(bindings)
  const shown = held ?? layer
  const ownerOf = useCallback((combo: string) => actions.find((a) => draft[a.id] === combo)?.id ?? null, [actions, draft])

  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(null), 3200)
    return () => window.clearTimeout(timer)
  }, [notice])
  useEffect(() => {
    if (!shake) return
    const timer = window.setTimeout(() => setShake(null), 420)
    return () => window.clearTimeout(timer)
  }, [shake])

  const reject = (code: string, text: string) => {
    setShake(code)
    setNotice({ kind: 'error', text })
  }

  const bind = (action: ActionId, combo: string) => {
    setDraft((current) => {
      const next = { ...current }
      for (const a of ACTIONS) if (next[a.id] === combo) next[a.id] = ''
      next[action] = combo
      return next
    })
    setNotice({ kind: 'ok', text: t('「{action}」→ {key}', { action: t(ACTION_BY_ID[action].label), key: comboText(combo) }) })
    setSelected(null)
    setConflict(null)
    setFocus(combo)
  }

  const unbind = (action: ActionId) => {
    setDraft((current) => ({ ...current, [action]: '' }))
    setNotice({ kind: 'info', text: t('已解除「{action}」的快捷键', { action: t(ACTION_BY_ID[action].label) }) })
    setConflict(null)
  }

  const press = (code: string, pressedLayer: Layer) => {
    const key = KEY_BY_CODE[code]
    if (!key) return
    click()
    if (key.kind === 'mod') {
      if (key.label === 'Shift' || key.label === 'Alt') setLayer((current) => (current === key.label ? '' : key.label as Layer))
      else reject(code, t('Ctrl / ⌘ 组合留给浏览器和系统，Ctrl + K 固定为快速跳转'))
      return
    }
    if (key.kind === 'sys') return reject(code, t(SYSTEM_KEYS[code] ?? '这个键由系统使用'))
    const combo = comboOf(pressedLayer, code)
    if (BROWSER_COMBOS[combo]) return reject(code, t(BROWSER_COMBOS[combo]))
    setLayer(pressedLayer)
    const owner = ownerOf(combo)
    setConflict(null)
    if (!selected) {
      setFocus(combo)
      return
    }
    if (owner === selected) {
      setNotice({ kind: 'info', text: t('「{action}」本来就是 {key}', { action: t(ACTION_BY_ID[selected].label), key: comboText(combo) }) })
      setSelected(null)
      setFocus(combo)
    } else if (owner) {
      setFocus(combo)
      setConflict({ combo, owner, action: selected })
    } else {
      bind(selected, combo)
    }
  }
  const pressRef = useRef(press)
  pressRef.current = press

  const select = (id: ActionId) => {
    setConflict(null)
    if (selected === id) {
      setSelected(null)
      return
    }
    setSelected(id)
    const combo = draft[id]
    setFocus(combo || null)
    if (combo) setLayer(splitCombo(combo).layer)
  }

  const clearRef = useRef<() => boolean>(() => false)
  clearRef.current = () => {
    if (!selected && !conflict && !focus) return false
    setSelected(null)
    setConflict(null)
    setFocus(null)
    return true
  }

  useEffect(() => {
    const onDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (clearRef.current()) {
          e.preventDefault()
          e.stopImmediatePropagation()
        }
        return
      }
      if (e.ctrlKey || e.metaKey) return
      const target = e.target as HTMLElement
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return
      if (!KEY_BY_CODE[e.code]) return
      e.preventDefault()
      setDown((current) => new Set(current).add(e.code))
      if (e.key === 'Shift') setHeld('Shift')
      else if (e.key === 'Alt') setHeld('Alt')
      else if (!e.repeat && !(e.altKey && e.shiftKey)) pressRef.current(e.code, e.altKey ? 'Alt' : e.shiftKey ? 'Shift' : '')
    }
    const onUp = (e: KeyboardEvent) => {
      if (KEY_BY_CODE[e.code]) e.preventDefault()
      setDown((current) => {
        const next = new Set(current)
        next.delete(e.code)
        return next
      })
      if (e.key === 'Shift' || e.key === 'Alt') setHeld(null)
    }
    const reset = () => {
      setDown(new Set())
      setHeld(null)
    }
    window.addEventListener('keydown', onDown, true)
    window.addEventListener('keyup', onUp, true)
    window.addEventListener('blur', reset)
    return () => {
      window.removeEventListener('keydown', onDown, true)
      window.removeEventListener('keyup', onUp, true)
      window.removeEventListener('blur', reset)
    }
  }, [])

  const close = () => {
    if (dirty && !window.confirm(t('快捷键修改还没有保存，确定放弃吗？'))) return
    onClose()
  }
  const submit = async () => {
    setSaving(true)
    try {
      await save(draft)
      toast(t('快捷键已保存'), 'success')
      onClose()
    } catch (error) {
      toast((error as Error).message, 'error')
    } finally {
      setSaving(false)
    }
  }

  const layerCount = (l: Layer) => actions.filter((a) => draft[a.id] && splitCombo(draft[a.id]).layer === l).length
  const focusOwner = focus ? ownerOf(focus) : null

  const keycap = (key: KeyDef) => {
    const combo = comboOf(shown, key.code)
    const owner = key.kind ? null : ownerOf(combo)
    const action = owner ? ACTION_BY_ID[owner] : null
    const modOn = key.kind === 'mod' && key.label === shown
    const browser = !key.kind && BROWSER_COMBOS[combo]
    const legend = shown === 'Shift' && key.shift ? key.shift : key.label
    const classes = [
      'kb-key',
      key.kind ?? (browser ? 'sys' : 'free'),
      action ? 'bound' : '',
      selected && owner === selected ? 'target' : '',
      focus === combo && !key.kind ? 'focus' : '',
      down.has(key.code) || modOn ? 'down' : '',
      shake === key.code ? 'shake' : '',
    ].filter(Boolean).join(' ')
    const title = action
      ? `${comboText(combo)} → ${t(action.label)}`
      : key.kind === 'sys' ? t(SYSTEM_KEYS[key.code] ?? '这个键由系统使用')
      : browser ? t(browser) : key.kind === 'mod' ? key.label : t('{key} 未绑定', { key: comboText(combo) })
    return (
      <button key={key.code} type="button" className={classes} title={title} data-code={key.code}
        style={{ '--w': key.w ?? 1, ...(action ? { '--cap': action.color } : {}) } as CSSProperties}
        onClick={() => press(key.code, shown)}>
        <span className="kb-legend">{legend}</span>
        {action && <span className="kb-sticker"><i>{action.glyph}</i>{t(action.short)}</span>}
      </button>
    )
  }

  const hint = () => {
    if (conflict) {
      return (
        <div className="kb-hint warn">
          <span>{t('{key} 已经绑定了「{owner}」。', { key: comboText(conflict.combo), owner: t(ACTION_BY_ID[conflict.owner].label) })}</span>
          <span className="spacer" />
          <button type="button" className="btn sm primary" onClick={() => bind(conflict.action, conflict.combo)}>
            {t('改为「{action}」', { action: t(ACTION_BY_ID[conflict.action].label) })}
          </button>
          <button type="button" className="btn sm" onClick={() => setConflict(null)}>{t('保留原来的')}</button>
        </div>
      )
    }
    if (selected) {
      const action = ACTION_BY_ID[selected]
      return (
        <div className="kb-hint picking">
          <span className="kb-pulse" />
          <span>{t('为「{action}」选一个键：点击下方键帽，或直接在键盘上按下组合键', { action: t(action.label) })}</span>
          <span className="spacer" />
          {draft[selected] && <button type="button" className="btn sm" onClick={() => unbind(selected)}>{t('解除绑定')}</button>}
          <button type="button" className="btn sm ghost" onClick={() => setSelected(null)}>{t('取消')}</button>
        </div>
      )
    }
    if (focus) {
      return (
        <div className="kb-hint">
          <KeyChips combo={focus} />
          <span>{focusOwner ? `→ ${t(ACTION_BY_ID[focusOwner].label)}` : t('还没有绑定功能')}</span>
          <span className="spacer" />
          <Select className="select sm kb-assign" value={focusOwner ?? ''} aria-label={t('绑定到')}
            onChange={(e) => {
              const id = e.target.value as ActionId
              if (id) bind(id, focus)
              else if (focusOwner) unbind(focusOwner)
            }}>
            <option value="">{focusOwner ? t('解除绑定') : t('绑定到…')}</option>
            {actions.map((a) => <option key={a.id} value={a.id}>{t(a.label)}{draft[a.id] && draft[a.id] !== focus ? ` · ${comboText(draft[a.id])}` : ''}</option>)}
          </Select>
        </div>
      )
    }
    return (
      <div className="kb-hint muted">
        <span>{t('先在左侧选一个功能，再点键帽或直接按下想要的键；也可以先点键帽，查看或分配它的功能。')}</span>
      </div>
    )
  }

  return (
    <Modal
      size="full"
      onClose={close}
      title={<span className="row" style={{ gap: 8 }}>⌘ {t('键盘快捷键')}</span>}
      footer={(
        <>
          <button type="button" className="btn ghost" onClick={() => {
            setDraft(defaultBindings())
            setSelected(null)
            setConflict(null)
            setNotice({ kind: 'info', text: t('已恢复默认快捷键，保存后生效') })
          }}>{t('恢复默认')}</button>
          <span className="spacer" />
          {dirty && <span className="small warn-text">{t('有未保存的修改')}</span>}
          <button type="button" className="btn" onClick={close}>{t('取消')}</button>
          <button type="button" className="btn primary" disabled={!dirty || saving} onClick={submit}>{t('保存')}</button>
        </>
      )}
    >
      <div className="kb-editor">
        <aside className="kb-actions" aria-label={t('可绑定的功能')}>
          {ACTION_GROUPS.map((group) => {
            const items = actions.filter((a) => a.group === group.id)
            if (!items.length) return null
            return (
              <section key={group.id}>
                <div className="kb-group">{t(group.label)}</div>
                {items.map((a) => (
                  <button key={a.id} type="button" className={`kb-action ${selected === a.id ? 'active' : ''}`}
                    aria-pressed={selected === a.id} onClick={() => select(a.id)}>
                    <span className="kb-glyph" style={{ background: a.color }}>{a.glyph}</span>
                    <span className="kb-action-text">
                      <b>{t(a.label)}</b>
                      <span className="tiny muted">{t(a.hint)}</span>
                    </span>
                    <KeyChips combo={draft[a.id]} />
                  </button>
                ))}
              </section>
            )
          })}
        </aside>
        <div className="kb-stage">
          {hint()}
          <div className="kb-toolbar">
            <Segmented value={layer} onChange={(value) => setLayer(value)}
              items={LAYERS.map((l) => ({
                value: l,
                label: <span>{t(LAYER_LABELS[l])} <span className="kb-count">{layerCount(l)}</span></span>,
              }))} />
            {notice && <span className={`kb-notice ${notice.kind}`} role="status">{notice.text}</span>}
            <span className="spacer" />
            <label className="row small muted kb-sound">
              <input type="checkbox" checked={sound} onChange={(e) => {
                setSound(e.target.checked)
                localStorage.setItem(SOUND_KEY, e.target.checked ? '1' : '0')
              }} />
              {t('按键音')}
            </label>
          </div>
          <div className={`kb-board ${selected ? 'picking' : ''}`}>
            <div className="kb-top">
              {keycap(ESC_KEY)}
              <span className="kb-brand">MIRA</span>
              <span className="spacer" />
              <span className="kb-leds" aria-hidden>
                {LAYERS.map((l) => <i key={l || 'base'} className={shown === l ? 'on' : ''} title={t(LAYER_LABELS[l])} />)}
              </span>
            </div>
            <div className="kb-plate">
              {KEY_ROWS.map((row, i) => <div className="kb-row" key={i}>{row.map(keycap)}</div>)}
            </div>
          </div>
          <div className="kb-legend-bar small muted">
            <span><i className="kb-swatch bound" />{t('已绑定')}</span>
            <span><i className="kb-swatch free" />{t('可绑定')}</span>
            <span><i className="kb-swatch sys" />{t('系统保留')}</span>
            <span className="spacer" />
            <span>{t('固定：Ctrl + K 快速跳转 · Esc 关闭 · Enter 保存 / 发送 · Ctrl + Enter 提交')}</span>
          </div>
          <p className="tiny faint">{t('快捷键在没有输入焦点、没有打开弹窗时生效；保存在你的账号上，换电脑或浏览器也一样。')}</p>
        </div>
      </div>
    </Modal>
  )
}
