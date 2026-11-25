import { Fragment, useState } from 'react'
import { LOB_TABS, type Lob } from '../../lib/recordingFormSchema'
import { useAllFormOptions, useCreateFormOption, useUpdateFormOption, type FormOptionCategory } from '../../lib/formOptions'
import { useAllSubscenarios, useCreateSubscenario, useUpdateSubscenario } from '../../lib/subscenarios'

const CATEGORY_TABS: { category: FormOptionCategory; label: string }[] = [
  { category: 'client', label: 'Client' },
  { category: 'action', label: 'Action' },
  { category: 'ehr', label: 'EHR' },
]

function SubscenarioManager({ actionId, actionLabel }: { actionId: string; actionLabel: string }) {
  const [newLabel, setNewLabel] = useState('')
  const { data: subscenarios = [] } = useAllSubscenarios(actionId)
  const createSubscenario = useCreateSubscenario()
  const updateSubscenario = useUpdateSubscenario()

  function handleAdd() {
    const label = newLabel.trim()
    if (!label) return
    createSubscenario.mutate({ action_id: actionId, label }, { onSuccess: () => setNewLabel('') })
  }

  return (
    <div className="flex flex-col gap-3 pl-4 py-3 bg-atlas-bg">
      <table className="w-full text-[12.5px] border border-atlas-border rounded-[10px] overflow-hidden">
        <thead>
          <tr className="bg-atlas-card text-left">
            <th className="px-3 py-1.5">Subscenario</th>
            <th className="px-3 py-1.5">Active</th>
          </tr>
        </thead>
        <tbody>
          {subscenarios.map((subscenario) => (
            <tr key={subscenario.id} className="border-t border-atlas-border">
              <td className="px-3 py-1.5">{subscenario.label}</td>
              <td className="px-3 py-1.5">
                <input
                  type="checkbox"
                  aria-label={`Deactivate ${subscenario.label}`}
                  checked={subscenario.active}
                  onChange={(e) => updateSubscenario.mutate({ id: subscenario.id, active: e.target.checked })}
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
          placeholder="New subscenario label"
          className="px-3 py-2 rounded-[10px] border border-atlas-border outline-none text-[12.5px] flex-1"
        />
        <button
          onClick={handleAdd}
          disabled={!newLabel.trim() || createSubscenario.isPending}
          className="bg-atlas-primary text-white rounded-full px-3 text-[12.5px] font-medium disabled:opacity-60"
        >
          Add subscenario
        </button>
      </div>
      <div className="text-[11px] text-atlas-muted">Subscenarios for {actionLabel}</div>
    </div>
  )
}

export function FormOptionsPage() {
  const [lob, setLob] = useState<Lob>('AR')
  const [category, setCategory] = useState<FormOptionCategory>('action')
  const [newLabel, setNewLabel] = useState('')
  const [expandedActionId, setExpandedActionId] = useState<string | null>(null)

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
            {category === 'action' && <th className="px-3 py-2" />}
          </tr>
        </thead>
        <tbody>
          {options.map((option) => (
            <Fragment key={option.id}>
              <tr className="border-t border-atlas-border">
                <td className="px-3 py-2">{option.label}</td>
                <td className="px-3 py-2">
                  <input
                    type="checkbox"
                    aria-label={`Deactivate ${option.label}`}
                    checked={option.active}
                    onChange={(e) => updateOption.mutate({ id: option.id, active: e.target.checked })}
                  />
                </td>
                {category === 'action' && (
                  <td className="px-3 py-2">
                    <button
                      aria-label={`Manage subscenarios for ${option.label}`}
                      onClick={() => setExpandedActionId(expandedActionId === option.id ? null : option.id)}
                      className="text-[12px] text-atlas-primary font-medium"
                    >
                      {expandedActionId === option.id ? 'Hide' : 'Subscenarios'}
                    </button>
                  </td>
                )}
              </tr>
              {category === 'action' && expandedActionId === option.id && (
                <tr>
                  <td colSpan={3} className="p-0">
                    <SubscenarioManager actionId={option.id} actionLabel={option.label} />
                  </td>
                </tr>
              )}
            </Fragment>
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
