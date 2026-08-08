import type { ComponentProps, InputHTMLAttributes, ReactNode } from "react";
import { Input as ShadcnInput } from "./input";
import { Label as ShadcnLabel } from "./label";

/* New shared form-field primitives — nothing to preserve here (there was no
   shared Input/Field component before this; every form hand-rolled its own
   <div className="field"><label>…</label><input/></div>). Built on shadcn's
   Input/Label; the app's existing bare `input, select {}` / `label {}` CSS
   rules (components.css) are unlayered and apply automatically to any real
   <input>/<label> element regardless of Tailwind classes on it, so these
   wrappers need almost no visual overrides of their own. */

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <ShadcnInput {...props} />;
}

export function Label(props: ComponentProps<typeof ShadcnLabel>) {
  return <ShadcnLabel {...props} />;
}

export function Field({
  label,
  htmlFor,
  children,
  hint,
  error,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
  hint?: string;
  error?: string;
}) {
  return (
    <div className="field">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint && !error && (
        <div className="faint" style={{ fontSize: 11.5 }}>
          {hint}
        </div>
      )}
      {error && (
        <div className="order-status neg" role="status">
          {error}
        </div>
      )}
    </div>
  );
}
