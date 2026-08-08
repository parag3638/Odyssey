"use client";

import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { ResearchSearch } from "@/components/ResearchSearch";
import { StockDetailView } from "@/components/StockDetailView";
import { ChevronLeftIcon } from "@/components/icons";

export default function StockPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = (params.symbol || "").toUpperCase();
  const router = useRouter();

  return (
    <>
      <Nav active="stocks" accountLabel="my-paper" accountInitials="MY" />

      <div className="wrap roomy">
        <Link className="back" href="/stocks">
          <ChevronLeftIcon />
          Stocks
        </Link>

        <div style={{ maxWidth: 480, marginBottom: 20 }}>
          <ResearchSearch onSelect={(s) => router.push(`/stocks/${s}`)} />
        </div>

        <StockDetailView symbol={symbol} />
      </div>
    </>
  );
}
