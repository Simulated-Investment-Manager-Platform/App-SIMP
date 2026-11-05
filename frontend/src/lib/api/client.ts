import createClient from "openapi-fetch";
import type { paths } from "./schema";

/** Typed client generated from the FastAPI OpenAPI schema (`pnpm gen:api`); no hand-written types. */
export const api = createClient<paths>({ baseUrl: "", credentials: "include" });

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : ((detail as any)?.message ?? JSON.stringify(detail)));
  }
}

export function unwrap<T>(r: { data?: T; error?: unknown; response: Response }): T {
  if (r.error !== undefined || !r.response.ok) throw new ApiError(r.response.status, (r.error as any)?.detail ?? r.error);
  return r.data as T;
}

type Params = Record<string, string | number | boolean | undefined | null>;

/** GET for dynamic JSON shapes (run summaries, studies) that the schema types as free-form objects. */
export async function getJson<T = any>(path: string, params?: Params): Promise<T> {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const qs = q.toString();
  const res = await fetch(`/api${path}${qs ? `?${qs}` : ""}`, { credentials: "include" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, body?.detail ?? body);
  return body as T;
}

export async function sendJson<T = any>(method: "POST" | "PUT" | "DELETE", path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? data);
  return data as T;
}
