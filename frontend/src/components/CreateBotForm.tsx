"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createBot, listAccounts, type AccountOut } from "@/lib/api";
import { Button, Field, Input, Select } from "@/components/ui";

export function CreateBotForm({
  bare,
  onNeedAccount,
  initialAccounts,
}: {
  bare?: boolean;
  onNeedAccount?: () => void;
  initialAccounts?: AccountOut[];
}) {
  const router = useRouter();
  const [accounts, setAccounts] = useState<AccountOut[]>(initialAccounts ?? []);
  const [accountId, setAccountId] = useState(initialAccounts?.[0]?.id ?? "");
  const [name, setName] = useState("");
  const [strategy, setStrategy] = useState<"trailing_stop" | "copy_trade">(
    "trailing_stop",
  );
  const [symbol, setSymbol] = useState("");
  const [initialShares, setInitialShares] = useState("10");
  const [stopPct, setStopPct] = useState("10");
  const [trailPct, setTrailPct] = useState("5");
  const [politician, setPolitician] = useState("");
  const [perTradeNotional, setPerTradeNotional] = useState("1000");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(initialAccounts !== undefined);

  useEffect(() => {
    if (initialAccounts !== undefined) return;
    let cancelled = false;
    (async () => {
      try {
        const list = await listAccounts();
        if (cancelled) return;
        setAccounts(list);
        if (list[0]) setAccountId(list[0].id);
      } catch {
        if (!cancelled) setError("Could not load accounts.");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [initialAccounts]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmedName = name.trim();
    const acctNum = Number(accountId);

    if (!trimmedName || !Number.isFinite(acctNum)) {
      setError("Pick an account and enter a name.");
      return;
    }

    if (strategy === "copy_trade") {
      const trimmedPolitician = politician.trim();
      const notional = Number(perTradeNotional);
      if (!trimmedPolitician) {
        setError("Enter a politician to copy.");
        return;
      }
      if (!Number.isFinite(notional) || notional <= 0) {
        setError("Per-trade notional must be a positive number.");
        return;
      }
      setPending(true);
      setError(null);
      try {
        const bot = await createBot({
          name: trimmedName,
          account_id: acctNum,
          strategy_type: "copy_trade",
          politician: trimmedPolitician,
          per_trade_notional: notional,
        });
        router.push(`/bots/${bot.id}`);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not create bot.");
        setPending(false);
      }
      return;
    }

    const trimmedSymbol = symbol.trim().toUpperCase();
    const shares = Number(initialShares);
    const stop = Number(stopPct) / 100;
    const trail = Number(trailPct) / 100;

    if (!trimmedSymbol) {
      setError("Enter a symbol.");
      return;
    }
    if (!Number.isFinite(shares) || shares <= 0) {
      setError("Initial shares must be a positive number.");
      return;
    }

    setPending(true);
    setError(null);
    try {
      const bot = await createBot({
        name: trimmedName,
        account_id: acctNum,
        strategy_type: "trailing_stop",
        symbol: trimmedSymbol,
        initial_shares: shares,
        stop_pct: stop,
        trail_pct: trail,
      });
      router.push(`/bots/${bot.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create bot.");
      setPending(false);
    }
  }

  if (loaded && accounts.length === 0 && !error) {
    return (
      <div className={bare ? "" : "tcard"} style={bare ? undefined : { marginTop: 12, padding: "20px 22px" }}>
        <div className="faint">Connect an account to create a bot.</div>
        {onNeedAccount && (
          <Button variant="buy" style={{ marginTop: 12 }} onClick={onNeedAccount}>
            Connect account
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className={bare ? "" : "tcard"} style={bare ? undefined : { marginTop: 12, padding: "20px 22px" }}>
      <form className="orderform" onSubmit={submit}>
        <Field label="Name" htmlFor="cb-name">
          <Input
            id="cb-name"
            name="name"
            placeholder="TSLA trail"
            autoComplete="off"
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={{ width: 160 }}
          />
        </Field>
        <Field label="Strategy" htmlFor="cb-strategy">
          <Select
            id="cb-strategy"
            minWidth={150}
            value={strategy}
            onChange={(v) => setStrategy(v as "trailing_stop" | "copy_trade")}
            options={[
              { value: "trailing_stop", label: "Trailing-stop" },
              { value: "copy_trade", label: "Copy-trade" },
            ]}
          />
        </Field>
        {strategy === "trailing_stop" ? (
          <>
            <Field label="Symbol" htmlFor="cb-symbol">
              <Input
                id="cb-symbol"
                name="symbol"
                placeholder="TSLA"
                autoComplete="off"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                style={{ width: 110 }}
              />
            </Field>
            <Field label="Initial shares" htmlFor="cb-shares">
              <Input
                id="cb-shares"
                name="initial_shares"
                type="number"
                min="0"
                step="any"
                value={initialShares}
                onChange={(e) => setInitialShares(e.target.value)}
                style={{ width: 110 }}
              />
            </Field>
            <Field label="Stop %" htmlFor="cb-stop">
              <Input
                id="cb-stop"
                name="stop_pct"
                type="number"
                min="0"
                step="any"
                value={stopPct}
                onChange={(e) => setStopPct(e.target.value)}
                style={{ width: 90 }}
              />
            </Field>
            <Field label="Trail %" htmlFor="cb-trail">
              <Input
                id="cb-trail"
                name="trail_pct"
                type="number"
                min="0"
                step="any"
                value={trailPct}
                onChange={(e) => setTrailPct(e.target.value)}
                style={{ width: 90 }}
              />
            </Field>
          </>
        ) : (
          <>
            <Field label="Politician" htmlFor="cb-politician">
              <Input
                id="cb-politician"
                name="politician"
                placeholder="Michael McCaul"
                autoComplete="off"
                value={politician}
                onChange={(e) => setPolitician(e.target.value)}
                style={{ width: 180 }}
              />
            </Field>
            <Field label="Per-trade $" htmlFor="cb-notional">
              <Input
                id="cb-notional"
                name="per_trade_notional"
                type="number"
                min="0"
                step="any"
                value={perTradeNotional}
                onChange={(e) => setPerTradeNotional(e.target.value)}
                style={{ width: 110 }}
              />
            </Field>
          </>
        )}
        <Field label="Account" htmlFor="cb-account">
          <Select
            id="cb-account"
            minWidth={180}
            placeholder="No accounts"
            value={accountId}
            onChange={setAccountId}
            options={accounts.map((a) => ({
              value: a.id,
              label: `${a.label} · ${a.mode}`,
            }))}
          />
        </Field>
        <Button type="submit" variant="buy" disabled={pending || accounts.length === 0}>
          {pending ? "Creating…" : "Create bot"}
        </Button>
      </form>

      {error && (
        <div className="order-status neg" role="status">
          {error}
        </div>
      )}
    </div>
  );
}
