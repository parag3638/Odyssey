"use client";

import { useState } from "react";
import { createAccount, type AccountOut } from "@/lib/api";

export function ConnectAccountForm({
  bare,
  onConnected,
}: {
  bare?: boolean;
  onConnected?: (account: AccountOut) => void;
}) {
  const [label, setLabel] = useState("Primary");
  const [keyId, setKeyId] = useState("");
  const [secret, setSecret] = useState("");
  const [endpoint, setEndpoint] = useState("https://paper-api.alpaca.markets");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmedLabel = label.trim();
    const trimmedKeyId = keyId.trim();
    const trimmedSecret = secret.trim();
    const trimmedEndpoint = endpoint.trim();

    if (!trimmedLabel || !trimmedKeyId || !trimmedSecret) {
      setError("Enter a label, key, and secret.");
      return;
    }

    setPending(true);
    setError(null);
    try {
      const account = await createAccount({
        label: trimmedLabel,
        alpaca_key_id: trimmedKeyId,
        alpaca_secret: trimmedSecret,
        endpoint: trimmedEndpoint || undefined,
      });
      onConnected?.(account);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not connect account.");
      setPending(false);
    }
  }

  return (
    <div className={bare ? "" : "tcard"} style={bare ? undefined : { marginTop: 12, padding: "20px 22px" }}>
      <form className="orderform" onSubmit={submit}>
        <div className="field">
          <label htmlFor="ca-label">Label</label>
          <input
            id="ca-label"
            name="label"
            placeholder="Primary"
            autoComplete="off"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            style={{ width: 140 }}
          />
        </div>
        <div className="field">
          <label htmlFor="ca-key">Alpaca key</label>
          <input
            id="ca-key"
            name="alpaca_key_id"
            placeholder="PK…"
            autoComplete="off"
            spellCheck={false}
            value={keyId}
            onChange={(e) => setKeyId(e.target.value)}
            style={{ width: 220 }}
          />
        </div>
        <div className="field">
          <label htmlFor="ca-secret">Alpaca secret</label>
          <input
            id="ca-secret"
            name="alpaca_secret"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            style={{ width: 220 }}
          />
        </div>
        <div className="field">
          <label htmlFor="ca-endpoint">Endpoint</label>
          <input
            id="ca-endpoint"
            name="endpoint"
            autoComplete="off"
            spellCheck={false}
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
            style={{ width: 240 }}
          />
        </div>
        <button type="submit" className="btn buy" disabled={pending}>
          {pending ? "Connecting…" : "Connect account"}
        </button>
      </form>

      <div className="faint" style={{ marginTop: 10 }}>
        Paper-trading keys only — from your Alpaca dashboard&apos;s API Keys panel. Odyssey never
        touches real money.
      </div>

      {error && (
        <div className="order-status neg" role="status">
          {error}
        </div>
      )}
    </div>
  );
}
