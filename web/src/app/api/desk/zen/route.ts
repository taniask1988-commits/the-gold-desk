import { NextResponse } from "next/server";
import { promises as fs } from "fs";
import path from "path";
import { DATA_ROOT } from "@/lib/desk/journal";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    // catalog written by: python -m gold_desk.cli zen (harness = source of truth)
    let catalog: Record<string, unknown> | null = null;
    try {
      catalog = JSON.parse(
        await fs.readFile(path.join(DATA_ROOT, "zen-catalog.json"), "utf-8"),
      );
    } catch {
      catalog = null;
    }
    // bench history (veto research results)
    const bench: Array<Record<string, unknown>> = [];
    try {
      const text = await fs.readFile(
        path.join(DATA_ROOT, "veto_bench.jsonl"),
        "utf-8",
      );
      for (const line of text.split("\n")) {
        const t = line.trim();
        if (!t) continue;
        try {
          bench.push(JSON.parse(t));
        } catch {
          /* skip */
        }
      }
    } catch {
      /* no bench yet */
    }
    return NextResponse.json({
      ok: true,
      base_url: "https://opencode.ai/zen/v1",
      keyless: true,
      catalog,
      bench: bench.slice(-30).reverse(),
    });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: (e as Error).message },
      { status: 500 },
    );
  }
}
