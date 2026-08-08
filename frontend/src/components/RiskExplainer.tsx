"use client";

import { useState } from "react";
import { explainRisk } from "@/lib/api";

/* "Why?" expander shown next to a risk rejection. It explains a decision the
   deterministic engine has ALREADY made — it never participates in it. */
export function RiskExplainer({
  reason,
  symbol,
  qty,
  side,
}: {
  reason: string;
  symbol?: string;
  qty?: number;
  side?: string;
}) {
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tried, setTried] = useState(false);

  async function explain() {
    setBusy(true);
    try {
      const r = await explainRisk({ reason, symbol, qty, side });
      setText(r.available ? r.text : null);
    } catch {
      setText(null);
    } finally {
      setBusy(false);
      setTried(true);
    }
  }

  if (text) return <div className="riskwhy-text">{text}</div>;
  if (tried) return null; // unavailable → stay quiet

  return (
    <button type="button" className="riskwhy-btn" onClick={explain} disabled={busy}>
      {busy ? "Explaining…" : "Why?"}
    </button>
  );
}
