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
| Board and metrics | `.terminal-board-heading`, `.terminal-compact` and `.terminal-model-inputs` organize density and model context. The table uses the existing bullet graph and the cards use the existing distribution graphic. Expected catches and yards per catch only render where both model parameters exist. |
| Search | `.terminal-search-dialog` uses the existing board filter and supports Search, `/`, and Ctrl/Command+K. Submitting moves focus to the board heading. |
| Filters and simulation | The existing filter sheet and `.terminal-sim-sheet` use native dialogs and the shared swipe handler. The selected card's simulator is mounted in the mobile sheet and stays inline on desktop. |
| Loading and actions | Shared skeleton markup, quote-status states, copy confirmation, and ledger state use the terminal tokens. The status animation is finite and respects reduced motion. |

All quote states and simulation outputs derive from the existing data and model paths. A model fair line remains separate from a sportsbook quote. The sheet uses the published range and the existing approximate simulator.

## Preview checks

Run `npm run check` and the cache-buster check, then load the production viewer at phone and desktop widths. Open the board, change leagues and slates, search for a player, switch Grid/Table and density, and use Show more. On a phone, check touch targets, swipe dismissal and focus restoration on both sheets. Verify stale and missing prices on a known snapshot and confirm copy feedback in a secure browser context.
