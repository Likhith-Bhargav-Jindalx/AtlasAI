import { useAuth } from '../../auth/AuthContext'
import { useLlmUsageSummary } from '../../lib/llmUsage'

export function CostDashboardPage() {
  const { user } = useAuth()
  const { data, isLoading, isError, error } = useLlmUsageSummary(user!.org_id)

  if (isError) {
    return (
      <div className="p-9 text-[13px] text-atlas-diffOldText">
        {(error as Error).message}
      </div>
    )
  }

  if (isLoading || !data) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  const maxCost = Math.max(...data.by_day.map((d) => d.cost_usd), 0.0001)

  return (
    <div className="p-9 pb-16 flex flex-col gap-7 max-w-[1180px]">
      <div className="flex flex-col gap-1.5">
        <h1 className="font-display text-[30px] font-semibold tracking-tight m-0">Cost dashboard</h1>
        <div className="text-sm text-atlas-body">LLM spend across authoring and healing runs.</div>
      </div>

      <div className="grid grid-cols-4 gap-4">
        <div className="bg-atlas-card border border-atlas-border rounded-2xl p-5 flex flex-col gap-1.5">
          <div className="text-[11px] tracking-wider uppercase text-atlas-muted">Calls</div>
          <div className="text-2xl font-semibold tracking-tight">{data.total_calls}</div>
        </div>
        <div className="bg-atlas-card border border-atlas-border rounded-2xl p-5 flex flex-col gap-1.5">
          <div className="text-[11px] tracking-wider uppercase text-atlas-muted">Input tokens</div>
          <div className="text-2xl font-semibold tracking-tight">{data.total_input_tokens.toLocaleString()}</div>
        </div>
        <div className="bg-atlas-card border border-atlas-border rounded-2xl p-5 flex flex-col gap-1.5">
          <div className="text-[11px] tracking-wider uppercase text-atlas-muted">Output tokens</div>
          <div className="text-2xl font-semibold tracking-tight">{data.total_output_tokens.toLocaleString()}</div>
        </div>
        <div className="bg-atlas-card border border-atlas-border rounded-2xl p-5 flex flex-col gap-1.5">
          <div className="text-[11px] tracking-wider uppercase text-atlas-muted">Total spend</div>
          <div className="text-2xl font-semibold tracking-tight">${data.total_cost_usd.toFixed(2)}</div>
        </div>
      </div>

      <div className="bg-atlas-card border border-atlas-border rounded-2xl p-6 flex flex-col gap-3">
        <div className="text-[11px] tracking-wider uppercase text-atlas-muted">Spend by day</div>
        {data.by_day.map((day) => (
          <div key={day.date} className="flex items-center gap-3">
            <div className="text-[12.5px] text-atlas-body w-24 flex-none">{day.date}</div>
            <div className="flex-1 h-5 bg-atlas-bg rounded-full overflow-hidden">
              <div
                className="h-full bg-atlas-primary rounded-full"
                style={{ width: `${(day.cost_usd / maxCost) * 100}%` }}
              />
            </div>
            <div className="text-[12.5px] text-atlas-body w-16 flex-none text-right">${day.cost_usd.toFixed(2)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
