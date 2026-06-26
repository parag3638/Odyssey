"use client";

import { Nav } from "@/components/Nav";
import { HoldingsTable } from "@/components/HoldingsTable";
import { Card, Skeleton, Stat, StatGrid } from "@/components/ui";
import { usePortfolio } from "@/lib/usePortfolio";
import { initials, money, signClass, signedMoney } from "@/lib/format";
import { SailIcon } from "@/components/icons";

export default function PositionsPage() {
  const pf = usePortfolio();
  const label = pf.account?.label ?? "Trading Claude";

  return (
    <>
      <Nav active="positions" accountLabel={label} accountInitials={initials(label)} />

      <div className="wrap roomy">
        <div className="shead reveal" style={{ ["--i" as string]: 0 }}>
          <span className="flame">
            <SailIcon />
          </span>
          <span className="ttl">Positions</span>
          <span className="sub">
            {pf.hasData ? `${pf.holdings.length} holdings` : "paper account"}
          </span>
        </div>

        <Card pad className="reveal" style={{ marginBottom: 20, ["--i" as string]: 1 }}>
          <StatGrid>
            <Stat
              value={pf.loading ? <Skeleton w={110} h={20} /> : money(pf.balance)}
              label="Total value"
              hl
            />
            <Stat
              value={pf.loading ? <Skeleton w={80} h={20} /> : signedMoney(pf.todayAmount)}
              label="Today's return"
              tone={pf.loading ? "" : signClass(pf.todayAmount)}
            />
            <Stat
              value={pf.loading ? <Skeleton w={80} h={20} /> : signedMoney(pf.allTime.amount)}
              label="All-time return"
              tone={pf.loading ? "" : signClass(pf.allTime.amount)}
            />
            <Stat
              value={pf.loading ? <Skeleton w={96} h={20} /> : pf.cash != null ? money(pf.cash) : "—"}
              label="Cash available"
            />
          </StatGrid>
        </Card>

        <div className="reveal" style={{ ["--i" as string]: 2 }}>
          <HoldingsTable
            holdings={pf.holdings}
            totalValue={pf.balance}
            loading={pf.loading}
            error={pf.error}
          />
        </div>
      </div>
    </>
  );
}
