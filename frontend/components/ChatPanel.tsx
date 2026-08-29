"use client";

import { useEffect, useRef, type FormEvent, type KeyboardEvent } from "react";

import AnswerText from "./AnswerText";
import { AlertIcon, LogoIcon, SendIcon, SpinnerIcon } from "./Icons";
import { classNames } from "@/lib/format";
import type { ChatMessage, Citation } from "@/lib/types";

interface ChatPanelProps {
  messages: ChatMessage[];
  input: string;
  sending: boolean;
  hasDocuments: boolean;
  activeCitationId: string | null;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  onCitationClick: (citation: Citation) => void;
}

const SUGGESTIONS = [
  "Summarise the key points of this document.",
  "What are the main figures or numbers mentioned?",
  "What conclusions does the document reach?",
];

export default function ChatPanel(props: ChatPanelProps) {
  const {
    messages,
    input,
    sending,
    hasDocuments,
    activeCitationId,
    onInputChange,
    onSubmit,
    onStop,
    onCitationClick,
  } = props;

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Follow the stream, but only while the user is already near the bottom.
  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    const distanceFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight;
    if (distanceFromBottom < 220) {
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    }
  }, [messages]);

  // Grow the composer with its content, up to a sane ceiling.
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 176)}px`;
  }, [input]);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!sending && input.trim()) onSubmit();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!sending && input.trim()) onSubmit();
    }
  }

  return (
    <section className="flex h-full min-w-0 flex-1 flex-col">
      <div ref={scrollRef} className="scroll-slim flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6">
          {messages.length === 0 ? (
            <EmptyState hasDocuments={hasDocuments} onPick={onInputChange} />
          ) : (
            <ol className="space-y-6">
              {messages.map((message) => (
                <li key={message.id} className="animate-fade-in">
                  {message.role === "user" ? (
                    <div className="flex justify-end">
                      <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-accent-600 px-4 py-2.5 text-sm leading-relaxed text-white">
                        {message.content}
                      </div>
                    </div>
                  ) : (
                    <AssistantMessage
                      message={message}
                      activeCitationId={activeCitationId}
                      onCitationClick={onCitationClick}
                    />
                  )}
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>

      <form
        onSubmit={handleSubmit}
        className="border-t border-white/10 bg-ink-950/40 px-4 py-3.5 sm:px-6"
      >
        <div className="mx-auto flex w-full max-w-3xl items-end gap-2">
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            onChange={(event) => onInputChange(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              hasDocuments
                ? "Ask a question about your documents…"
                : "Upload a PDF first, then ask a question…"
            }
            className="field max-h-44 flex-1 resize-none py-2.5"
            aria-label="Your question"
          />
          {sending ? (
            <button type="button" onClick={onStop} className="btn-ghost shrink-0 py-2.5">
              Stop
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim()}
              className="btn-primary shrink-0 px-3.5 py-2.5"
              aria-label="Send question"
            >
              <SendIcon />
            </button>
          )}
        </div>
        <p className="mx-auto mt-2 w-full max-w-3xl text-[11px] text-ink-500">
          Answers are generated from your uploaded documents only. Click a citation
          number to see the exact source text.
        </p>
      </form>
    </section>
  );
}

function AssistantMessage({
  message,
  activeCitationId,
  onCitationClick,
}: {
  message: ChatMessage;
  activeCitationId: string | null;
  onCitationClick: (citation: Citation) => void;
}) {
  const waiting = message.streaming && message.content.length === 0 && !message.error;

  return (
    <div className="flex gap-3">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-ink-900 text-accent-400">
        <LogoIcon className="h-4 w-4" />
      </span>

      <div className="min-w-0 flex-1">
        {message.error ? (
          <p className="flex items-start gap-2 rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2.5 text-sm text-red-200">
            <AlertIcon className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{message.error}</span>
          </p>
        ) : waiting ? (
          <p className="flex items-center gap-2 text-sm text-ink-400">
            <SpinnerIcon className="h-3.5 w-3.5" />
            Searching your documents…
          </p>
        ) : (
          <>
            <AnswerText
              content={message.content}
              citations={message.citations}
              onCitationClick={onCitationClick}
              activeCitationId={activeCitationId}
            />
            {message.streaming && (
              <span
                className="ml-0.5 inline-block h-4 w-[2px] animate-blink bg-accent-400 align-text-bottom"
                aria-hidden="true"
              />
            )}
          </>
        )}

        {!message.streaming && message.citations.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] uppercase tracking-wide text-ink-500">
              Sources
            </span>
            {message.citations.map((citation) => (
              <button
                key={citation.chunk_id}
                type="button"
                onClick={() => onCitationClick(citation)}
                className={classNames(
                  "inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] transition",
                  activeCitationId === citation.chunk_id
                    ? "border-accent-500/60 bg-accent-500/15 text-accent-200"
                    : "border-white/10 bg-ink-950/50 text-ink-300 hover:border-white/25 hover:text-ink-100",
                )}
              >
                <span className="font-semibold text-accent-300">{citation.marker}</span>
                <span className="truncate">{citation.filename}</span>
                <span className="text-ink-500">p.{citation.page_number}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyState({
  hasDocuments,
  onPick,
}: {
  hasDocuments: boolean;
  onPick: (value: string) => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl border border-white/10 bg-ink-900 text-accent-400">
        <LogoIcon className="h-6 w-6" />
      </span>
      <h2 className="text-lg font-semibold text-white">
        {hasDocuments ? "Ask anything about your documents" : "Upload a PDF to begin"}
      </h2>
      <p className="mt-1.5 max-w-md text-sm leading-relaxed text-ink-400">
        {hasDocuments
          ? "DocuMind searches your uploads, answers from what it finds, and cites the exact page it used."
          : "Drop a PDF into the sidebar. It will be parsed, chunked and indexed so you can ask questions about its contents."}
      </p>

      {hasDocuments && (
        <div className="mt-6 flex w-full max-w-md flex-col gap-2">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => onPick(suggestion)}
              className="rounded-lg border border-white/10 bg-ink-900/50 px-3.5 py-2.5 text-left text-sm text-ink-300 transition hover:border-accent-500/40 hover:text-ink-100"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
