"use client";

import { useState } from "react";
import type { EarningsDay } from "@/lib/types";
import { IconButton } from "./controls";
import { ChevronLeftIcon, ChevronRightIcon } from "@/components/icons";

/* Earnings calendar — flip-card date tiles (reference #4). */
export function EarningsCalendar({
  days,
  title = "Earnings",
}: {
  days: EarningsDay[];
  title?: string;
}) {
  const [sel, setSel] = useState(() => {
    const t = days.findIndex((d) => d.today);
    return t >= 0 ? t : 0;
  });

  return (
    <div>
      <div className="ecal-head">
        <div className="et">{title}</div>
        <div className="carousel-nav">
          <IconButton
            aria-label="Previous"
            onClick={() => setSel((s) => Math.max(0, s - 1))}
          >
            <ChevronLeftIcon />
          </IconButton>
          <IconButton
            aria-label="Next"
            onClick={() => setSel((s) => Math.min(days.length - 1, s + 1))}
          >
            <ChevronRightIcon />
          </IconButton>
        </div>
      </div>
      <div className="ecal">
        {days.map((d, i) => (
          <button
            key={`${d.dow}-${d.dnum}`}
            type="button"
            className={`eday${i === sel ? " on" : ""}`}
            onClick={() => setSel(i)}
          >
            <div className="ago">{i === sel ? (d.ago ?? "") : ""}</div>
            <div className="dow">{d.dow}</div>
            <div className="dnum">{d.dnum}</div>
            {d.logos.length > 0 ? (
              <div className="logos">
                {d.logos.map((l, li) => (
                  <span key={li} style={{ background: l.color }}>
                    {l.label}
                  </span>
                ))}
              </div>
            ) : (
              <div className="ne">No events</div>
            )}
            {d.events > 0 && (
              <div className="ne">
                <b>{d.events}</b> {d.events === 1 ? "event" : "events"}
              </div>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
