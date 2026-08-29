"use client";

import { useCallback, useRef, useState, type DragEvent } from "react";

import { AlertIcon, SpinnerIcon, UploadIcon } from "./Icons";
import { classNames } from "@/lib/format";
import type { UploadResult } from "@/lib/types";

interface UploadDropzoneProps {
  uploading: boolean;
  results: UploadResult[];
  onUpload: (files: File[]) => void;
  onDismissResults: () => void;
}

export default function UploadDropzone({
  uploading,
  results,
  onUpload,
  onDismissResults,
}: UploadDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const handleFiles = useCallback(
    (fileList: FileList | null) => {
      const files = Array.from(fileList ?? []);
      if (files.length === 0) return;

      // Filter client-side too, so an obvious mistake never costs a round trip.
      const pdfs = files.filter((file) => file.name.toLowerCase().endsWith(".pdf"));
      const rejected = files.length - pdfs.length;
      setLocalError(
        rejected > 0
          ? `${rejected} file${rejected > 1 ? "s were" : " was"} skipped — only PDFs are supported.`
          : null,
      );
      if (pdfs.length > 0) onUpload(pdfs);
    },
    [onUpload],
  );

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (!uploading) handleFiles(event.dataTransfer.files);
  }

  const failures = results.filter((result) => !result.ok);
  const successes = results.filter((result) => result.ok);

  return (
    <div className="space-y-2">
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={classNames(
          "rounded-xl border border-dashed p-4 text-center transition",
          dragging
            ? "border-accent-400 bg-accent-500/10"
            : "border-white/15 bg-ink-950/40 hover:border-white/25",
          uploading && "opacity-70",
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          multiple
          className="sr-only"
          onChange={(event) => {
            handleFiles(event.target.files);
            event.target.value = ""; // allow re-picking the same file
          }}
        />

        <div className="mb-2 flex justify-center text-ink-400">
          {uploading ? <SpinnerIcon className="h-5 w-5" /> : <UploadIcon className="h-5 w-5" />}
        </div>

        <p className="text-xs text-ink-300">
          {uploading ? "Indexing your documents…" : "Drop PDFs here"}
        </p>

        <button
          type="button"
          disabled={uploading}
          onClick={() => inputRef.current?.click()}
          className="mt-2 text-xs font-medium text-accent-400 underline-offset-4 hover:underline disabled:opacity-50"
        >
          or browse files
        </button>
      </div>

      {localError && <p className="text-xs text-amber-300">{localError}</p>}

      {results.length > 0 && (
        <div className="space-y-1.5 rounded-lg border border-white/10 bg-ink-950/50 p-2.5">
          {successes.length > 0 && (
            <p className="text-xs text-emerald-300">
              Indexed {successes.length} document{successes.length > 1 ? "s" : ""}.
            </p>
          )}
          {failures.map((failure) => (
            <p
              key={failure.filename}
              className="flex items-start gap-1.5 text-xs text-red-300"
            >
              <AlertIcon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                <span className="font-medium">{failure.filename}</span>: {failure.error}
              </span>
            </p>
          ))}
          <button
            type="button"
            onClick={onDismissResults}
            className="text-xs text-ink-400 underline-offset-4 hover:underline"
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
