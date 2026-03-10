export interface Agent {
  id: string;
  name: string;
  icon: string;
  description: string | null;
  subject: string | null;
  level: string | null;
  goals: string | null;
  learning_style: string | null;
  system_prompt: string | null;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface AgentCreatePayload {
  name: string;
  icon: string;
  description: string;
  subject: string;
  level: string;
  goals: string;
  learning_style: string;
}

export interface Session {
  id: string;
  agent_id: string;
  title: string | null;
  summary: string | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface SessionMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface SessionWithMessages extends Session {
  messages: SessionMessage[];
}

export interface Resource {
  id: string;
  agent_id: string;
  name: string;
  resource_type: string;
  chunk_count: number;
  size_bytes: number | null;
  status: string;
  created_at: string;
}

export interface TopicMastery {
  topic: string;
  chapter: string | null;
  signal_type: "understood" | "struggled" | "unclear";
  session_count: number;
}

export interface ChapterMastery {
  name: string;
  understood: number;
  struggled: number;
  unclear: number;
}

export interface MasteryData {
  topics: TopicMastery[];
  chapters: ChapterMastery[];
}
