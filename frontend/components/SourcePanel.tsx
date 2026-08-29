"use client";

import { useEffect } from "react";

import { CloseIcon, FileIcon } from "./Icons";
import type { Citation } from "@/lib/types";

interface SourcePanelProps {
  citation: Citation | null;
  onClose: () => void;
}

/**
 * Slide-over panel showing the exact chunk an answer drew from.
 *
 * This is the payoff of the whole citation pipeline: the user can check the
 * model's work against the source text, with the document and page it came from.
 */
export default function SourcePanel({ citation, onClose }: SourcePanelProps) {
  useEffect(() => {
    if (!citation) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [citation, onClose]);

  if (!citation) return null;

  const relevance = Math.max(0, Math.min(1, citation.score));

  return (
    <>
      {/* Backdrop only covers small screens, where the panel is a true overlay. */}
      <button
        type="button"
        aria-label="Close source panel"
        onClick={onClose}
        className="fixed inset-0 z-30 bg-ink-950/60 backdrop-blur-sm lg:hidden"
      />

      <aside
        role="complementary"
        aria-label="Cited source"
        className="fixed inset-y-0 right-0 z-40 flex w-full max-w-md animate-slide-in flex-col border-l border-white/10 bg-ink-900 shadow-2xl lg:static lg:z-auto lg:w-96 lg:shadow-none"
      >
        <header className="flex items-start justify-between gap-3 border-b border-white/10 px-4 py-3.5">
          <div className="flex min-w-0 items-start gap-2.5">
            <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded bg-accent-500/20 text-[11px] font-semibold text-accent-300">
              {citation.marker}
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-white">
                {citation.filename}
              </p>
              <p className="text-[11px] text-ink-400">
                Page {citation.page_number} · chunk {citation.chunk_index + 1}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-ink-400 transition hover:bg-white/5 hover:text-ink-100"
          >
            <CloseIcon />
          </button>
        </header>

        <div className="border-b border-white/10 px-4 py-3">
          <div className="mb-1.5 flex items-center justify-between text-[11px] text-ink-400">
            <span>Relevance</span>
            <span className="tabular-nums text-ink-300">
              {(citation.score * 100).toFixed(0)}%
            </span>
          </div>
          <div
            className="h-1 overflow-hidden rounded-full bg-white/10"
            role="meter"
            aria-valuenow={Math.round(relevance * 100)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Cosine similarity to the question"
          >
            <div
              className="h-full rounded-full bg-accent-500"
              style={{ width: `${relevance * 100}%` }}
            />
          </div>
        </div>

        <div className="scroll-slim flex-1 overflow-y-auto px-4 py-4">
          <p className="mb-2 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-500">
            <FileIcon className="h-3.5 w-3.5" />
            Source text
          </p>
          <blockquote className="whitespace-pre-wrap rounded-lg border border-white/10 bg-ink-950/60 p-3.5 text-sm leading-relaxed text-ink-200">
            {citation.snippet}
          </blockquote>
        </div>
      </aside>
    </>
  );
}
