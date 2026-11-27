import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { Modal, Select } from './ui'

function ModalHarness({ onClose }: { onClose: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button onClick={() => setOpen(true)}>打开</button>
      {open && (
        <Modal
          title="编辑事项"
          onClose={() => {
            onClose()
            setOpen(false)
          }}
        >
          <input aria-label="标题" />
        </Modal>
      )}
    </>
  )
}

describe('Modal', () => {
  it('labels the dialog, closes with Escape, and restores focus', async () => {
    const onClose = vi.fn()
    render(<ModalHarness onClose={onClose} />)
    const opener = screen.getByRole('button', { name: '打开' })
    opener.focus()
    fireEvent.click(opener)

    const dialog = screen.getByRole('dialog', { name: '编辑事项' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    await waitFor(() => expect(screen.getByRole('button', { name: '关闭' })).toHaveFocus())

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
    expect(opener).toHaveFocus()
  })
})

describe('Select', () => {
  it('exposes a listbox and supports keyboard selection', () => {
    const onChange = vi.fn()
    render(
      <Select defaultValue="todo" onChange={onChange}>
        <option value="todo">待办</option>
        <option value="doing">进行中</option>
      </Select>,
    )

    const trigger = screen.getByRole('combobox')
    fireEvent.click(trigger)
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    expect(screen.getAllByRole('option')[0]).toHaveAttribute('aria-selected', 'true')

    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    fireEvent.keyDown(trigger, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith({ target: { value: 'doing' } })
  })
})
