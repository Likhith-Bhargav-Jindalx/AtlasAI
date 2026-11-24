import { useState } from 'react'
import { LOB_TABS, type Lob } from '../../lib/recordingFormSchema'
import { useAllFormOptions, useCreateFormOption, useUpdateFormOption, type FormOptionCategory } from '../../lib/formOptions'

const CATEGORY_TABS: { category: FormOptionCategory; label: string }[] = [
  { category: 'client', label: 'Client' },
  { category: 'action', label: 'Action' },
  { category: 'ehr', label: 'EHR' },
]

export function FormOptionsPage() {
  const [lob, setLob] = useState<Lob>('AR')
  const [category, setCategory] = useState<FormOptionCategory>('action')
  const [newLabel, setNewLabel] = useState('')

  const { data: options = [] } = useAllFormOptions(lob, category)
  const createOption = useCreateFormOption()
  const updateOption = useUpdateFormOption()

  function handleAdd() {
    const label = newLabel.trim()
    if (!label) return
    createOption.mutate({ lob, category, label }, { onSuccess: () => setNewLabel('') })
  }

  return (
    <div className="p-8 flex flex-col gap-6 max-w-2xl">
      <div className="font-display text-lg font-semibold tracking-tight">Recording form options</div>

      <div className="flex gap-1">
        {LOB_TABS.map((tab) => (
          <button
            key={tab.lob}
            onClick={() => setLob(tab.lob)}
            className={`px-4 py-2 rounded-full text-[13px] font-medium border ${
              lob === tab.lob ? 'bg-atlas-primary text-white border-atlas-primary' : 'border-atlas-border text-atlas-body'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="flex gap-1">
        {CATEGORY_TABS.map((tab) => (
          <button
            key={tab.category}
            onClick={() => setCategory(tab.category)}
            className={`px-4 py-2 rounded-full text-[13px] font-medium border ${
              category === tab.category ? 'bg-atlas-primary text-white border-atlas-primary' : 'border-atlas-border text-atlas-body'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <table className="w-full text-sm border border-atlas-border rounded-[10px] overflow-hidden">
        <thead>
          <tr className="bg-atlas-bg text-left">
            <th className="px-3 py-2">Label</th>
            <th className="px-3 py-2">Active</th>
          </tr>
        </thead>
        <tbody>
          {options.map((option) => (
            <tr key={option.id} className="border-t border-atlas-border">
              <td className="px-3 py-2">{option.label}</td>
              <td className="px-3 py-2">
                <input
                  type="checkbox"
                  aria-label={`Deactivate ${option.label}`}
                  checked={option.active}
                  onChange={(e) => updateOption.mutate({ id: option.id, active: e.target.checked })}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="flex gap-2">
        <input
          value={newLabel}
          onChange={(e) => setNewLabel(e.target.value)}
          placeholder="New option label"
          className="px-3.5 py-2.5 rounded-[10px] border border-atlas-border outline-none text-sm flex-1"
        />
        <button
          onClick={handleAdd}
          disabled={!newLabel.trim() || createOption.isPending}
          className="bg-atlas-primary text-white rounded-full px-4 text-[13.5px] font-medium disabled:opacity-60"
        >
          Add
        </button>
      </div>
    </div>
  )
}
