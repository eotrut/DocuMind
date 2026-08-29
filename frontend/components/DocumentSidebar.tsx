"use client";

import { useState } from "react";

import UploadDropzone from "./UploadDropzone";
import {
  ChatIcon,
  FileIcon,
  LogoIcon,
  PlusIcon,
  SpinnerIcon,
  TrashIcon,
} from "./Icons";
import { classNames, formatBytes, formatRelativeTime } from "@/lib/format";
import type {
  ConversationSummary,
  DocumentSummary,
  UploadResult,
} from "@/lib/types";

interface DocumentSidebarProps {
  email: string;
  documents: DocumentSummary[];
  documentsLoading: boolean;
  selectedDocumentIds: string[];
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  uploading: boolean;
  uploadResults: UploadResult[];
  onUpload: (files: File[]) => void;
  onDismissUploadResults: () => void;
  onToggleDocument: (documentId: string) => void;
  onDeleteDocument: (documentId: string) => void;
  onSelectConversation: (conversationId: string) => void;
  onDeleteConversation: (conversationId: string) => void;
  onNewChat: () => void;
  onSignOut: () => void;
}

export default function DocumentSidebar(props: DocumentSidebarProps) {
  const {
    email,
    documents,
    documentsLoading,
    selectedDocumentIds,
    conversations,
    activeConversationId,
    uploading,
    uploadResults,
    onUpload,
    onDismissUploadResults,
    onToggleDocument,
    onDeleteDocument,
    onSelectConversation,
    onDeleteConversation,
    onNewChat,
    onSignOut,
  } = props;

  const [tab, setTab] = useState<"documents" | "chats">("documents");
  const filtering = selectedDocumentIds.length > 0;

  return (
    <aside className="flex h-full w-full flex-col border-r border-white/10 bg-ink-900/50 lg:w-80">
      <header className="flex items-center gap-2.5 border-b border-white/10 px-4 py-3.5">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 bg-ink-950 text-accent-400">
          <LogoIcon className="h-4.5 w-4.5" />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold leading-tight text-white">DocuMind</p>
          <p className="truncate text-[11px] leading-tight text-ink-400">{email}</p>
        </div>
      </header>

      <div className="px-4 pt-4">
        <button type="button" onClick={onNewChat} className="btn-primary w-full">
          <PlusIcon />
          New chat
        </button>
      </div>

      <nav className="mt-4 flex gap-1 border-b border-white/10 px-4" aria-label="Sidebar sections">
        {(["documents", "chats"] as const).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => setTab(value)}
            className={classNames(
              "-mb-px border-b-2 px-2.5 pb-2.5 text-xs font-medium capitalize transition",
              tab === value
                ? "border-accent-500 text-white"
                : "border-transparent text-ink-400 hover:text-ink-200",
            )}
          >
            {value}
            <span className="ml-1.5 text-ink-500">
              {value === "documents" ? documents.length : conversations.length}
            </span>
          </button>
        ))}
      </nav>

      <div className="scroll-slim flex-1 overflow-y-auto p-4">
        {tab === "documents" ? (
          <div className="space-y-4">
            <UploadDropzone
              uploading={uploading}
              results={uploadResults}
              onUpload={onUpload}
              onDismissResults={onDismissUploadResults}
            />

            {filtering && (
              <p className="rounded-lg border border-accent-500/25 bg-accent-500/10 px-2.5 py-2 text-[11px] text-accent-300">
                Answers are limited to {selectedDocumentIds.length} selected document
                {selectedDocumentIds.length > 1 ? "s" : ""}. Click to unselect.
              </p>
            )}

            {documentsLoading ? (
              <p className="flex items-center gap-2 text-xs text-ink-400">
                <SpinnerIcon className="h-3.5 w-3.5" /> Loading documents…
              </p>
            ) : documents.length === 0 ? (
              <p className="text-xs leading-relaxed text-ink-400">
                No documents yet. Upload a PDF to start asking questions about it.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {documents.map((document) => {
                  const selected = selectedDocumentIds.includes(document.id);
                  return (
                    <li key={document.id}>
                      <div
                        className={classNames(
                          "group flex items-start gap-2 rounded-lg border px-2.5 py-2 transition",
                          selected
                            ? "border-accent-500/50 bg-accent-500/10"
                            : "border-white/10 bg-ink-950/40 hover:border-white/20",
                        )}
                      >
                        <button
                          type="button"
                          onClick={() => onToggleDocument(document.id)}
                          aria-pressed={selected}
                          className="flex min-w-0 flex-1 items-start gap-2 text-left"
                        >
                          <FileIcon
                            className={classNames(
                              "mt-0.5 h-4 w-4 shrink-0",
                              selected ? "text-accent-300" : "text-ink-400",
                            )}
                          />
                          <span className="min-w-0">
                            <span className="block truncate text-xs font-medium text-ink-100">
                              {document.filename}
                            </span>
                            <span className="block text-[11px] text-ink-500">
                              {document.status === "ready"
                                ? `${document.page_count} pages · ${document.chunk_count} chunks · ${formatBytes(document.size_bytes)}`
                                : document.status === "failed"
                                  ? (document.error ?? "Failed to index")
                                  : "Processing…"}
                            </span>
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={() => onDeleteDocument(document.id)}
                          aria-label={`Delete ${document.filename}`}
                          className="mt-0.5 shrink-0 rounded p-1 text-ink-500 opacity-0 transition hover:bg-red-500/15 hover:text-red-300 focus:opacity-100 group-hover:opacity-100"
                        >
                          <TrashIcon className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        ) : conversations.length === 0 ? (
          <p className="text-xs leading-relaxed text-ink-400">
            No conversations yet. Ask a question to start one.
          </p>
        ) : (
          <ul className="space-y-1">
            {conversations.map((conversation) => (
              <li key={conversation.id}>
                <div
                  className={classNames(
                    "group flex items-center gap-2 rounded-lg border px-2.5 py-2 transition",
                    conversation.id === activeConversationId
                      ? "border-accent-500/50 bg-accent-500/10"
                      : "border-transparent hover:border-white/15 hover:bg-ink-950/40",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => onSelectConversation(conversation.id)}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  >
                    <ChatIcon className="h-3.5 w-3.5 shrink-0 text-ink-400" />
                    <span className="min-w-0">
                      <span className="block truncate text-xs text-ink-100">
                        {conversation.title}
                      </span>
                      <span className="block text-[11px] text-ink-500">
                        {formatRelativeTime(conversation.updated_at)}
                      </span>
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => onDeleteConversation(conversation.id)}
                    aria-label={`Delete conversation ${conversation.title}`}
                    className="shrink-0 rounded p-1 text-ink-500 opacity-0 transition hover:bg-red-500/15 hover:text-red-300 focus:opacity-100 group-hover:opacity-100"
                  >
                    <TrashIcon className="h-3.5 w-3.5" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <footer className="border-t border-white/10 p-3">
        <button type="button" onClick={onSignOut} className="btn-ghost w-full text-xs">
          Sign out
        </button>
      </footer>
    </aside>
  );
}
