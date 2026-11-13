"use client";

import { RotateCcw } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, ErrorState, Input, Notice, Select } from "@/components/ui";
import { api } from "@/lib/api";
import {
  SETTING_GROUPS,
  boundsHint,
  buildSettingsPatch,
  choiceLabel,
  fieldErrorsFromApi,
  formatSettingValue,
  inputValue,
  type SettingsDraft,
} from "@/lib/settings";
import type { OrgSetting, OrgSettings } from "@/lib/types";

/** Settings form. Read-only unless `canEdit`; the API enforces manage_settings on save regardless. */
export function OrgSettingsForm({
  data,
  canEdit,
  onSaved,
}: {
  data: OrgSettings;
  canEdit: boolean;
  onSaved: (updated: OrgSettings) => Promise<unknown> | void;
}) {
  const [draft, setDraft] = useState<SettingsDraft>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [err, setErr] = useState<unknown>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const keys = data.items.map((s) => s.key);
  const pending = buildSettingsPatch(data.items, draft);
  const changeCount = Object.keys(pending.body).length;

  const edit = (key: string, value: string | null) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setFieldErrors((fe) => {
      const next = { ...fe };
      delete next[key];
      return next;
    });
    setMsg(null);
  };

  const discard = () => {
    setDraft({});
    setFieldErrors({});
    setErr(null);
    setMsg(null);
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setMsg(null);
    const { body, errors } = buildSettingsPatch(data.items, draft);
    setFieldErrors(errors);
    if (Object.keys(errors).length) return;
    const n = Object.keys(body).length;
    if (n === 0) {
      setDraft({});
      setMsg("Nothing to save.");
      return;
    }
    setBusy(true);
    try {
      const updated = await api<OrgSettings>("/settings/org", { method: "PATCH", body });
      await onSaved(updated);
      setDraft({});
      setMsg(`Saved ${n} ${n === 1 ? "change" : "changes"}.`);
    } catch (e2) {
      setFieldErrors(fieldErrorsFromApi(e2, keys));
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  const alertMin = data.items.find((s) => s.key === "alert_min_severity");

  return (
    <form onSubmit={save} noValidate>
      {!canEdit && (
        <div className="mb-4">
          <Notice tone="info">You can see the values in use. Only administrators can change them.</Notice>
        </div>
      )}
      <div className="space-y-6">
        {SETTING_GROUPS.map((g) => {
          const rows = data.items.filter((s) => s.group === g.id);
          if (!rows.length) return null;
          return (
            <Card key={g.id} title={g.title} subtitle={g.subtitle}>
              {g.id === "alerts" && alertMin && (
                <p className="mb-2 text-sm text-muted">
                  Open alerts rated <span className="text-ink">{choiceLabel(String(alertMin.value))}</span> or higher are sent to:{" "}
                  <span className="text-ink">{data.alert_channels.join(", ")}</span>.
                  {!data.alert_channels.includes("webhook") && " No webhook is configured on the server, so notifications go to the server log only."}
                </p>
              )}
              <div>
                {rows.map((s) => (
                  <SettingRow
                    key={s.key}
                    setting={s}
                    draft={draft}
                    error={fieldErrors[s.key]}
                    canEdit={canEdit}
                    changed={s.key in pending.body}
                    onChange={(v) => edit(s.key, v)}
                  />
                ))}
              </div>
            </Card>
          );
        })}
      </div>
      {err != null && Object.keys(fieldErrors).length === 0 && (
        <div className="mt-4">
          <ErrorState error={err} />
        </div>
      )}
      {err != null && Object.keys(fieldErrors).length > 0 && (
        <div className="mt-4">
          <Notice tone="bad">The server rejected some values. See the messages next to the fields.</Notice>
        </div>
      )}
      {msg && (
        <div className="mt-4">
          <Notice tone="ok">{msg}</Notice>
        </div>
      )}
      {canEdit && (
        <div className="sticky bottom-0 mt-6 flex flex-wrap items-center justify-end gap-3 border-t border-line bg-bg/90 py-3 backdrop-blur">
          <span className="text-sm text-muted" aria-live="polite">
            {changeCount ? `${changeCount} unsaved ${changeCount === 1 ? "change" : "changes"}` : "No unsaved changes"}
          </span>
          <Button variant="ghost" onClick={discard} disabled={busy || Object.keys(draft).length === 0}>
            Discard
          </Button>
          <Button type="submit" variant="primary" loading={busy} disabled={Object.keys(draft).length === 0}>
            Save changes
          </Button>
        </div>
      )}
    </form>
  );
}

function SettingRow({
  setting: s,
  draft,
  error,
  canEdit,
  changed,
  onChange,
}: {
  setting: OrgSetting;
  draft: SettingsDraft;
  error?: string;
  canEdit: boolean;
  changed: boolean;
  onChange: (value: string | null) => void;
}) {
  const id = `setting-${s.key}`;
  const value = inputValue(s, draft);
  const resetting = s.key in draft && draft[s.key] === null;
  const hint = boundsHint(s);
  const describedBy = `${id}-help`;
  return (
    <div className="grid gap-3 border-b border-line/60 py-4 first:pt-0 last:border-0 last:pb-0 md:grid-cols-[minmax(0,1fr)_280px]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={id} className="text-sm font-medium text-ink">
            {s.label}
          </label>
          {s.source === "org" ? <Badge tone="info">Overridden</Badge> : <Badge>Default</Badge>}
          {changed && <Badge tone="warn">Unsaved</Badge>}
        </div>
        <p className="mt-1 text-xs text-muted">{s.description}</p>
        <p className="mt-1 text-xs text-faint">
          In use: <span className="text-ink">{formatSettingValue(s, s.value)}</span>
          {" · "}Platform default: {formatSettingValue(s, s.default)}
        </p>
      </div>
      <div className="flex flex-col gap-1.5">
        {s.type === "choice" ? (
          <Select id={id} value={value} disabled={!canEdit} onChange={(e) => onChange(e.target.value)} aria-invalid={!!error || undefined} aria-describedby={describedBy}>
            {(s.choices ?? []).map((c) => (
              <option key={c} value={c}>
                {choiceLabel(c)}
              </option>
            ))}
          </Select>
        ) : (
          <div className="flex items-center gap-2">
            <Input
              id={id}
              inputMode={s.type === "integer" ? "numeric" : "decimal"}
              value={value}
              disabled={!canEdit}
              onChange={(e) => onChange(e.target.value)}
              aria-invalid={!!error || undefined}
              aria-describedby={describedBy}
            />
            {s.unit && <span className="shrink-0 text-xs text-muted">{s.unit}</span>}
          </div>
        )}
        <div id={describedBy}>
          {error ? (
            <p className="text-xs text-bad" role="alert">
              {error}
            </p>
          ) : resetting ? (
            <p className="text-xs text-warn">Goes back to the platform default when you save.</p>
          ) : (
            hint && <p className="text-xs text-faint">{hint}</p>
          )}
        </div>
        {canEdit && s.source === "org" && !resetting && (
          <Button variant="ghost" className="self-start" onClick={() => onChange(null)}>
            <RotateCcw className="h-3.5 w-3.5" aria-hidden /> Reset to default
          </Button>
        )}
      </div>
    </div>
  );
}
