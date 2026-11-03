import { useState } from 'react'
import { FolderPlus, Search } from 'lucide-react'
import { useCreateRecordingFolder, useRecordingFolders } from '../lib/recordings'

interface Props {
  q: string
  onQChange: (q: string) => void
  folderId: string
  onFolderIdChange: (folderId: string) => void
}

export function RecordingLibraryToolbar({ q, onQChange, folderId, onFolderIdChange }: Props) {
  const { data: folders } = useRecordingFolders()
  const createFolder = useCreateRecordingFolder()
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [error, setError] = useState<string | null>(null)

  // The input updates on every keystroke (draft), but the search itself
  // (q, which drives the /recordings query) only fires on Enter or a click
  // on the search icon -- not per keystroke.
  const [draft, setDraft] = useState(q)
  function runSearch() {
    onQChange(draft)
  }

  function submitNewFolder() {
    const name = newName.trim()
    if (!name) return
    createFolder.mutate(name, {
      onSuccess: (folder) => {
        setCreating(false)
        setNewName('')
        setError(null)
        onFolderIdChange(folder.id)
      },
      onError: (err) => setError(err instanceof Error ? err.message : 'Could not create folder'),
    })
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2.5 flex-wrap">
        <div className="relative flex-1 min-w-[220px] max-w-[360px]">
          <button
            type="button"
            onClick={runSearch}
            aria-label="Search"
            className="absolute left-2.5 top-1/2 -translate-y-1/2 p-0.5 text-atlas-muted hover:text-atlas-primary"
          >
            <Search size={15} />
          </button>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') runSearch()
            }}
            placeholder="Search recordings…"
            className="w-full pl-9 pr-3 py-2 text-sm rounded-full border border-atlas-border bg-atlas-card outline-none focus:border-atlas-primary"
          />
        </div>

        <button
          type="button"
          onClick={runSearch}
          className="text-sm px-3 py-2 rounded-full border border-atlas-border bg-atlas-card hover:bg-atlas-rowBorder"
        >
          Search
        </button>

        <select
          value={folderId}
          onChange={(e) => onFolderIdChange(e.target.value)}
          className="text-sm px-3 py-2 rounded-full border border-atlas-border bg-atlas-card outline-none"
        >
          <option value="">All folders</option>
          <option value="unfiled">Unfiled</option>
          {(folders ?? []).map((f) => (
            <option key={f.id} value={f.id}>{f.name}</option>
          ))}
        </select>

        {creating ? (
          <div className="flex items-center gap-1.5">
            <input
              autoFocus
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submitNewFolder()
                if (e.key === 'Escape') {
                  setCreating(false)
                  setNewName('')
                  setError(null)
                }
              }}
              placeholder="Folder name"
              className="text-sm px-3 py-2 rounded-full border border-atlas-border bg-atlas-card outline-none"
            />
            <button
              onClick={submitNewFolder}
              disabled={createFolder.isPending}
              className="text-sm px-3 py-2 rounded-full bg-atlas-primary text-white disabled:opacity-50"
            >
              Add
            </button>
          </div>
        ) : (
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-1.5 text-sm px-3 py-2 rounded-full border border-atlas-border bg-atlas-card hover:bg-atlas-rowBorder"
          >
            <FolderPlus size={15} /> New folder
          </button>
        )}
      </div>
      {error && <div className="text-[12px] text-atlas-danger">{error}</div>}
    </div>
  )
}
