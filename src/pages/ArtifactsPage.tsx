import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

export function ArtifactsPage() {
  const queryClient = useQueryClient();
  const artifactsQuery = useQuery({
    queryKey: ["artifacts"],
    queryFn: () => fetch("/api/v1/artifacts").then(res => res.json())
  });

  const updateStatusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string, status: string }) => 
      fetch(`/api/v1/artifacts/${id}/status`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status })
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["artifacts"] })
  });

  return (
    <div className="p-4 space-y-6">
      <h1 className="text-2xl font-bold">Artifacts Review</h1>
      <table className="min-w-full border bg-white shadow-sm rounded overflow-hidden">
        <thead className="bg-gray-50 border-b">
          <tr>
            <th className="p-3 text-left font-semibold text-gray-700">Preview</th>
            <th className="p-3 text-left font-semibold text-gray-700">Details</th>
            <th className="p-3 text-left font-semibold text-gray-700">Status</th>
            <th className="p-3 text-right font-semibold text-gray-700">Actions</th>
          </tr>
        </thead>
        <tbody>
          {artifactsQuery.data?.items?.map((art: any) => (
            <tr key={art.id} className="border-b hover:bg-gray-50">
              <td className="p-3 w-64">
                {art.artifact_type === "video" ? (
                  <video controls width="250" src={art.storage_uri} className="rounded border bg-black" />
                ) : art.artifact_type === "image" ? (
                  <img src={art.storage_uri} width="250" className="rounded border" alt="artifact" />
                ) : (
                  <span className="text-gray-500 italic text-sm">No preview available</span>
                )}
              </td>
              <td className="p-3">
                <div className="text-sm"><span className="font-semibold text-gray-600">ID:</span> {art.id}</div>
                <div className="text-sm"><span className="font-semibold text-gray-600">Type:</span> <span className="uppercase font-mono text-xs bg-gray-100 px-1 rounded">{art.artifact_type}</span></div>
                <div className="text-sm mt-1 text-gray-500 break-all w-64" title={art.storage_uri}>{art.storage_uri}</div>
              </td>
              <td className="p-3">
                <span className={`px-2 py-1 rounded text-xs font-bold uppercase tracking-wider ${
                  art.status === "approved" ? "bg-green-100 text-green-800" :
                  art.status === "rejected" ? "bg-red-100 text-red-800" :
                  "bg-yellow-100 text-yellow-800"
                }`}>
                  {art.status || "pending"}
                </span>
              </td>
              <td className="p-3 text-right space-x-2">
                <button 
                  className="bg-green-50 hover:bg-green-100 border border-green-200 text-green-700 px-3 py-1.5 rounded text-sm font-medium"
                  onClick={() => updateStatusMutation.mutate({ id: art.id, status: "approved" })}
                  disabled={updateStatusMutation.isPending}
                >
                  Approve
                </button>
                <button 
                  className="bg-red-50 hover:bg-red-100 border border-red-200 text-red-700 px-3 py-1.5 rounded text-sm font-medium"
                  onClick={() => updateStatusMutation.mutate({ id: art.id, status: "rejected" })}
                  disabled={updateStatusMutation.isPending}
                >
                  Reject
                </button>
              </td>
            </tr>
          )) || <tr><td colSpan={4} className="p-4 text-center text-gray-500">Loading / No Data</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
