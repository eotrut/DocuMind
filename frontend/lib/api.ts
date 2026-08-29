/**
 * Typed client for the DocuMind API.
 *
 * Every call funnels through `request()` so error handling is uniform: the
 * backend's `{ detail }` body becomes an `ApiError` carrying the HTTP status,
 * which the UI turns into a message instead of a blank screen.
 */

import type {
  ChatStreamEvent,
  Citation,
  ConversationDetail,
  ConversationSummary,
  DocumentSummary,
  TokenResponse,
  UploadResponse,
  User,
} from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }

  /** The session is gone; the UI should bounce the user to /login. */
  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isRateLimited(): boolean {
    return this.status === 429;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  token?: string | null;
  signal?: AbortSignal;
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const data: unknown = await response.json();
    if (data && typeof data === "object" && "detail" in data) {
      const detail = (data as { detail: unknown }).detail;
      if (typeof detail === "string") return detail;
      // FastAPI validation errors arrive as an array of issue objects.
      if (Array.isArray(detail) && detail.length > 0) {
        const first = detail[0] as { msg?: string };
        if (first?.msg) return first.msg.replace(/^Value error, /, "");
      }
    }
  } catch {
    /* fall through to the generic message below */
  }
  return `Request failed (${response.status})`;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, token, signal } = options;

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method,
      signal,
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(
      "Could not reach the server. Is the backend running?",
      0,
    );
  }

  if (!response.ok) throw new ApiError(await readErrorDetail(response), response.status);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// ------------------------------------------------------------------------ auth
export const signup = (email: string, password: string) =>
  request<TokenResponse>("/api/auth/signup", {
    method: "POST",
    body: { email, password },
  });

export const login = (email: string, password: string) =>
  request<TokenResponse>("/api/auth/login", {
    method: "POST",
    body: { email, password },
  });

export const fetchMe = (token: string) =>
  request<User>("/api/auth/me", { token });

// ------------------------------------------------------------------- documents
export const listDocuments = (token: string) =>
  request<DocumentSummary[]>("/api/documents", { token });

export const deleteDocument = (token: string, documentId: string) =>
  request<void>(`/api/documents/${documentId}`, { method: "DELETE", token });

export async function uploadDocuments(
  token: string,
  files: File[],
): Promise<UploadResponse> {
  const form = new FormData();
  files.forEach((file) => form.append("files", file));

  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/documents`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
  } catch {
    throw new ApiError("Could not reach the server. Is the backend running?", 0);
  }

  if (!response.ok) throw new ApiError(await readErrorDetail(response), response.status);
  return (await response.json()) as UploadResponse;
}

// ---------------------------------------------------------------- conversations
export const listConversations = (token: string) =>
  request<ConversationSummary[]>("/api/conversations", { token });

export const getConversation = (token: string, conversationId: string) =>
  request<ConversationDetail>(`/api/conversations/${conversationId}`, { token });

export const deleteConversation = (token: string, conversationId: string) =>
  request<void>(`/api/conversations/${conversationId}`, {
    method: "DELETE",
    token,
  });

// ------------------------------------------------------------------------ chat
interface ChatStreamParams {
  token: string;
  message: string;
  conversationId?: string | null;
  documentIds?: string[] | null;
  signal?: AbortSignal;
}

/**
 * Stream a RAG answer as an async iterable of typed events.
 *
 * Server-Sent Events are parsed by hand rather than with `EventSource` because
 * the endpoint is a POST that needs an Authorization header - `EventSource`
 * supports neither.
 */
export async function* streamChat(
  params: ChatStreamParams,
): AsyncGenerator<ChatStreamEvent> {
  const { token, message, conversationId, documentIds, signal } = params;

  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/chat/stream`, {
      method: "POST",
      signal,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        message,
        conversation_id: conversationId ?? null,
        document_ids: documentIds ?? null,
      }),
    });
  } catch {
    throw new ApiError("Could not reach the server. Is the backend running?", 0);
  }

  if (!response.ok) throw new ApiError(await readErrorDetail(response), response.status);
  if (!response.body) throw new ApiError("The server sent an empty response.", 502);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line; keep the trailing partial.
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        const event = parseFrame(frame);
        if (event) yield event;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseFrame(frame: string): ChatStreamEvent | null {
  let name = "";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7).trim();
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!name || !data) return null;

  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data) as Record<string, unknown>;
  } catch {
    return null;
  }

  switch (name) {
    case "meta":
      return {
        type: "meta",
        conversationId: String(payload.conversation_id ?? ""),
        userMessageId: String(payload.user_message_id ?? ""),
      };
    case "sources":
      return { type: "sources", citations: (payload.citations ?? []) as Citation[] };
    case "token":
      return { type: "token", text: String(payload.text ?? "") };
    case "done":
      return {
        type: "done",
        messageId: String(payload.message_id ?? ""),
        usedMarkers: (payload.used_markers ?? []) as number[],
      };
    case "error":
      return {
        type: "error",
        detail: String(payload.detail ?? "Something went wrong."),
        code: Number(payload.code ?? 500),
      };
    default:
      return null;
  }
}
