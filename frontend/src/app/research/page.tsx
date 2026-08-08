"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Nav } from "@/components/Nav";
import { Skeleton } from "@/components/ui";
import { listStocks } from "@/lib/api";

/** Landing at /research always resolves to a real stock — defaults to the
 *  largest by market cap (the API's default sort) — rather than showing an
 *  empty "search first" state. */
export default function ResearchLandingPage() {
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    listStocks({ limit: 1 })
      .then((rows) => {
        if (cancelled) return;
        router.replace(`/research/${rows[0]?.symbol ?? "AAPL"}`);
      })
      .catch(() => !cancelled && router.replace("/research/AAPL"));
    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <>
      <Nav active="research" accountLabel="my-paper" accountInitials="MY" />
      <div className="wrap roomy">
        <div className="tcard" style={{ padding: "22px" }}>
          <Skeleton w={220} h={20} />
          <Skeleton w={140} h={36} style={{ marginTop: 16 }} />
        </div>
      </div>
    </>
  );
}
