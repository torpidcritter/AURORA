/** Helpers for two-factor login screens. The API checks every code; these only shape the input. */

/** Digits only, for a 6-digit authenticator code ("123 456" → "123456"). */
export function cleanCode(raw: string): string {
  return raw.replace(/\s+/g, "");
}

export function codeProblem(raw: string): string | null {
  return /^\d{6}$/.test(cleanCode(raw)) ? null : "Enter the 6-digit code from your authenticator app";
}

/** The secret in groups of four, easier to type into an app by hand. */
export function groupSecret(secret: string): string {
  return secret.replace(/(.{4})/g, "$1 ").trim();
}

/** Recovery codes as a text file the user can save. */
export function recoveryCodesText(codes: string[], email: string): string {
  return [`Aurora recovery codes for ${email}`, "Each code works once. Keep them somewhere safe.", "", ...codes, ""].join("\n");
}
