import type { GeneratedContent } from "@/types/content";
import { api } from "@/lib/api";
import { slugFilename, triggerDownload } from "@/lib/download";

/** The downloadable formats, all rendered by the backend. */
export type ExportFormat =
  "docx" | "pptx" | "pdf" | "answers-docx" | "answers-pdf";

// The server's queue wait plus worker deadline must be able to answer first.
const EXPORT_TIMEOUT_MS = 150_000;

function withTimeout(signal?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(EXPORT_TIMEOUT_MS);
  return signal ? AbortSignal.any([signal, timeout]) : timeout;
}

/**
 * Export already-generated content (no Gemini re-call) and download the file.
 * Any failure rejects without touching the caller's on-screen document.
 */
export async function exportResource(
  content: GeneratedContent,
  fmt: ExportFormat,
  signal?: AbortSignal,
): Promise<void> {
  const { blob, filename } = await api.export.adHoc(fmt, content, {
    signal: withTimeout(signal),
  });
  triggerDownload(blob, filename ?? slugFilename(content.title, fmt));
}

/** Export a saved lesson by id, served from the artifact cache when available. */
export async function exportSavedLesson(
  lessonUuid: string,
  title: string,
  fmt: ExportFormat,
  signal?: AbortSignal,
): Promise<void> {
  const { blob, filename } = await api.export.savedLesson(lessonUuid, fmt, {
    signal: withTimeout(signal),
  });
  triggerDownload(blob, filename ?? slugFilename(title, fmt));
}
