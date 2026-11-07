/**
 * Client-side "download this text as a file" helper, shared by every
 * downloadable-template button (source objects CSV, linked service CSV, …).
 * No backend endpoint involved — the content is generated in-browser and
 * handed to the browser's own download mechanism via a throwaway object URL.
 */
export function downloadTextFile(fileName: string, content: string, mimeType = "text/csv;charset=utf-8"): void {
  if (typeof window === "undefined" || typeof document === "undefined") return;
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Same as {@link downloadTextFile} but for binary content (e.g. a
 *  `.xlsx` STTM template, §33.5) — an `ArrayBuffer`/`Uint8Array` instead of
 *  a text string. */
export function downloadBinaryFile(
  fileName: string,
  content: ArrayBuffer | Uint8Array,
  mimeType = "application/octet-stream",
): void {
  if (typeof window === "undefined" || typeof document === "undefined") return;
  const blob = new Blob([content as BlobPart], { type: mimeType });
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  } finally {
    URL.revokeObjectURL(url);
  }
}
