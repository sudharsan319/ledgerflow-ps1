export type DeletionPreview = {
  run_ids: string[];
  count: number;
  filenames: string[];
  approved_count: number;
  can_delete: boolean;
  reason: string | null;
  fingerprint: string;
};

export type DeletionResult = { deleted_run_ids: string[]; count: number; cleanup_pending?: boolean };

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const data = await response.json();
      if (typeof data.detail === 'string') message = data.detail;
    } catch { /* Keep the HTTP fallback when the response is not JSON. */ }
    throw new Error(message);
  }
  return response.json();
}

export function loadDeletionPreview(runId: string, signal?: AbortSignal): Promise<DeletionPreview> {
  return request(`/api/runs/${encodeURIComponent(runId)}/deletion-preview`, { signal });
}

export function deleteInvoiceUpload(runId: string, preview: DeletionPreview): Promise<DeletionResult> {
  if (!preview.can_delete || !preview.fingerprint || !preview.run_ids.includes(runId)) {
    return Promise.reject(new Error(preview.reason || 'Load the latest deletion details before confirming.'));
  }
  return request(`/api/runs/${encodeURIComponent(runId)}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_fingerprint: preview.fingerprint }),
  });
}
