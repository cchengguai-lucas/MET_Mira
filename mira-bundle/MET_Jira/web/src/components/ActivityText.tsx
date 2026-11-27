import { Fragment, type ReactNode } from 'react'
import { pick, t } from '../i18n'
import { fieldLabel, priorityName, typeName } from '../labels'
import type { Activity } from '../types'
import { PersonHover } from './PersonHover'

/** Like `t()`, but `{name}` placeholders may be filled with React nodes. */
function tNodes(text: string, nodes: Record<string, ReactNode>): ReactNode {
  return t(text)
    .split(/(\{\w+\})/)
    .map((part, index) => {
      const name = /^\{(\w+)\}$/.exec(part)?.[1]
      return <Fragment key={index}>{name && name in nodes ? nodes[name] : part}</Fragment>
    })
}

export function linkLabel(label: string): string {
  return label === '关联' ? pick({ zh: '关联', en: 'Relates to' }) : t(label)
}

function linkValue(value: string): string {
  const space = value.indexOf(' ')
  return space < 0 ? linkLabel(value) : linkLabel(value.slice(0, space)) + value.slice(space)
}

function activityValue(field: string | null, value: string | null): string | null {
  if (!value) return value
  if (field === 'priority') return priorityName(value)
  if (field === 'type') return typeName(value)
  return value
    .split(', ')
    .map((part) => (part.includes('/') && t(part) === part ? part.split('/').map((valuePart) => t(valuePart)).join('/') : t(part)))
    .join(', ')
}

export function ActivityText({
  activity: item,
  customLabels,
}: {
  activity: Activity
  customLabels?: Record<string, string>
}) {
  const who = item.actor ? (
    <PersonHover user={item.actor}>
      <b>{item.actor.display_name}</b>
    </PersonHover>
  ) : (
    <b>{t('系统')}</b>
  )
  const field = item.field ? fieldLabel(item.field, customLabels) : ''
  switch (item.action) {
    case 'created':
      return <span>{tNodes('{who} 创建了此问题', { who })}</span>
    case 'comment':
      return <span>{tNodes('{who} 发表了评论', { who })}</span>
    case 'attachment':
      return <span>{tNodes('{who} 上传了附件 {name}', { who, name: item.new_value })}</span>
    case 'attachment_removed':
      return <span>{tNodes('{who} 删除了附件 {name}', { who, name: item.old_value })}</span>
    case 'link':
      return <span>{tNodes('{who} 添加了关联：{link}', { who, link: item.new_value && linkValue(item.new_value) })}</span>
    case 'unlink':
      return <span>{tNodes('{who} 移除了关联', { who })}</span>
    case 'transition': {
      const reason = typeof item.meta?.reopen_reason === 'string' ? item.meta.reopen_reason : null
      return (
        <span>
          {tNodes(item.meta?.auto ? '{who} 将状态从 {from} 自动流转到 {to}' : '{who} 将状态从 {from} 流转到 {to}', {
            who,
            from: <span className="tag">{t(item.old_value ?? '—')}</span>,
            to: <span className="tag">{t(item.new_value ?? '—')}</span>,
          })}
          {reason ? <span className="activity-reopen-reason">{t('原因')}：{reason}</span> : null}
        </span>
      )
    }
    default:
      return (
        <span>
          {tNodes('{who} 修改了 {field}：{old} → {new}', {
            who,
            field: <b>{field}</b>,
            old: <span className="faint">{activityValue(item.field, item.old_value) || t('空')}</span>,
            new: activityValue(item.field, item.new_value) || t('空'),
          })}
        </span>
      )
  }
}
