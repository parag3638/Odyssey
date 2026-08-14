"use client";

import { usePathname } from "next/navigation";
import { Nav } from "@/components/Nav";

export function AppNavigation() {
  const pathname = usePathname();
  if (pathname.startsWith("/design-system")) return null;

  const active = pathname.startsWith("/stocks")
    ? "stocks"
    : pathname.startsWith("/research")
      ? "research"
      : pathname.startsWith("/positions")
        ? "positions"
        : pathname.startsWith("/signals")
          ? "signals"
          : pathname.startsWith("/activity")
            ? "activity"
            : "overview";

  return <Nav active={active} />;
}
