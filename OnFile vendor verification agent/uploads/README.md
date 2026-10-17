# OnFile — simplified design pack (v16)

10 screens, one visual system, three ways to use them.

## What's here
- `prototype.html` — open in any browser. Clickable prototype: every button, row and link navigates. Starts on Payments.
- `screens/*.html` — the same 10 screens as standalone files (inline CSS) for Figma import. Links between them work in a browser too.
- `screens/index.json` — title, width, height and suggested canvas position of each frame.
- `tokens/` — colours, type, radii, spacing as CSS variables, Tokens Studio JSON, and plain JSON.
- `PROTOTYPE-MAP.md` — every hotspot: screen → element → destination, plus the model event it fires. Use it to wire Figma prototype links.
- `EVALUATION.md` — screen-by-screen review of v15 and what changed.

## Figma
1. Plugin **html.to.design** → Upload HTML → one file from `screens/` at a time. Use `index.json` for frame sizes.
2. Plugin **Tokens Studio for Figma** → Import → `tokens/tokens-studio.json`.
3. Prototype tab: add interactions from `PROTOTYPE-MAP.md` (On click → Navigate to). HTML can't carry Figma interactions, so this step is manual; the map lists all 61 links.

Fonts: Inter (Google Fonts). Be online when importing.

## Rules that travel with the design
- Buttons carry `data-event="…"` = the model event from `state_model.json → screen_actions`. Labels were rewritten in plain language; the event attribute is the stable key.
- Anything an AP user doesn't need to finish the task lives in Settings → Advanced (admins) or in the downloadable record (auditors). Don't bring it back to the task screens.
- Status colours are the only semantic colours: ok (green), warn (amber), crit (red), info (brand blue).
