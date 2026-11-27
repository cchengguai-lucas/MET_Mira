import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub

afterEach(() => {
  document.body.innerHTML = ''
})
