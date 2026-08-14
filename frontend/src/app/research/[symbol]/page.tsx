"use client";

import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { ResearchSearch } from "@/components/ResearchSearch";
import { StockDetailView } from "@/components/StockDetailView";
import { ChevronLeftIcon } from "@/components/icons";

export default function ResearchSymbolPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = (params.symbol || "").toUpperCase();
  const router = useRouter();

  return (
    <>
      <Nav active="research" accountLabel="my-paper" accountInitials="MY" fetchAccount={false} />

      <div className="wrap roomy">
        <Link className="back" href="/research" prefetch={false}>
          <ChevronLeftIcon />
          Research
        </Link>

        <div style={{ maxWidth: 480, marginBottom: 20 }}>
          <ResearchSearch onSelect={(s) => router.push(`/research/${s}`)} />
        </div>

        <StockDetailView key={symbol} symbol={symbol} />
      </div>
    </>
  );
}
