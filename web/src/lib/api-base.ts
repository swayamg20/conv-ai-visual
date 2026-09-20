export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export function resolveApiBase(apiUrl?: string): string {
  return apiUrl ?? API_BASE;
}
