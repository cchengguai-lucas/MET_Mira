import { t } from './i18n'

export type MasterKind = 'material' | 'line' | 'equipment' | 'service'

export const MASTER_KINDS: MasterKind[] = ['material', 'line', 'equipment', 'service']

export const masterKindLabel = (kind: MasterKind) =>
  ({ material: t('物料'), line: t('产线'), equipment: t('设备'), service: t('服务') })[kind]

/** A typical code / name pair, shown as placeholder and in the empty state. */
export const masterKindExample = (kind: MasterKind) =>
  ({
    material: { code: 'SUS304', name: t('不锈钢 304') },
    line: { code: 'L1', name: t('一号装配线') },
    equipment: { code: 'CMB-1/EFEM', name: 'EFEM' },
    service: { code: 'mira', name: 'Mira' },
  })[kind]
