"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import ChatPanel from "@/components/ChatPanel";
import DocumentSidebar from "@/components/DocumentSidebar";
import { LogoIcon } from "@/components/Icons";
import SourcePanel from "@/components/SourcePanel";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type {
  ChatMessage,
  Citation,
  ConversationSummary,
  DocumentSummary,
  UploadResult,
} from "@/lib/types";

/** Temporary client-side id for a message that has no server id yet. */
const localId = () => `local-${Math.random().toString(36).slice(2)}`;

export default function ChatWorkspace() {
  const router = useRouter();
  const { user, token, loading, signOut } = useAuth();

  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [documentsLoading, setDocumentsLoading] = useState(true);
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadResults, setUploadResults] = useState<UploadResult[]>([]);
  const [activeCitation, setActiveCitation] = useState<Citation | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  // A 401 anywhere means the token is dead: clear it and bounce to /login.
  const handleApiError = useCallback(
    (error: unknown, fallback: string): string => {
      if (error instanceof ApiError) {
        if (error.isUnauthorized) {
          signOut();
          return "Your session expired. Please sign in again.";
        }
        return error.message;
      }
      return fallback;
    },
    [signOut],
  );

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  const refreshDocuments = useCallback(async () => {
    if (!token) return;
    try {
      setDocuments(await api.listDocuments(token));
    } catch (error) {
      setBanner(handleApiError(error, "Could not load your documents."));
    } finally {
      setDocumentsLoading(false);
    }
  }, [token, handleApiError]);

  const refreshConversations = useCallback(async () => {
    if (!token) return;
    try {
      setConversations(await api.listConversations(token));
    } catch {
      /* the sidebar list is non-critical; stay quiet */
    }
  }, [token]);

  useEffect(() => {
    if (!token) return;
    void refreshDocuments();
    void refreshConversations();
  }, [token, refreshDocuments, refreshConversations]);

  // Abort any in-flight stream when the workspace unmounts.
  useEffect(() => () => abortRef.current?.abort(), []);

  // ---------------------------------------------------------------- documents
  async function handleUpload(files: File[]) {
    if (!token) return;
    setUploading(true);
    setUploadResults([]);
    try {
      const response = await api.uploadDocuments(token, files);
      setUploadResults(response.results);
      await refreshDocuments();
    } catch (error) {
      setBanner(handleApiError(error, "Upload failed. Please try again."));
    } finally {
      setUploading(false);
    }
  }

  async function handleDeleteDocument(documentId: string) {
    if (!token) return;
    const previous = documents;
    setDocuments((current) => current.filter((document) => document.id !== documentId));
    setSelectedDocumentIds((current) => current.filter((id) => id !== documentId));
    try {
      await api.deleteDocument(token, documentId);
    } catch (error) {
      setDocuments(previous); // roll the optimistic removal back
      setBanner(handleApiError(error, "Could not delete that document."));
    }
  }

  function toggleDocument(documentId: string) {
    setSelectedDocumentIds((current) =>
      current.includes(documentId)
        ? current.filter((id) => id !== documentId)
        : [...current, documentId],
    );
  }

  // ------------------------------------------------------------ conversations
  async function openConversation(id: string) {
    if (!token) return;
    abortRef.current?.abort();
    setActiveCitation(null);
    try {
      const detail = await api.getConversation(token, id);
      setConversationId(detail.id);
      setMessages(detail.messages);
    } catch (error) {
      setBanner(handleApiError(error, "Could not open that conversation."));
    }
  }

  async function handleDeleteConversation(id: string) {
    if (!token) return;
    try {
      await api.deleteConversation(token, id);
      setConversations((current) => current.filter((item) => item.id !== id));
      if (id === conversationId) startNewChat();
    } catch (error) {
      setBanner(handleApiError(error, "Could not delete that conversation."));
    }
  }

  function startNewChat() {
    abortRef.current?.abort();
    setConversationId(null);
    setMessages([]);
    setActiveCitation(null);
  }

  // --------------------------------------------------------------------- chat
  async function sendMessage() {
    if (!token || sending) return;
    const question = input.trim();
    if (!question) return;

    const assistantId = localId();
    setInput("");
    setSending(true);
    setBanner(null);
    setMessages((current) => [
      ...current,
      {
        id: localId(),
        role: "user",
        content: question,
        citations: [],
        created_at: new Date().toISOString(),
      },
      {
        id: assistantId,
        role: "assistant",
        content: "",
        citations: [],
        created_at: new Date().toISOString(),
        streaming: true,
      },
    ]);

    const patchAssistant = (patch: Partial<ChatMessage>) =>
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId ? { ...message, ...patch } : message,
        ),
      );

    const controller = new AbortController();
    abortRef.current = controller;

    let streamed = "";
    let citations: Citation[] = [];

    try {
      for await (const event of api.streamChat({
        token,
        message: question,
        conversationId,
        documentIds: selectedDocumentIds.length > 0 ? selectedDocumentIds : null,
        signal: controller.signal,
      })) {
        switch (event.type) {
          case "meta":
            setConversationId(event.conversationId);
            break;
          case "sources":
            citations = event.citations;
            patchAssistant({ citations });
            break;
          case "token":
            streamed += event.text;
            patchAssistant({ content: streamed });
            break;
          case "done":
            // Keep only the sources the model actually cited.
            patchAssistant({
              streaming: false,
              citations: citations.filter((citation) =>
                event.usedMarkers.includes(citation.marker),
              ),
            });
            break;
          case "error":
            patchAssistant({ streaming: false, error: event.detail });
            break;
        }
      }
    } catch (error) {
      if (controller.signal.aborted) {
        patchAssistant({ streaming: false, content: streamed || "(stopped)" });
      } else {
        patchAssistant({
          streaming: false,
          error: handleApiError(error, "Could not reach the server."),
        });
      }
    } finally {
      // Guard against a stream that ended without a `done` event.
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId ? { ...message, streaming: false } : message,
        ),
      );
      setSending(false);
      abortRef.current = null;
      void refreshConversations();
    }
  }

  if (loading || !user) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <LogoIcon className="h-8 w-8 animate-pulse text-accent-400" />
      </main>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <div className="hidden h-full shrink-0 md:block">
        <DocumentSidebar
          email={user.email}
          documents={documents}
          documentsLoading={documentsLoading}
          selectedDocumentIds={selectedDocumentIds}
          conversations={conversations}
          activeConversationId={conversationId}
          uploading={uploading}
          uploadResults={uploadResults}
          onUpload={handleUpload}
          onDismissUploadResults={() => setUploadResults([])}
          onToggleDocument={toggleDocument}
          onDeleteDocument={handleDeleteDocument}
          onSelectConversation={openConversation}
          onDeleteConversation={handleDeleteConversation}
          onNewChat={startNewChat}
          onSignOut={signOut}
        />
      </div>

      <main className="flex h-full min-w-0 flex-1 flex-col">
        {banner && (
          <div className="flex items-center justify-between gap-3 border-b border-amber-500/25 bg-amber-500/10 px-4 py-2 text-sm text-amber-200">
            <span>{banner}</span>
            <button
              type="button"
              onClick={() => setBanner(null)}
              className="text-xs underline-offset-4 hover:underline"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Compact document strip for narrow screens, where the sidebar is hidden. */}
        <div className="border-b border-white/10 px-4 py-2 md:hidden">
          <MobileHeader
            documentCount={documents.length}
            uploading={uploading}
            onUpload={handleUpload}
            onSignOut={signOut}
          />
        </div>

        <ChatPanel
          messages={messages}
          input={input}
          sending={sending}
          hasDocuments={documents.length > 0}
          activeCitationId={activeCitation?.chunk_id ?? null}
          onInputChange={setInput}
          onSubmit={sendMessage}
          onStop={() => abortRef.current?.abort()}
          onCitationClick={(citation) =>
            setActiveCitation((current) =>
              current?.chunk_id === citation.chunk_id ? null : citation,
            )
          }
        />
      </main>

      <SourcePanel citation={activeCitation} onClose={() => setActiveCitation(null)} />
    </div>
  );
}

function MobileHeader({
  documentCount,
  uploading,
  onUpload,
  onSignOut,
}: {
  documentCount: number;
  uploading: boolean;
  onUpload: (files: File[]) => void;
  onSignOut: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="flex items-center gap-2">
        <LogoIcon className="h-5 w-5 text-accent-400" />
        <span className="text-sm font-semibold text-white">DocuMind</span>
        <span className="text-xs text-ink-500">{documentCount} docs</span>
      </div>
      <div className="flex items-center gap-2">
        <label className="cursor-pointer text-xs font-medium text-accent-400">
          {uploading ? "Uploading…" : "Upload PDF"}
          <input
            type="file"
            accept="application/pdf,.pdf"
            multiple
            className="sr-only"
            disabled={uploading}
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              if (files.length > 0) onUpload(files);
              event.target.value = "";
            }}
          />
        </label>
        <button
          type="button"
          onClick={onSignOut}
          className="text-xs text-ink-400 hover:text-ink-200"
        >
          Sign out
        </button>
      </div>
    </div>
  );
}
