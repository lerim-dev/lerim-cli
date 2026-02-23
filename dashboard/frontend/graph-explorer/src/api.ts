import type {
  GraphExpandRequest,
  GraphExpandResponse,
  GraphOptionsResponse,
  GraphQueryRequest,
  GraphQueryResponse,
} from "./state";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message =
      (typeof data?.error === "string" && data.error) ||
      `Request failed: ${response.status}`;
    throw new Error(message);
  }
  return data as T;
}

export async function fetchGraphOptions(): Promise<GraphOptionsResponse> {
  return requestJson<GraphOptionsResponse>("/api/memory-graph/options");
}

export async function queryGraph(payload: GraphQueryRequest): Promise<GraphQueryResponse> {
  return requestJson<GraphQueryResponse>("/api/memory-graph/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function expandGraph(payload: GraphExpandRequest): Promise<GraphExpandResponse> {
  return requestJson<GraphExpandResponse>("/api/memory-graph/expand", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
