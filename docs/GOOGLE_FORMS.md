# Google Forms Automation in DigitalSoul

DigitalSoul automates Google Forms inspection, review, verified filling, and manual submission approval using `browser-use` over Chrome DevTools Protocol (CDP).

## Key Guarantees

1. **Explicit Review Before Fill**: DigitalSoul inspects the visible form section, extracts question structures (text, paragraph, radio, checkbox, dropdown, grids), and matches them against locally remembered profile data.
2. **Local-Only Markdown Memory**: User profile facts are stored in `.ghost/user_memory.md` (and synced to local `learning.sqlite3`). No identity data is ever sent to external cloud servers or manufactured.
3. **Verified Re-Readback**: After setting values in the DOM, DigitalSoul fires native input/change events and verifies that the field values match before proceeding.
4. **Guarded Multi-Section Progression**: If a form has multiple pages/sections, DigitalSoul advances to the next section and re-inspects, presenting each section for review.
5. **No Blind Submissions**: The final form submission requires an explicit one-time cryptographic approval token (`approval_token`) and confirmation from the user. Duplicate or accidental submissions are blocked by an SHA-256 submission journal in `.ghost/gform_submissions/`.

## Upstream References & Adaptations

- **`browser-use/browser-use`**: Used as the primary browser execution engine via local CDP/Actor sessions. Playwright is not used.
- **`Nir-Bhay/google-forms-autofill-extension`**: Reference for container and selector detection patterns (`[role="listitem"]`, `.Qr7Oae`, `.M7eMe`, etc.). Selectively adapted into pure JavaScript inspection scripts (`gform_fields.js`).
- **`muratalperen/GoogleFormsAutoFiller`**: Reference for fuzzy matching and field alias mapping (`FIELD_ALIASES`).

## Architecture & Files

- `agent/ghost/browser/gform_fields.js`: Injected page script for reliable, deterministic inspection and DOM manipulation without generated code.
- `agent/ghost/browser/gform_fields.py`: Python wrapper executing `gform_fields.js` inside the active `browser-use` page.
- `agent/ghost/browser/gform_actor.py`: Multi-step actor managing browser lifecycle, section snapshots, verified filling, and submission journaling.
- `agent/ghost/browser/gform_memory.py`: Markdown parser and fuzzy matcher reading/writing `.ghost/user_memory.md`.
- `agent/ghost/desktop/notch_bridge.py`: RPC bridge exposing `gform_inspect`, `gform_run`, `gform_submit`, `gform_memory_get`, and `gform_memory_save` to the Tauri frontend.
- `desktop/ui/app.js`: Notch UI for reviewing remembered answers vs required answers before automation.

## Supported Field Types

| Field Type | DOM Role / Tag | Verification Strategy |
|---|---|---|
| Text / Paragraph | `<input>`, `<textarea>` | Prototype property descriptor setter + input/change dispatch + value equality |
| Radio Group | `[role="radio"]`, `input[type="radio"]` | Click target option + verify `aria-checked="true"` |
| Checkbox Set | `[role="checkbox"]`, `input[type="checkbox"]` | Click each selected option + verify sorted array match |
| Dropdown | `<select>`, `[role="listbox"]` | Native option select or listbox popover click + value verification |
| Choice Grid | `[role="row"]` with radio/checkbox | Row-by-row selection + row label verification |
| File Upload | `input[type="file"]` | Marked as requiring manual input (safe fail) |
