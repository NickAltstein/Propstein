# PropStein terminal UI

The production terminal is the static app under `viewer/`. The Next.js app under `terminal/` has its own Tailwind build. The production entry point loads `terminal-ui.css` after the shared viewer styles and scopes the new components to `.terminal-ui`.

## Layout audit

- The desktop league switcher occupied its own header row. Several top-bar actions were already visually hidden, and the visible search fallback pointed to an input hidden by the design system.
- The board showed dataset, market, position, alerts, and filters in one strip. On phones the filter entry could scroll out of view and the position dropdown could be clipped.
- Three stylesheets set overlapping surface colors and component dimensions, often with `!important`. The terminal layer uses semantic tokens and localized overrides while those shared styles still serve other viewer pages.
- The table and card layouts already supported distinct views and existing quantitative graphics. The refactor uses that rendering path and its card pagination.

## Components

| Component | Contract |
| --- | --- |
| Header | `.terminal-leagues`, `.terminal-control` and `.terminal-search-trigger` group league, slate, search and menu controls. The existing league handlers remain the source of sport routing. |
| Board controls | `.terminal-board-controls` keeps Filters beside the scrolling quick filters. `.terminal-quote-status` uses the feed verification state and the recorded feed timestamp. |
| Board and metrics | `.terminal-board-heading`, `.terminal-compact` and `.terminal-model-inputs` organize density and model context. The table, cards, and simulator share the same distribution curve geometry. Expected catches and yards per catch only render where both model parameters exist. |
| Search | `.terminal-search-dialog` uses the existing board filter and supports Search, `/`, and Ctrl/Command+K. Submitting moves focus to the board heading. |
| Filters and simulation | The existing filter sheet and `.terminal-sim-sheet` use native dialogs and the shared swipe handler. The selected card's simulator is mounted in the mobile sheet and stays inline on desktop. |
| Loading and actions | Shared skeleton markup, quote-status states, copy confirmation, and ledger state use the terminal tokens. The status animation is finite and respects reduced motion. |

All quote states and simulation outputs derive from the existing data and model paths. A model fair line remains separate from a sportsbook quote. The sheet uses the published range and the existing approximate simulator.

## Preview checks

Run `npm run check` and the cache-buster check, then load the production viewer at phone and desktop widths. Open the board, change leagues and slates, search for a player, switch Grid/Table and density, and use Show more. On a phone, check touch targets, swipe dismissal and focus restoration on both sheets. Verify stale and missing prices on a known snapshot and confirm copy feedback in a secure browser context.

## Obsidian Desk review (2026-11-16)

The terminal now uses dark surfaces, Inter text, JetBrains Mono data, semantic quote/warning/verified colors, and 44px portraits with an 18px edge chip. The shared styles still serve the research pages; the old top-bar hiding rule excludes `.terminal-ui`. The production viewer forces dark mode while leaving other viewer pages' theme behavior intact.

Screenshots for each stage are saved locally in `artifacts/obsidian-review/` (ignored by Git):

| Stage | Review and correction |
| --- | --- |
| 1. Avatar | The first screenshots showed the logo clear of the face but legacy card rules reduced the portrait to 42px on mobile and enlarged it to 52px on desktop. The final scoped rule fixes both at 44px; the chip measures 18px. |
| 2. Tokens | The first phone screenshot exposed a 96px horizontal overflow from the quick-filter strip. Its scrolling container now clips its own chips, leaving document overflow at 0px. |
| 3. Header and dock | The condensed header initially measured 100–101px because the old filter margin remained. Removing that margin yields 96px at 390px and 1440px. The saved/record pill remains hidden until an item is staged. |
| 4. Curves | Earlier charts had a blue glow and bar-style compact marks. Cards, table rows, and the simulator now use the same curve; the projection tick is chartreuse, fair tick blue, and verified book tick amber. Pointer movement over a full curve shows an explicitly approximate P(over). |
| 5. Verification | The current local slate has stale prices and no verified card price, so screenshots show the warning border and research state. Receipt chips render only when a verified quote carries an actual SHA-256; this review did not synthesize a live receipt. The final overlay check found older blue simulator controls, which were changed to neutral surface controls. |

The final local browser pass covered 360, 390, 768, 1280, 1440, and 1680px widths with no document overflow or page errors. A stored light preference still produced the dark terminal. The Filters dialog opened and closed by Escape; the mobile trigger measured 44px. Search and simulation sheets restored focus to their triggers on Escape. The final 390px and 1440px screenshots are `final-390.png` and `final-1440.png` in the same local review folder.
