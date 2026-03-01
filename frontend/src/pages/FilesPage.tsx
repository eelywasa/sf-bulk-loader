import { EmptyState } from '../components/ui'

export default function FilesPage() {
  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Input Files</h1>
        <p className="mt-1 text-sm text-gray-500">
          Browse and preview CSV files in the input directory.
        </p>
      </div>

      <EmptyState
        title="No input files found"
        description="Place CSV files in the /data/input directory to see them here."
      />
    </div>
  )
}
