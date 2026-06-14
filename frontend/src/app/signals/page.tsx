"use client";

import { useEffect, useState } from "react";
import { Nav } from "@/components/Nav";
import { SignalsTable } from "@/components/SignalsTable";
import { listAccounts, syncSignals } from "@/lib/api";
import { initials } from "@/lib/format";
import { Button, SegmentedControl } from "@/components/ui";
import { SailIcon, SwapIcon, XIcon } from "@/components/icons";

type Action = "all" | "buy" | "sell";

export default function SignalsPage() {
  const [accountLabel, setAccountLabel] = useState("Trading Claude");
  const [politician, setPolitician] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [action, setAction] = useState<Action>("all");
  const [reloadKey, setReloadKey] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const accounts = await listAccounts();
        if (!cancelled && accounts[0]) setAccountLabel(accounts[0].label);
      } catch {
        /* keep default */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function sync() {
    setSyncing(true);
    setSyncMsg("");
    try {
      const r = await syncSignals();
      setSyncMsg(`+${r.added} new`);
      setReloadKey((k) => k + 1);
    } catch (e) {
      setSyncMsg(e instanceof Error ? e.message : "sync failed");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <>
      <Nav active="signals" accountLabel={accountLabel} accountInitials={initials(accountLabel)} />

      <div className="wrap roomy">
        <div className="shead reveal" style={{ ["--i" as string]: 0 }}>
          <span className="flame">
            <SailIcon />
          </span>
          <span className="ttl">Signals</span>
          <span className="sub">Capitol Trades · congressional disclosures</span>
        </div>

        <div className="filters reveal" style={{ ["--i" as string]: 1 }}>
          <span className="filter">
            Action
            <SegmentedControl
              options={[
                { label: "All", value: "all" },
                { label: "Buy", value: "buy" },
                { label: "Sell", value: "sell" },
              ]}
              value={action}
              onChange={setAction}
            />
          </span>

          {politician ? (
            <span className="filter">
              Politician
              <span className="chipval">
                {politician}
                <button
                  type="button"
                  className="x"
                  aria-label="Clear politician filter"
                  onClick={() => setPolitician(null)}
                >
                  <XIcon />
                </button>
              </span>
            </span>
          ) : (
            <span className="filter">
              <input
                placeholder="Filter politician…"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && draft.trim()) {
                    setPolitician(draft.trim());
                    setDraft("");
                  }
                }}
                style={{ height: 36, width: 190, borderRadius: 10, fontSize: 12.5 }}
              />
            </span>
          )}

          <span className="sp" />
          {syncMsg && (
            <span className="faint" style={{ fontSize: 12.5 }}>
              {syncMsg}
            </span>
          )}
          <Button sm onClick={sync} disabled={syncing}>
            <SwapIcon />
            {syncing ? "Syncing…" : "Sync now"}
          </Button>
        </div>

        <div className="reveal" style={{ ["--i" as string]: 2 }}>
          <SignalsTable
            key={reloadKey}
            politician={politician ?? undefined}
            action={action === "all" ? undefined : action}
          />
        </div>

        <div className="foot">
          <b>Odyssey</b> · paper trading · Signals
        </div>
      </div>
    </>
  );
}
