/** Helpers for scheduled digests. The API validates the same rules and checks webhook addresses. */

import type { DigestChannel, DigestSchedule, DigestSection } from "@/lib/types";

export const SECTIONS: { key: DigestSection; label: string }[] = [
  { key: "stockouts", label: "Stockout risk" },
  { key: "overstock", label: "Overstock" },
  { key: "accuracy", label: "Forecast accuracy" },
  { key: "alerts", label: "Alerts" },
  { key: "purchasing", label: "Purchasing" },
];

export const CHANNELS: { key: DigestChannel; label: string; target: string | null; hint?: string }[] = [
  { key: "in_app", label: "In Aurora only", target: null },
  { key: "slack", label: "Slack", target: "Slack webhook URL", hint: "An incoming-webhook URL from hooks.slack.com" },
  { key: "webhook", label: "Webhook (JSON)", target: "Webhook URL", hint: "A public https:// address; the digest is POSTed as JSON" },
  { key: "email", label: "E-mail", target: "E-mail addresses", hint: "Separate addresses with commas. Needs e-mail set up on the server" },
];

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export const hh = (h: number): string => `${String(h).padStart(2, "0")}:00 UTC`;

export function whenLabel(s: Pick<DigestSchedule, "frequency" | "weekday" | "hour_utc">): string {
  return s.frequency === "weekly" && s.weekday != null ? `${WEEKDAYS[s.weekday]}s at ${hh(s.hour_utc)}` : `Daily at ${hh(s.hour_utc)}`;
}

export interface DigestForm {
  name: string;
  frequency: "daily" | "weekly";
  weekday: number;
  hour_utc: number;
  sections: DigestSection[];
  channel: DigestChannel;
  target: string;
}

export const emptyDigest = (): DigestForm => ({
  name: "",
  frequency: "daily",
  weekday: 0,
  hour_utc: 7,
  sections: ["stockouts", "alerts", "purchasing"],
  channel: "in_app",
  target: "",
});

export function digestErrors(f: DigestForm): string | null {
  if (!f.name.trim()) return "Give the digest a name";
  if (f.sections.length === 0) return "Choose at least one section";
  if (f.channel !== "in_app") {
    const t = f.target.trim();
    if (!t) return `Enter the ${CHANNELS.find((c) => c.key === f.channel)!.target!.toLowerCase()}`;
    if (f.channel !== "email" && !t.startsWith("https://")) return "The address must start with https://";
    if (f.channel === "slack" && !t.startsWith("https://hooks.slack.com/")) return "Slack webhook URLs start with https://hooks.slack.com/";
  }
  return null;
}

export function digestBody(f: DigestForm) {
  return {
    name: f.name.trim(),
    frequency: f.frequency,
    weekday: f.frequency === "weekly" ? f.weekday : null,
    hour_utc: f.hour_utc,
    sections: f.sections,
    channel: f.channel,
    target: f.channel === "in_app" ? null : f.target.trim(),
  };
}
