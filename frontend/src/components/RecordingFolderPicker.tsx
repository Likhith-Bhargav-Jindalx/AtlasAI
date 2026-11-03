import { useRecordingFolders, useMoveRecordingToFolder } from '../lib/recordings'

export function RecordingFolderPicker({ recordingId, folderId }: { recordingId: string; folderId: string | null }) {
  const { data: folders } = useRecordingFolders()
  const moveToFolder = useMoveRecordingToFolder()

  return (
    <select
      value={folderId ?? ''}
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
      onChange={(e) => {
        e.stopPropagation()
        moveToFolder.mutate({ recordingId, folderId: e.target.value })
      }}
      className="text-[11.5px] px-2 py-1 rounded-full border border-atlas-border bg-white text-atlas-body"
    >
      <option value="">Unfiled</option>
      {(folders ?? []).map((f) => (
        <option key={f.id} value={f.id}>{f.name}</option>
      ))}
    </select>
  )
}
