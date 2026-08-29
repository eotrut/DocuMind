"use client";

import { Fragment, useMemo, type ReactNode } from "react";

import { classNames } from "@/lib/format";
import type { Citation } from "@/lib/types";

const MARKER_PATTERN = /\[(\d{1,2})\]/g;
const BULLET_PATTERN = /^\s*[-*•]\s+/;

interface AnswerTextProps {
  content: string;
  citations: Citation[];
  onCitationClick: (citation: Citation) => void;
  activeCitationId?: string | null;
}

type Block =
  | { kind: "paragraph"; text: string }
  | { kind: "list"; items: string[] };

/**
 * Very small Markdown subset — paragraphs, `- ` bullets, `**bold**`.
 * A full Markdown renderer would be a heavy dependency for what Gemini
 * actually emits under this prompt.
 */
function toBlocks(content: string): Block[] {
  return content
    .split(/\n{2,}/)
    .map((raw) => raw.trim())
    .filter(Boolean)
    .map<Block>((raw) => {
      const lines = raw.split("\n").filter((line) => line.trim().length > 0);
      const isList = lines.length > 0 && lines.every((line) => BULLET_PATTERN.test(line));
      return isList
        ? { kind: "list", items: lines.map((line) => line.replace(BULLET_PATTERN, "")) }
        : { kind: "paragraph", text: raw };
    });
}

export default function AnswerText({
  content,
  citations,
  onCitationClick,
  activeCitationId,
}: AnswerTextProps) {
  const blocks = useMemo(() => toBlocks(content), [content]);
  const byMarker = useMemo(
    () => new Map(citations.map((citation) => [citation.marker, citation])),
    [citations],
  );

  /**
   * Render one run of text, turning `[n]` markers into clickable chips.
   * A marker that does not resolve to a supplied citation is left as literal
   * text: the model occasionally invents one, and a chip that opens nothing is
   * worse than the raw characters.
   */
  function renderInline(text: string, keyPrefix: string): ReactNode[] {
    const nodes: ReactNode[] = [];
    let cursor = 0;

    const pushText = (value: string, key: string) => {
      if (!value) return;
      // Bold is applied inside the plain-text runs only.
      value.split(/(\*\*[^*]+\*\*)/g).forEach((part, index) => {
        if (!part) return;
        if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
          nodes.push(
            <strong key={`${key}-b${index}`} className="font-semibold text-white">
              {part.slice(2, -2)}
            </strong>,
          );
        } else {
          nodes.push(<Fragment key={`${key}-t${index}`}>{part}</Fragment>);
        }
      });
    };

    for (const match of text.matchAll(MARKER_PATTERN)) {
      const index = match.index ?? 0;
      pushText(text.slice(cursor, index), `${keyPrefix}-p${index}`);

      const citation = byMarker.get(Number(match[1]));
      if (citation) {
        const isActive = activeCitationId === citation.chunk_id;
        nodes.push(
          <button
            key={`${keyPrefix}-c${index}`}
            type="button"
            onClick={() => onCitationClick(citation)}
            title={`${citation.filename} — page ${citation.page_number}`}
            aria-label={`Source ${citation.marker}: ${citation.filename}, page ${citation.page_number}`}
            className={classNames(
              "mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] items-center justify-center rounded px-1 align-[0.05rem] text-[11px] font-semibold tabular-nums transition",
              isActive
                ? "bg-accent-500 text-white"
                : "bg-accent-500/20 text-accent-300 hover:bg-accent-500/35 hover:text-accent-200",
            )}
          >
            {citation.marker}
          </button>,
        );
      } else {
        pushText(match[0], `${keyPrefix}-u${index}`);
      }
      cursor = index + match[0].length;
    }

    pushText(text.slice(cursor), `${keyPrefix}-end`);
    return nodes;
  }

  return (
    <div className="space-y-2.5 text-sm leading-relaxed text-ink-100">
      {blocks.map((block, index) =>
        block.kind === "list" ? (
          <ul key={index} className="ml-4 list-disc space-y-1">
            {block.items.map((item, itemIndex) => (
              <li key={itemIndex} className="pl-0.5 marker:text-ink-500">
                {renderInline(item, `${index}-${itemIndex}`)}
              </li>
            ))}
          </ul>
        ) : (
          <p key={index} className="whitespace-pre-wrap">
            {renderInline(block.text, String(index))}
          </p>
        ),
      )}
    </div>
  );
}
