import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

const PlatformIcon = ({ platform }: { platform: string }) => {
  if (platform === "tiktok") return <span className="mr-2">🎵</span>;
  if (platform === "youtube") return <span className="mr-2">▶️</span>;
  if (platform === "facebook") return <span className="mr-2">📘</span>;
  return null;
};

const StatusBadge = ({ status }: { status: string }) => {
  const colors: any = {
    healthy: "bg-green-100 text-green-800",
    limited: "bg-yellow-100 text-yellow-800",
    banned: "bg-red-100 text-red-800"
  };
  return (
    <span className={`px-2 py-1 rounded text-xs font-semibold ${colors[status] || "bg-gray-100 text-gray-800"}`}>
      {status ? status.toUpperCase() : "UNKNOWN"}
    </span>
  );
};

export function AccountsPage() {
  const queryClient = useQueryClient();
  const [platform, setPlatform] = useState("tiktok");
  const [accountHandle, setAccountHandle] = useState("");
  const [proxyUrl, setProxyUrl] = useState("");

  const accountsQuery = useQuery({
    queryKey: ["accounts"],
    queryFn: () => fetch("/api/v1/accounts").then(res => res.json())
  });

  const createMutation = useMutation({
    mutationFn: (data: any) => fetch("/api/v1/accounts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data)
    }).then(res => res.json()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
      setAccountHandle("");
      setProxyUrl("");
    }
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => fetch(`/api/v1/accounts/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["accounts"] })
  });

  const testConnectionMutation = useMutation({
    mutationFn: (id: string) => fetch(`/api/v1/accounts/${id}/health`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["accounts"] })
  });

  return (
    <div className="p-4 space-y-6">
      <h1 className="text-2xl font-bold">Accounts Management</h1>
      
      <div className="border p-4 rounded space-y-4 max-w-md bg-white shadow-sm">
        <h2 className="font-semibold text-lg">Add Account</h2>
        <div className="flex flex-col space-y-3">
          <select className="border border-gray-300 p-2 rounded" value={platform} onChange={e => setPlatform(e.target.value)}>
            <option value="tiktok">TikTok</option>
            <option value="youtube">YouTube</option>
            <option value="facebook">Facebook</option>
          </select>
          <input className="border border-gray-300 p-2 rounded" placeholder="Account Handle" value={accountHandle} onChange={e => setAccountHandle(e.target.value)} />
          <input className="border border-gray-300 p-2 rounded" placeholder="Proxy URL (optional)" value={proxyUrl} onChange={e => setProxyUrl(e.target.value)} />
          <button className="bg-blue-600 hover:bg-blue-700 text-white font-medium p-2 rounded shadow-sm" onClick={() => createMutation.mutate({ platform, account_handle: accountHandle, proxy_url: proxyUrl })}>
            {createMutation.isPending ? "Adding..." : "Add Account"}
          </button>
        </div>
      </div>

      <div>
        <h2 className="font-semibold text-lg mb-3">Account List</h2>
        <table className="min-w-full border bg-white shadow-sm rounded overflow-hidden">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="p-3 text-left font-semibold text-gray-700">Platform</th>
              <th className="p-3 text-left font-semibold text-gray-700">Handle</th>
              <th className="p-3 text-left font-semibold text-gray-700">Status</th>
              <th className="p-3 text-right font-semibold text-gray-700">Actions</th>
            </tr>
          </thead>
          <tbody>
            {accountsQuery.data?.items?.map((acc: any) => (
              <tr key={acc.id} className="border-b hover:bg-gray-50">
                <td className="p-3 flex items-center font-medium text-gray-800"><PlatformIcon platform={acc.platform} /> <span className="capitalize">{acc.platform}</span></td>
                <td className="p-3 text-gray-600">{acc.account_handle}</td>
                <td className="p-3"><StatusBadge status={acc.status} /></td>
                <td className="p-3 text-right space-x-2">
                  <button 
                    className="text-sm bg-gray-100 hover:bg-gray-200 text-gray-800 px-3 py-1.5 rounded font-medium border border-gray-200" 
                    onClick={() => testConnectionMutation.mutate(acc.id)}
                    disabled={testConnectionMutation.isPending}
                  >
                    Test
                  </button>
                  <button 
                    className="text-sm bg-red-50 hover:bg-red-100 text-red-700 px-3 py-1.5 rounded font-medium border border-red-100" 
                    onClick={() => { if(confirm("Delete account?")) deleteMutation.mutate(acc.id); }}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            )) || <tr><td colSpan={4} className="p-4 text-center text-gray-500">Loading / No Data</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
