"use client";

import { useRouter } from "next/navigation";
import { Nav } from "@/components/Nav";
import { ResearchSearch } from "@/components/ResearchSearch";
import { EmptyState } from "@/components/ui";
import { ResearchIcon } from "@/components/icons";

export default function ResearchLandingPage() {
  const router = useRouter();

  return (
    <>
      <Nav active="research" accountLabel="my-paper" accountInitials="MY" />

      <div className="wrap roomy">
        <div className="shead reveal" style={{ ["--i" as string]: 0 }}>
          <span className="ttl">Research</span>
          <span className="sub">Fundamentals and news for any stock</span>
        </div>

        <div className="reveal" style={{ ["--i" as string]: 1, maxWidth: 480 }}>
          <ResearchSearch autoFocus onSelect={(symbol) => router.push(`/research/${symbol}`)} />
        </div>

        <div className="reveal" style={{ ["--i" as string]: 2, marginTop: 24 }}>
          <div className="tcard">
            <EmptyState
              icon={<ResearchIcon />}
              title="Search a stock to get started"
              desc="Pull up fundamentals — P/E, earnings, analyst ratings, dividends — and the latest news, all in one place."
            />
          </div>
        </div>
      </div>
    </>
  );
}
