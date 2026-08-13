const PILL_COLORS: Record<string, { bg: string; fg: string }> = {
  draft: { bg: '#f4f5f4', fg: '#78786f' },
  captured: { bg: '#edf5f3', fg: '#144e48' },
  failed: { bg: '#fdf0f0', fg: '#8a3a3a' },
  discarded: { bg: '#f4f5f4', fg: '#78786f' },
  open: { bg: '#fdf6e7', fg: '#8a6a1f' },
  submitted: { bg: '#edf5f3', fg: '#144e48' },
  fulfilled: { bg: '#144e48', fg: '#ffffff' },
  cancelled: { bg: '#f4f5f4', fg: '#78786f' },
  certified: { bg: '#144e48', fg: '#ffffff' },
  active: { bg: '#144e48', fg: '#ffffff' },
  deprecated: { bg: '#f4f5f4', fg: '#78786f' },
  pending_approval: { bg: '#fdf6e7', fg: '#8a6a1f' },
  approved: { bg: '#edf5f3', fg: '#144e48' },
  rejected: { bg: '#fdf0f0', fg: '#8a3a3a' },
  auto_approved: { bg: '#edf5f3', fg: '#144e48' },
  running: { bg: '#fdf6e7', fg: '#8a6a1f' },
  waiting_for_developer: { bg: '#fdf6e7', fg: '#8a6a1f' },
  completed: { bg: '#144e48', fg: '#ffffff' },
  partially_certified: { bg: '#fdf6e7', fg: '#8a6a1f' },
}

export function StatusPill({ status }: { status: string }) {
  const colors = PILL_COLORS[status] ?? PILL_COLORS.draft
  const label = status.replace(/_/g, ' ')
  return (
    <span
      className="inline-flex items-center text-[11.5px] px-[11px] py-1 rounded-full capitalize"
      style={{ background: colors.bg, color: colors.fg }}
    >
      {label}
    </span>
  )
}
