"use client";

import { useState } from "react";
import { placeOrder, type OrderSide } from "@/lib/api";

type Result =
  | { kind: "ok"; text: string }
  | { kind: "err"; text: string }
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
      setResult({
        kind: "err",
        text: e instanceof Error ? e.message : "Order failed.",
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
        <div className="field">
          <label htmlFor="of-symbol">Symbol</label>
          <input
            id="of-symbol"
            name="symbol"
            placeholder="AAPL"
            autoComplete="off"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            style={{ width: 140 }}
          />
        </div>
        <div className="field">
          <label htmlFor="of-qty">Quantity</label>
          <input
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
        </div>
        <button type="submit" className="btn buy" disabled={pending}>
          {pending ? "Placing…" : "Buy"}
        </button>
        <button
          type="button"
          className="btn sell"
          disabled={pending}
          onClick={() => submit("sell")}
        >
          Sell
        </button>
      </form>

      {result && (
        <div
          className={`order-status ${result.kind === "ok" ? "pos" : "neg"}`}
          role="status"
        >
          {result.text}
        </div>
      )}
    </div>
  );
}
