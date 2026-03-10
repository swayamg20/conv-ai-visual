import { getToken } from "@/hooks/use-auth";
import type { Agent, AgentCreatePayload, Session, SessionWithMessages, Resource, MasteryData } from "./types";

function authHeaders(): Record<string, string> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  return headers;
}

export async function fetchAgents(): Promise<Agent[]> {
  const res = await fetch("/api/agents", { headers: authHeaders() });
  if (!res.ok) throw new Error("Failed to fetch agents");
  return res.json();
}

export async function fetchAgent(agentId: string): Promise<Agent> {
  const res = await fetch(`/api/agents/${agentId}`, { headers: authHeaders() });
  if (!res.ok) throw new Error("Failed to fetch agent");
  return res.json();
}

export async function createAgent(payload: AgentCreatePayload): Promise<Agent> {
  const res = await fetch("/api/agents", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "Failed to create agent");
  }
  return res.json();
}

export async function deleteAgent(agentId: string): Promise<void> {
  const res = await fetch(`/api/agents/${agentId}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error("Failed to delete agent");
}

// --- Sessions ---

export async function fetchSessions(agentId?: string): Promise<Session[]> {
  const url = agentId ? `/api/sessions?agent_id=${agentId}` : "/api/sessions";
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) throw new Error("Failed to fetch sessions");
  return res.json();
}

export async function createSession(agentId: string): Promise<Session> {
  const res = await fetch("/api/sessions", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ agent_id: agentId }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "Failed to create session");
  }
  return res.json();
}

export async function fetchSession(sessionId: string): Promise<SessionWithMessages> {
  const res = await fetch(`/api/sessions/${sessionId}`, { headers: authHeaders() });
  if (!res.ok) throw new Error("Failed to fetch session");
  return res.json();
}

export async function endSession(sessionId: string): Promise<void> {
  await fetch(`/api/sessions/${sessionId}/end`, {
    method: "POST",
    headers: authHeaders(),
  }).catch(() => {
    // Fire and forget — don't block navigation
  });
}

// --- Resources ---

export async function fetchResources(agentId: string): Promise<Resource[]> {
  const res = await fetch(`/api/agents/${agentId}/resources`, { headers: authHeaders() });
  if (!res.ok) throw new Error("Failed to fetch resources");
  return res.json();
}

export async function uploadResource(agentId: string, file: File): Promise<Resource> {
  const token = getToken();
  const formData = new FormData();
  formData.append("file", file);

  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`/api/agents/${agentId}/resources/upload`, {
    method: "POST",
    headers,
    body: formData,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "Failed to upload resource");
  }
  return res.json();
}

export async function addResourceUrl(agentId: string, url: string): Promise<Resource> {
  const res = await fetch(`/api/agents/${agentId}/resources/url`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ url }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "Failed to add resource URL");
  }
  return res.json();
}

export async function deleteResource(agentId: string, resourceId: string): Promise<void> {
  const res = await fetch(`/api/agents/${agentId}/resources/${resourceId}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error("Failed to delete resource");
}

// --- Mastery ---

export async function fetchMastery(agentId: string): Promise<MasteryData> {
  const res = await fetch(`/api/agents/${agentId}/mastery`, { headers: authHeaders() });
  if (!res.ok) throw new Error("Failed to fetch mastery data");
  return res.json();
}
