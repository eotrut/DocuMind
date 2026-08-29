/** Shapes mirroring the backend's Pydantic schemas. */

export type DocumentStatus = "processing" | "ready" | "failed";

export interface User {
  id: string;
  email: string;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  user: User;
}

export interface DocumentSummary {
  id: string;
  filename: string;
  size_bytes: number;
  page_count: number;
  chunk_count: number;
  status: DocumentStatus;
  error: string | null;
  created_at: string;
}

export interface UploadResult {
  filename: string;
  ok: boolean;
  document: DocumentSummary | null;
  error: string | null;
}

export interface UploadResponse {
  results: UploadResult[];
}

export interface Citation {
  marker: number;
  chunk_id: string;
  document_id: string;
  filename: string;
  page_number: number;
  chunk_index: number;
  score: number;
  snippet: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  created_at: string;
  /** Client-side only: the answer is still streaming in. */
  streaming?: boolean;
  /** Client-side only: a terminal error replaced the answer. */
  error?: string | null;
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ChatMessage[];
}

/** Events emitted by `POST /api/chat/stream`. */
export type ChatStreamEvent =
  | { type: "meta"; conversationId: string; userMessageId: string }
  | { type: "sources"; citations: Citation[] }
  | { type: "token"; text: string }
  | { type: "done"; messageId: string; usedMarkers: number[] }
  | { type: "error"; detail: string; code: number };
