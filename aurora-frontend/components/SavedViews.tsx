"use client";

import { useState, type FormEvent } from "react";
import useSWR from "swr";

import { Button, ConfirmButton, Input, Select } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { SavedView, SavedViewPage, ViewParams } from "@/lib/types";

/** Pick, save and delete named filter sets for a list page. Filters stay in the page; this only stores them. */
export function SavedViews({ page, current, onApply }: { page: SavedViewPage; current: ViewParams; onApply: (p: ViewParams) => void }) {
  const key = `/saved-views?page=${page}`;
  const list = useSWR<SavedView[]>(key);
  const { can } = useSession();
  const [selected, setSelected] = useState("");
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState("");
  const [shared, setShared] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const views = list.data ?? [];
  const chosen = views.find((v) => v.id === selected);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return setErr("Give the view a name");
    setBusy(true);
    setErr(null);
    try {
      const params = Object.fromEntries(Object.entries(current).filter(([, v]) => v !== "" && v != null));
      const v = await api<SavedView>("/saved-views", { method: "POST", body: { page, name: name.trim(), params, shared } });
      await list.mutate();
      setSelected(v.id);
      setSaving(false);
      setName("");
      setShared(false);
    } catch (e2) {
      setErr(errorMessage(e2));
    } finally {
      setBusy(false);
    }
  };

  const id = `views-${page}`;
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
      <label htmlFor={id} className="text-muted">Saved views</label>
      <Select
        id={id}
        className="w-56"
        value={selected}
        disabled={!list.data}
        onChange={(e) => {
          setSelected(e.target.value);
          const v = views.find((x) => x.id === e.target.value);
          if (v) onApply(v.params);
        }}
      >
        <option value="">{views.length ? "Choose a view" : "None saved yet"}</option>
        {views.map((v) => (
          <option key={v.id} value={v.id}>{v.name}{v.shared ? (v.mine ? " (shared)" : " (shared with you)") : ""}</option>
        ))}
      </Select>
      {chosen && (chosen.mine || can("manage_users")) && (
        <ConfirmButton
          title={`Delete the view “${chosen.name}”?`}
          body={chosen.shared ? "It is removed for everyone it is shared with." : "Only the saved filters are removed."}
          confirmLabel="Delete view"
          onConfirm={async () => {
            await api(`/saved-views/${chosen.id}`, { method: "DELETE" });
            setSelected("");
            await list.mutate();
          }}
        >
          Delete view
        </ConfirmButton>
      )}
      {saving ? (
        <form onSubmit={save} className="flex flex-wrap items-center gap-2">
          <label htmlFor={`${id}-name`} className="sr-only">View name</label>
          <Input id={`${id}-name`} className="w-48" maxLength={80} placeholder="View name" value={name} onChange={(e) => setName(e.target.value)} />
          <label className="flex items-center gap-1 text-xs text-muted">
            <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
            Share with my organisation
          </label>
          <Button type="submit" loading={busy}>Save view</Button>
          <Button variant="ghost" onClick={() => { setSaving(false); setErr(null); }}>Cancel</Button>
        </form>
      ) : (
        <Button variant="ghost" onClick={() => setSaving(true)}>Save these filters</Button>
      )}
      {err && <span className="text-bad" role="alert">{err}</span>}
    </div>
  );
}
