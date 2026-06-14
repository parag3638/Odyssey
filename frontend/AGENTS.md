<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# UI / design

For ANY UI work, read **`design/DESIGN_SYSTEM.md`** first and match it. Reuse the design
tokens in `src/app/styles/tokens.css` and the component library in `src/components/ui/`
(barrel: `ui/index.ts`) before writing new markup. Browse every component live at the
`/design-system` route. Dark is default; verify light too. Numbers use `lib/format.ts`
(tabular figures, dimmed cents, signed return pills).
