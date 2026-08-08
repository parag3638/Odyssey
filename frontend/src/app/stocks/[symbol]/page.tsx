"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { StockDetailView } from "@/components/StockDetailView";
import { ChevronLeftIcon } from "@/components/icons";

export default function StockPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = (params.symbol || "").toUpperCase();

  return (
    <>
      <Nav active="stocks" accountLabel="my-paper" accountInitials="MY" />

      <div className="wrap wide">
        <Link className="back" href="/stocks">
          <ChevronLeftIcon />
          Stocks
        </Link>

        <StockDetailView symbol={symbol} />
      </div>
    </>
  );
}
