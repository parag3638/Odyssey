"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { Dialog as DialogPrimitive } from "radix-ui";
import {
  Dialog as ShadcnDialog,
  DialogPortal,
  DialogOverlay,
  DialogTitle as ShadcnDialogTitle,
} from "@/components/ui/dialog";
import { ArrowUpRightIcon, SparklesIcon, XIcon } from "@/components/icons";
import { streamAssistant, type ChatTurn, type Citation } from "@/lib/api";

/* Global, context-aware AI assistant — a right-side slide-in drawer. Streams
   grounded answers (with citation chips) about the stock you're viewing and your
   portfolio. Reuses the app's Radix dialog plumbing + design tokens. */

interface Msg {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  pending?: boolean;
}

function symbolFromPath(path: string | null): string | null {
  if (!path) return null;
  const m = path.match(/^\/(?:research|stocks)\/([^/]+)/);
  return m ? decodeURIComponent(m[1]).toUpperCase() : null;
}

/* Strip inline [n1]/[pf] citation markers from the displayed prose (chips carry them). */
function displayText(s: string): string {
  return s.replace(/\s?\[[a-z]+\d*\]/g, "");
}

function suggestions(symbol: string | null): string[] {
  return symbol
    ? [
        `What's the latest on ${symbol}?`,
        `Bull vs bear case for ${symbol}?`,
        `Any congressional trades in ${symbol}?`,
      ]
    : [
        "How is my portfolio doing today?",
        "Which holding carries the most risk?",
        "What changed in my account recently?",
      ];
}

export function AiAssistant({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pathname = usePathname();
  const symbol = useMemo(() => symbolFromPath(pathname), [pathname]);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  // Keep the transcript scrolled to the newest content.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // Focus the composer once the drawer opens.
  useEffect(() => {
    if (!open) return;
    const id = setTimeout(() => taRef.current?.focus(), 80);
    return () => clearTimeout(id);
  }, [open]);

  const send = useCallback(
    async (raw: string) => {
      const q = raw.trim();
      if (!q || streaming) return;
      setInput("");
      if (taRef.current) taRef.current.style.height = "auto";
      const history: ChatTurn[] = [
        ...messages.map((m) => ({ role: m.role, content: m.content })),
        { role: "user", content: q },
      ];
      setMessages((prev) => [
        ...prev,
        { role: "user", content: q },
        { role: "assistant", content: "", pending: true },
      ]);
      setStreaming(true);

      const { text, citations } = await streamAssistant(history, { symbol }, (tok) => {
        setMessages((prev) => {
          const next = prev.slice();
          const last = next[next.length - 1];
          if (last && last.role === "assistant") {
            next[next.length - 1] = { ...last, content: last.content + tok };
          }
          return next;
        });
      });

      setMessages((prev) => {
        const next = prev.slice();
        const last = next[next.length - 1];
        if (last && last.role === "assistant") {
          next[next.length - 1] = {
            role: "assistant",
            content:
              text ||
              "The assistant isn't available right now — add an OpenAI key (and connect an account for portfolio questions).",
            citations,
            pending: false,
          };
        }
        return next;
      });
      setStreaming(false);
    },
    [messages, streaming, symbol],
  );

  return (
    <ShadcnDialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogPortal>
        <DialogOverlay className="aichat-backdrop" />
        <DialogPrimitive.Content
          className="aichat-drawer"
          aria-describedby={undefined}
          onOpenAutoFocus={(e) => e.preventDefault()}
        >
          <div className="aichat-head">
            <ShadcnDialogTitle className="aichat-title">
              <SparklesIcon /> Assistant
            </ShadcnDialogTitle>
            <span className="aichat-ctx">{symbol ? `on ${symbol}` : "your portfolio"}</span>
            <span className="sp" />
            <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
              <XIcon />
            </button>
          </div>

          <div className="aichat-body" ref={listRef}>
            {messages.length === 0 ? (
              <div className="aichat-empty">
                <div className="greet">
                  <SparklesIcon /> Ask about {symbol ? symbol : "your portfolio"}
                </div>
                <div className="sub">
                  I answer from this app&apos;s data — news, analyst ratings, congressional trades,
                  and your holdings — and cite my sources. Information only, not advice.
                </div>
                <div className="aichat-sugs">
                  {suggestions(symbol).map((s) => (
                    <button key={s} type="button" className="aichat-sug" onClick={() => send(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((m, i) => (
                <div key={i} className={`aimsg ${m.role} reveal`}>
                  {m.role === "assistant" && m.pending && !m.content ? (
                    <div className="aimsg-bubble">
                      <span className="aitype" aria-label="Assistant is typing">
                        <i />
                        <i />
                        <i />
                      </span>
                    </div>
                  ) : (
                    <div className="aimsg-bubble">{displayText(m.content)}</div>
                  )}
                  {m.role === "assistant" && m.citations && m.citations.length > 0 && (
                    <div className="aisum-cites">
                      {m.citations.map((c) => (
                        <a
                          key={c.id}
                          className="aisum-chip"
                          href={c.url}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {c.label}
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>

          <form
            className="aichat-composer"
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
          >
            <textarea
              ref={taRef}
              className="aichat-input"
              rows={1}
              placeholder="Ask anything…"
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                const el = e.target;
                el.style.height = "auto";
                el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(input);
                }
              }}
            />
            <button
              type="submit"
              className="icon-btn round aichat-send"
              aria-label="Send"
              disabled={streaming || !input.trim()}
            >
              <ArrowUpRightIcon />
            </button>
          </form>
        </DialogPrimitive.Content>
      </DialogPortal>
    </ShadcnDialog>
  );
}
