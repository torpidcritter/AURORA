/** Client-side validation helpers. They improve feedback only; the API enforces every rule again. */

export const MAX_UPLOAD_MB = 20;
const UPLOAD_EXTENSIONS = ["csv", "tsv", "txt", "xlsx", "json"];

export function validateFile(f: File | null): string | null {
  if (!f) return "Choose a file to upload.";
  const ext = f.name.includes(".") ? (f.name.split(".").pop()?.toLowerCase() ?? "") : "";
  if (!UPLOAD_EXTENSIONS.includes(ext)) return "Use a CSV, Excel (.xlsx) or JSON file.";
  if (f.size === 0) return "The file is empty.";
  if (f.size > MAX_UPLOAD_MB * 1024 * 1024) return `The file is larger than ${MAX_UPLOAD_MB} MB.`;
  return null;
}

/** Mirrors the backend password policy (10+ characters, at least 3 of 4 character classes). */
export function passwordProblem(pw: string): string | null {
  if (pw.length < 10) return "Use at least 10 characters";
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(pw)).length;
  if (classes < 3) return "Use at least three of: lowercase, uppercase, digits, symbols";
  return null;
}
