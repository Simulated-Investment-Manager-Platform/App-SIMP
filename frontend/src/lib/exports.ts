import { toast } from "sonner";

/** POST /exports and save the watermarked file. Raw CRSP/BMLL tables are refused server-side. */
export async function exportTable(runId: string, table: string, format: "csv" | "parquet" = "csv") {
  const res = await fetch("/api/exports", {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ run_id: runId, table, format }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    toast.error(`Export refused: ${body.detail ?? res.statusText}`);
    return;
  }
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `simp_${table}_${runId}.${format}`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast.success(`Exported ${table} (watermarked)`);
}
