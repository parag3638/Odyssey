"use client";

import Link from "next/link";
import { Nav } from "@/components/Nav";
import { StocksTable } from "@/components/StocksTable";
import { Button, EmptyState } from "@/components/ui";
import { useWatchlist } from "@/lib/useWatchlist";
import { SailIcon, StarIcon } from "@/components/icons";

export default function WatchlistPage() {
  const { rows, loading, isStarred, toggle } = useWatchlist();

  return (
    <>
      <Nav active="watchlist" accountLabel="my-paper" accountInitials="MY" />

      <div className="wrap roomy">
        <div className="shead reveal" style={{ ["--i" as string]: 0 }}>
          <span className="flame">
            <SailIcon />
          </span>
          <span className="ttl">Watchlist</span>
          <span className="sub">{rows.length} followed</span>
        </div>

        <div className="reveal" style={{ ["--i" as string]: 1 }}>
          {!loading && rows.length === 0 ? (
            <div className="tcard">
              <EmptyState
                icon={<StarIcon />}
                title="No stocks followed yet"
                desc="Star stocks from the Stocks page or any stock view to track them here."
                action={
                  <Link href="/stocks">
                    <Button sm>Browse stocks</Button>
                  </Link>
                }
              />
            </div>
          ) : (
            <StocksTable
              rows={rows}
              loading={loading}
              isStarred={isStarred}
              onToggleStar={toggle}
              empty="No stocks followed yet."
            />
          )}
        </div>

        <div className="foot">
          <b>Odyssey</b> · research · Watchlist
        </div>
      </div>
    </>
  );
}
