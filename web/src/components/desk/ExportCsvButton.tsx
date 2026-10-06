"use client";

/**
 * GAUNTLET 7 Piece 7 — shared CSV export button.
 *
 * Links to the canonical Python CSV builder (/api/desk/export) with the
 * SAME query params the surrounding table used to render — byte
 * fidelity is by construction, not by hope (the builder is round-trip
 * pinned in tests/test_workflow_glue.py).
 */
export function ExportCsvButton(props: {
  kind: "journal" | "screener" | "pair" | "macro" | "options";
  params?: Record<string, string | number | undefined>;
  label?: string;
}) {
  const sp = new URLSearchParams();
  sp.set("kind", props.kind);
  for (const [k, v] of Object.entries(props.params || {})) {
    if (v !== undefined && v !== null && `${v}`.length) sp.set(k, `${v}`);
  }
  return (
    <a
      href={`/api/desk/export?${sp.toString()}`}
      className="inline-flex items-center gap-1 border border-[#c8a04b]/30 px-2 py-1 font-mono text-[10px] uppercase tracking-[0.16em] text-[#c8a04b]/90 transition hover:bg-[#c8a04b]/10"
      aria-label={`export ${props.kind} table as CSV`}
    >
      {props.label || "EXPORT CSV"}
    </a>
  );
}
