"use client";

import { useEffect, useMemo, useState } from "react";
import { Nav } from "@/components/Nav";
import { Card } from "@/components/ui";
import { listActivity, type Activity } from "@/lib/api";
import { initials } from "@/lib/format";
import {
  CheckIcon,
  AlertCircleIcon,
  InfoIcon,
  SparklesIcon,
} from "@/components/icons";

function timelineText(event: string, detail: Record<string, unknown>) {
  const symbol = typeof detail.symbol === "string" ? detail.symbol : "";
  const price = typeof detail.price === "number" ? detail.price : null;
  const floor = typeof detail.stop_floor === "number" ? detail.stop_floor : null;

  const moneyFmt = (n: number) => `$${n.toFixed(2)}`;

  switch (event) {
    case "tick":
      return (
        <>
          <b>Tick</b> {symbol}
          {price !== null ? ` @ ${moneyFmt(price)}` : ""}
          {floor !== null ? ` · floor ${moneyFmt(floor)}` : ""}
        </>
      );
    case "tick_action_skipped":
      return (
        <>
          <b>Skipped action</b>{" "}
          {typeof detail.reason === "string" ? detail.reason : symbol}
        </>
      );
    case "order_filled":
      return (
        <>
          <b>Filled</b> {symbol}
          {price !== null ? ` @ ${moneyFmt(price)}` : ""}
        </>
      );
    default:
      return (
        <>
          <b>{event}</b> {symbol}
        </>
      );
  }
}

function levelIcon(level: string) {
  switch (level) {
    case "info":
      return <InfoIcon />;
    case "warning":
      return <AlertCircleIcon />;
    case "error":
      return <AlertCircleIcon />;
    case "success":
      return <CheckIcon />;
    default:
      return <SparklesIcon />;
  }
}

function relativeTime(isoDate: string | null): string {
  if (!isoDate) return "—";
  const date = new Date(isoDate);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;

  return date.toLocaleDateString();
}

function formatDate(isoDate: string | null): string {
  if (!isoDate) return "—";
  const date = new Date(isoDate);
  return date.toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

export default function ActivityPage() {
  const [activities, setActivities] = useState<Activity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await listActivity(100);
        if (!cancelled) setActivities(data);
      } catch (e) {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "Failed to load activity.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Group activities by day (most recent first)
  const groupedByDay = useMemo(() => {
    const groups: Record<string, Activity[]> = {};
    for (const a of activities) {
      const dateStr = formatDate(a.created_at);
      if (!groups[dateStr]) groups[dateStr] = [];
      groups[dateStr].push(a);
    }
    return groups;
  }, [activities]);

  const dayLabels = useMemo(() => {
    if (activities.length === 0) return [];
    const dates = new Set<string>();
    for (const a of activities) {
      dates.add(formatDate(a.created_at));
    }
    // Sort dates: today first, then backwards
    const sorted = Array.from(dates).sort((a, b) => {
      const aDate = new Date(a);
      const bDate = new Date(b);
      return bDate.getTime() - aDate.getTime();
    });
    return sorted;
  }, [activities]);

  return (
    <>
      <Nav active="activity" accountLabel="Trading Claude" accountInitials={initials("Trading Claude")} />

      <div className="wrap roomy">
        <div className="shead reveal" style={{ ["--i" as string]: 0 }}>
          <span className="ttl">Activity</span>
          <span className="sub">
            {loading ? "loading…" : `${activities.length} events`}
          </span>
        </div>

        {error && (
          <Card pad className="reveal" style={{ marginBottom: 20, ["--i" as string]: 1 }}>
            <div className="neg">{error}</div>
          </Card>
        )}

        {loading && (
          <div className="reveal" style={{ ["--i" as string]: 1 }}>
            <div className="acard">
              <div className="act-row">
                <div className="ai">
                  <div className="skeleton" style={{ width: 16, height: 16 }} />
                </div>
                <div className="ab">
                  <div className="a1">
                    <div
                      className="skeleton"
                      style={{ width: 120, height: 14 }}
                    />
                  </div>
                  <div className="a2">
                    <div
                      className="skeleton"
                      style={{ width: 80, height: 12, marginTop: 4 }}
                    />
                  </div>
                </div>
                <div className="ar">
                  <div
                    className="skeleton"
                    style={{ width: 50, height: 12 }}
                  />
                </div>
              </div>
            </div>
          </div>
        )}

        {!loading && activities.length === 0 && (
          <Card pad className="reveal" style={{ marginBottom: 20, ["--i" as string]: 1 }}>
            <div className="faint">No activity yet. Run a bot tick to begin.</div>
          </Card>
        )}

        {!loading && activities.length > 0 && (
          <div className="reveal" style={{ ["--i" as string]: 1 }}>
            {dayLabels.map((dateStr) => (
              <div key={dateStr}>
                <div className="day">{dateStr}</div>
                <div className="acard">
                  {groupedByDay[dateStr].map((activity, rowIdx) => (
                    <div
                      key={`${dateStr}-${rowIdx}`}
                      className="act-row"
                      style={{ ["--i" as string]: rowIdx }}
                    >
                      <div
                        className="ai"
                        title={activity.level}
                        style={{
                          color:
                            activity.level === "success"
                              ? "var(--gain)"
                              : activity.level === "error"
                                ? "var(--loss)"
                                : "var(--text-2)",
                        }}
                      >
                        {levelIcon(activity.level)}
                      </div>
                      <div className="ab">
                        <div className="a1">
                          {activity.bot_name && (
                            <span style={{ color: "var(--text-2)" }}>
                              {activity.bot_name}
                            </span>
                          )}
                          {timelineText(activity.event, activity.detail)}
                        </div>
                        <div className="a2">
                          {activity.symbol && (
                            <span>{activity.symbol}</span>
                          )}
                        </div>
                      </div>
                      <div className="ar">
                        <div className="at">{relativeTime(activity.created_at)}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="foot">
          <b>Odyssey</b> · paper trading · Activity
        </div>
      </div>
    </>
  );
}
