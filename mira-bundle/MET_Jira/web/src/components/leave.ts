/**
 * Exit animations for conditionally rendered popups (`{open && <Modal />}`).
 *
 * React removes the node right after ref cleanup, so the cleanup leaves an inert copy in its
 * place; the copy gets the `leaving` class, plays its CSS exit animation and removes itself.
 * Every close path (✕, Esc, cancel buttons, saving, route changes) animates the same way.
 */
const MAX_MS = 400
const SCROLL_SCAN_LIMIT = 4000

function copyLiveState(from: HTMLElement, to: HTMLElement) {
  const sources = from.querySelectorAll<HTMLInputElement>('input, textarea, select')
  const targets = to.querySelectorAll<HTMLInputElement>('input, textarea, select')
  sources.forEach((source, index) => {
    const target = targets[index]
    if (!target) return
    if (source.type === 'checkbox' || source.type === 'radio') target.checked = source.checked
    else if (source.type !== 'file') target.value = source.value
  })
}

function copyScroll(from: HTMLElement, to: HTMLElement) {
  const sources = [from, ...Array.from(from.querySelectorAll<HTMLElement>('*')).slice(0, SCROLL_SCAN_LIMIT)]
  const targets = [to, ...Array.from(to.querySelectorAll<HTMLElement>('*')).slice(0, SCROLL_SCAN_LIMIT)]
  sources.forEach((source, index) => {
    if (source.scrollTop || source.scrollLeft) {
      targets[index].scrollTop = source.scrollTop
      targets[index].scrollLeft = source.scrollLeft
    }
  })
}

export function leaveGhost(node: HTMLElement) {
  const parent = node.parentNode
  if (!parent || !node.isConnected || node.classList.contains('leaving')) return
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
  const ghost = node.cloneNode(true) as HTMLElement
  copyLiveState(node, ghost)
  ghost.classList.add('leaving')
  ghost.setAttribute('aria-hidden', 'true')
  ghost.inert = true
  ghost.querySelectorAll('[id]').forEach((el) => el.removeAttribute('id'))
  parent.insertBefore(ghost, node)
  copyScroll(node, ghost)
  let removed = false
  const remove = () => {
    if (removed) return
    removed = true
    ghost.remove()
  }
  ghost.addEventListener('animationend', (e) => { if (e.target === ghost) remove() })
  window.setTimeout(remove, MAX_MS)
}

/** Stable ref callback: `<div ref={leaveAnimation}>` animates out when React unmounts it. */
export function leaveAnimation(node: HTMLElement | null) {
  if (!node) return
  return () => leaveGhost(node)
}
