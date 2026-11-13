import { describe, expect, it } from "vitest";

import { safeNext } from "@/app/login/LoginForm";
import { MAX_UPLOAD_MB, passwordProblem, validateFile } from "@/lib/validation";

const file = (name: string, size: number) => {
  const f = new File(["x"], name);
  Object.defineProperty(f, "size", { value: size });
  return f;
};

describe("validateFile", () => {
  it("requires a file", () => expect(validateFile(null)).toMatch(/choose a file/i));
  it("accepts supported formats", () => {
    for (const n of ["a.csv", "b.TSV", "c.xlsx", "d.json", "e.txt"]) expect(validateFile(file(n, 10))).toBeNull();
  });
  it("rejects other or missing extensions", () => {
    expect(validateFile(file("a.exe", 10))).toMatch(/csv/i);
    expect(validateFile(file("csv", 10))).toMatch(/csv/i);
  });
  it("rejects empty and oversized files", () => {
    expect(validateFile(file("a.csv", 0))).toMatch(/empty/i);
    expect(validateFile(file("a.csv", MAX_UPLOAD_MB * 1024 * 1024 + 1))).toMatch(/larger/i);
  });
});

describe("passwordProblem", () => {
  it("enforces length and character classes like the backend", () => {
    expect(passwordProblem("Ab1!")).toMatch(/10 characters/);
    expect(passwordProblem("abcdefghijk")).toMatch(/three/);
    expect(passwordProblem("abcdefghij1")).toMatch(/three/);
    expect(passwordProblem("abcdefghiJ1")).toBeNull();
  });
});

describe("safeNext (open-redirect protection)", () => {
  it("keeps same-site paths", () => {
    expect(safeNext("/products/abc?x=1#y")).toBe("/products/abc?x=1#y");
  });
  it.each([null, "", "https://evil.com", "//evil.com", "/\\evil.com", "/\t/evil.com", "/\n/evil.com", "javascript:alert(1)", "evil"])(
    "falls back for %j",
    (v) => expect(safeNext(v)).toBe("/dashboard"),
  );
});
