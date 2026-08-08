"use client";

import { useState } from "react";
import { placeOrder, type OrderSide } from "@/lib/api";
import { Button, Field, Input } from "@/components/ui";
import { RiskExplainer } from "@/components/RiskExplainer";

type Result =
  | { kind: "ok"; text: string }
  | { kind: "err"; text: string; risk?: { reason: string; symbol: string; qty: number; side: OrderSide } }
  | null;

export function OrderForm({
  accountId,
  onPlaced,
  bare,
}: {
  accountId: string;
  onPlaced: () => void;
  bare?: boolean;
}) {
  const [symbol, setSymbol] = useState("");
  const [qty, setQty] = useState("");
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<Result>(null);

  async function submit(side: OrderSide) {
    const trimmed = symbol.trim().toUpperCase();
    const qtyNum = Number(qty);
    if (!trimmed || !Number.isFinite(qtyNum) || qtyNum <= 0) {
      setResult({ kind: "err", text: "Enter a symbol and a positive quantity." });
      return;
    }

    setPending(true);
    setResult(null);
    try {
      const order = await placeOrder(accountId, trimmed, qtyNum, side);
      const detail = order.reason ? ` — ${order.reason}` : "";
      setResult({
        kind: order.status === "rejected" ? "err" : "ok",
        text: `${side.toUpperCase()} ${order.qty} ${order.symbol}: ${order.status}${detail}`,
      });
      onPlaced();
    } catch (e) {
      const text = e instanceof Error ? e.message : "Order failed.";
      // A deterministic risk rejection (HTTP 422) — offer the read-only explainer.
      const isRisk = text.toLowerCase().includes("risk rejection");
      setResult({
        kind: "err",
        text,
        ...(isRisk
          ? { risk: { reason: text, symbol: trimmed, qty: qtyNum, side } }
          : {}),
      });
    } finally {
      setPending(false);
    }
  }

  return (
    <div className={bare ? "" : "tcard"} style={bare ? undefined : { marginTop: 12, padding: "20px 22px" }}>
      <form
        className="orderform"
        onSubmit={(e) => {
          e.preventDefault();
          submit("buy");
        }}
      >
        <Field label="Symbol" htmlFor="of-symbol">
          <Input
            id="of-symbol"
            name="symbol"
            placeholder="AAPL"
            autoComplete="off"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            style={{ width: 140 }}
          />
        </Field>
        <Field label="Quantity" htmlFor="of-qty">
          <Input
            id="of-qty"
            name="qty"
            type="number"
            min="0"
            step="any"
            placeholder="10"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            style={{ width: 120 }}
          />
        </Field>
        <Button type="submit" variant="buy" disabled={pending}>
          {pending ? "Placing…" : "Buy"}
        </Button>
        <Button type="button" variant="sell" disabled={pending} onClick={() => submit("sell")}>
          Sell
        </Button>
      </form>

      {result && (
        <div
          className={`order-status ${result.kind === "ok" ? "pos" : "neg"}`}
          role="status"
        >
          {result.text}
          {result.kind === "err" && result.risk && (
            <RiskExplainer
              reason={result.risk.reason}
              symbol={result.risk.symbol}
              qty={result.risk.qty}
              side={result.risk.side}
            />
          )}
        </div>
      )}
    </div>
  );
}
