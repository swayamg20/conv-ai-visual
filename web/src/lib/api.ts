import { getToken } from "@/hooks/use-auth";
import type { Agent, AgentCreatePayload, Session, SessionWithMessages, Resource, MasteryData } from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function authHeaders(): Record<string, string> {
  const token = getToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...opts,
    headers: { ...authHeaders(), ...opts.headers },
  });
  if (!res.ok) {
    let message = `Request failed: ${res.status}`;
    try {
      const body = await res.json();
      message = body.detail || body.error || body.message || message;
    } catch {
      // Response body is not JSON — use status text
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export async function fetchAgents(): Promise<Agent[]> {
  const data = await request<{ agents: Agent[] }>("/api/agents");
  return data.agents;
}

export async function fetchAgent(agentId: string): Promise<Agent> {
  return request<Agent>(`/api/agents/${agentId}`);
}

export async function createAgent(payload: AgentCreatePayload): Promise<Agent> {
  return request<Agent>("/api/agents", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteAgent(agentId: string): Promise<void> {
  await request<{ status: string }>(`/api/agents/${agentId}`, { method: "DELETE" });
}

// --- Sessions ---

export async function fetchSessions(agentId?: string): Promise<Session[]> {
  const path = agentId ? `/api/sessions?agent_id=${agentId}` : "/api/sessions";
  return request<Session[]>(path);
}

export async function createSession(agentId: string): Promise<Session> {
  return request<Session>("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ agent_id: agentId }),
  });
}

export async function fetchSession(sessionId: string): Promise<SessionWithMessages> {
  return request<SessionWithMessages>(`/api/sessions/${sessionId}`);
}

export async function endSession(sessionId: string): Promise<void> {
  await request<{ status: string }>(`/api/sessions/${sessionId}/end`, { method: "POST" }).catch(() => {
    // Fire and forget — don't block navigation
  });
}

// --- Resources ---

export async function fetchResources(agentId: string): Promise<Resource[]> {
  return request<Resource[]>(`/api/agents/${agentId}/resources`);
}

export async function uploadResource(agentId: string, file: File): Promise<Resource> {
  const token = getToken();
  const formData = new FormData();
  formData.append("file", file);

  // Omit Content-Type so the browser sets the correct multipart boundary
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}/api/agents/${agentId}/resources/upload`, {
    method: "POST",
    headers,
    body: formData,
  });
  if (!res.ok) {
    let message = "Failed to upload resource";
    try {
      const body = await res.json();
      message = body.detail || body.error || message;
    } catch {
      // Response body is not JSON
    }
    throw new Error(message);
  }
  return res.json() as Promise<Resource>;
}

export async function addResourceUrl(agentId: string, url: string): Promise<Resource> {
  return request<Resource>(`/api/agents/${agentId}/resources/url`, {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export async function deleteResource(agentId: string, resourceId: string): Promise<void> {
  await request<{ status: string }>(`/api/agents/${agentId}/resources/${resourceId}`, { method: "DELETE" });
}

// --- Mastery ---

export async function fetchMastery(agentId: string): Promise<MasteryData> {
  return request<MasteryData>(`/api/agents/${agentId}/mastery`);
}
