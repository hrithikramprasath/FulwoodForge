/*
 * Small download helpers for the server-backed `.docx`/`.pptx`/`.pdf` exports and
 * the two `answers-*` teacher files.
 */

/** How long a blob URL stays valid after the click, so every browser can start the save. */
const REVOKE_DELAY_MS = 10_000;

/** Slugify a resource title into a download filename with the given extension. */
export function slugFilename(title: string, ext: string): string {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${slug || "resource"}.${ext}`;
}

/**
 * Trigger a browser download of a Blob under the given filename.
 *
 * The object URL is always released: immediately if creating or clicking the
 * anchor throws, otherwise on a short timer so the browser has begun the save
 * (revoking synchronously after click() races the download in Firefox/Safari).
 */
export function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  try {
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
  } catch (err) {
    URL.revokeObjectURL(url);
    throw err;
  } finally {
    anchor.remove();
  }
  setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
}
