"use client";

import { useQuery } from "@tanstack/react-query";
import { parseAsString, useQueryState } from "nuqs";

import { api, getJson, unwrap } from "@/lib/api/client";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: async () => unwrap(await api.GET("/api/me")) });
}

export function useRuns(params: { fund_id?: string; status?: string; study_id?: string } = {}, refetch?: number) {
  return useQuery({
    queryKey: ["runs", params],
    queryFn: async () => unwrap(await api.GET("/api/runs", { params: { query: { ...params, limit: 200 } } })),
    refetchInterval: refetch,
  });
}

export function useRun(id: string | null | undefined, live = false) {
  return useQuery({
    queryKey: ["run", id],
    enabled: !!id,
    queryFn: async () => unwrap(await api.GET("/api/runs/{run_id}", { params: { path: { run_id: id! } } })),
    refetchInterval: (q) => (live && q.state.data && !["DONE", "FAILED"].includes(q.state.data.status) ? 2000 : false),
  });
}

/** The run a working screen shows: ?run= in the URL, else the latest finished run (index fund first). */
export function useSelectedRun(fundId?: string) {
  const [run, setRun] = useQueryState("run", parseAsString);
  const runs = useRuns(fundId ? { fund_id: fundId } : {});
  const done = (runs.data ?? []).filter((r) => r.status === "DONE");
  const main = (r: (typeof done)[number]) => r.fund_id === "SP500_INDEX" && !r.study_id;
  const fallback =
    done.find((r) => main(r) && r.execution_model === "T1") ?? done.find(main) ?? done.find((r) => r.fund_id === "SP500_INDEX") ?? done[0];
  return { runId: run ?? fallback?.run_id ?? null, setRun, runs: runs.data ?? [], loading: runs.isLoading };
}

export function useJson<T = any>(key: unknown[], path: string | null, params?: Record<string, any>, opts: { refetch?: number } = {}) {
  return useQuery<T>({
    queryKey: [...key, params],
    enabled: !!path,
    queryFn: () => getJson<T>(path!, params),
    refetchInterval: opts.refetch,
  });
}
