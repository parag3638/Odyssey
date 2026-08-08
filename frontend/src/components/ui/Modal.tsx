"use client";

import type { ReactNode } from "react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { XIcon } from "@/components/icons";
import {
  Dialog as ShadcnDialog,
  DialogPortal,
  DialogOverlay,
  DialogTitle as ShadcnDialogTitle,
} from "./dialog";

/* Overlay modal — wraps shadcn's Dialog (Radix), which natively handles
   Esc-close, backdrop-click-close, background scroll-lock, AND adds a focus
   trap the old hand-rolled version didn't have.

   Bypasses shadcn's own DialogContent composition and uses the lower-level
   DialogPortal/DialogOverlay/DialogPrimitive.Content directly instead — this
   app's .modal-backdrop/.modal/.modal-head classes need to land on those
   exact elements, and DialogContent doesn't expose a way to className the
   overlay it renders internally. Prop API unchanged, no call site needs to
   change. */
export function Modal({
  open,
  onClose,
  title,
  children,
  width = 520,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  width?: number;
}) {
  return (
    <ShadcnDialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogPortal>
        <DialogOverlay className="modal-backdrop" />
        <DialogPrimitive.Content
          className="modal"
          style={{ maxWidth: width }}
          aria-describedby={undefined}
        >
          <div className="modal-head">
            <ShadcnDialogTitle className="modal-title">{title}</ShadcnDialogTitle>
            <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
              <XIcon />
            </button>
          </div>
          <div className="modal-body">{children}</div>
        </DialogPrimitive.Content>
      </DialogPortal>
    </ShadcnDialog>
  );
}
