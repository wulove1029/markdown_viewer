# Markdown Viewer UI/UX Improvement Design

Date: 2026-05-15
Status: Draft for user review

## Context

Markdown Viewer is a Windows desktop Markdown previewer built with PyQt6 and
PyQt6-WebEngine. The current UI uses an Obsidian-like left ribbon, a left panel,
and a central WebEngine preview. There is no existing `DESIGN.md` or design token
source in the repository.

Target users:

- Developers and technical writers who need fast Markdown preview on Windows.
- Office users who read documents, notes, and manuals.

Primary goal:

- Improve the whole experience, prioritizing the details that most affect daily
  use: reading comfort, navigation clarity, search/open-file efficiency, visual
  consistency, clear states, and light/dark mode.

## Product Positioning

The app should become a quiet Markdown reading workspace. It should not feel like
a full IDE or a pure document viewer. The design principle is:

> Open fast, read steadily, find content quickly, and make every state clear.

Out of scope for this pass:

- Markdown editing.
- Multi-tab document management.
- Full-text indexing.
- Language switching/i18n.
- A full project workspace model.

## Information Architecture

The redesigned workspace has three primary regions.

### Top Toolbar

The toolbar is a stable, low-distraction command surface, about 48px tall.

Expected actions:

- Open Markdown file.
- Search.
- Toggle light/dark theme.
- Reload current file.
- Check for updates.

The toolbar should use icon buttons with Traditional Chinese tooltips and
accessible names. Buttons must use SVG icons, not emoji or font-dependent glyphs.

### Left Work Panel

The left panel replaces the current activity-ribbon-first structure with direct
tabs:

- Files.
- Recent.
- Outline.

The panel should be collapsible. When collapsed, a 44px restore button remains
available so users can recover the panel without hunting for hidden UI.

### Reading Area

The reading area is the visual priority. It should provide stable line length,
comfortable spacing, and strong styling for technical documents:

- Headings with clear hierarchy.
- Code blocks with readable contrast.
- Tables that scan cleanly.
- Blockquotes, inline code, links, task lists, images, and horizontal rules.
- Empty, loading, and error pages using the same visual system.

## Visual Direction

Style: quiet professional desktop productivity.

This intentionally rejects:

- Heavy glassmorphism.
- Marketing-style hero layouts.
- Decorative gradient blobs.
- Nested card-heavy layouts.
- A single purple-only palette.
- Emoji as structural icons.

The UI should feel native enough for Windows while still more polished than a
default PyQt app.

## Color System

Use semantic design tokens instead of raw per-widget colors.

Light theme:

- Background: warm off-white, close to paper but not beige-heavy.
- Surface: neutral light gray for panels and toolbars.
- Border: low-contrast gray with enough visibility.
- Text primary: near-black.
- Text secondary: readable gray.
- Accent: blue-leaning indigo for focus, selection, links, and primary actions.
- Info/success support: teal/cyan family for non-primary positive states.
- Error: accessible red with text and icon/label, never color alone.

Dark theme:

- Background: deep gray-blue, not pure black.
- Surface: slightly raised dark gray.
- Border: visible low-luminance separator.
- Text primary: high-contrast off-white.
- Text secondary: muted but still readable.
- Accent: lighter indigo tuned for dark surfaces.
- Code blocks remain dark but distinct from the page background.

Contrast requirements:

- Main text: WCAG AA, at least 4.5:1.
- Large text and large UI glyphs: at least 3:1.
- Focus, selected, disabled, hover, active, and error states must remain
  distinguishable in both themes.

## Typography

UI font:

- `Segoe UI`, with normal Windows system fallbacks.

Markdown body:

- `Segoe UI`, `Inter`, `-apple-system`, `BlinkMacSystemFont`, `sans-serif`.

Code:

- `Cascadia Code`, `Fira Code`, `Consolas`, monospace.

Hierarchy:

- Body text minimum 16px in the rendered document.
- Panel/list text no smaller than 13px.
- Labels use 600 weight.
- Headings use size, weight, and spacing rather than relying on accent color.
- Letter spacing remains default; do not use tight negative tracking.

## Spacing And Density

Base spacing unit: 4px, with an 8px rhythm for most component spacing.

Key dimensions:

- Toolbar height: about 48px.
- Icon button hit target: minimum 44x44px.
- Panel padding: 12px or 16px depending on density.
- Reading area desktop padding: about 48px.
- Rendered line length: constrained for long-form readability.

The app is a desktop app, but the 44px hit target still matters because it makes
mouse use more forgiving and aligns with accessibility requirements.

## Interaction Design

### Search

Search remains accessible through `Ctrl+F`; `Esc` closes it.

The search UI should include:

- Search input.
- Previous and next buttons.
- Close button.
- Empty query state.
- No-results state.
- Clear focus style.

### Panel Navigation

The left panel tabs should make the current section obvious. File, recent, and
outline lists should share the same hover, selected, focus, and disabled logic.

### Theme

The app should support light and dark themes as first-class modes, not by simple
color inversion. The rendered Markdown CSS and PyQt widget styles must switch
together.

### File Opening

Users can open a file through:

- Toolbar open button.
- Keyboard shortcut `Ctrl+O`.
- Drag and drop.
- File tree click.
- Recent file click.

The empty state should include an open-file action and drag/drop hint.

## State Design

Required states:

- Empty state: no file loaded, with open-file CTA and drag/drop hint.
- Loading state: clear progress or skeleton-style feedback while a file is
  loading/rendering.
- Error state: specific reason and recovery action.
- Disabled state: controls that cannot run without a loaded file should look and
  behave disabled.
- Update check state: checking, no update, update available, failure.

Error reasons to distinguish:

- File not found.
- File too large.
- Unsupported encoding.
- Update check failure.
- Update download failure.

## Accessibility Requirements

- All icon-only controls must have tooltip and accessible name.
- Keyboard focus must be visible.
- Tab order should follow visual order.
- Buttons and controls must not rely on hover only.
- Color must not be the only indicator of errors or selection.
- Main text contrast must be at least 4.5:1 in light and dark modes.
- Reduced-motion preference should be respected for rendered smooth scrolling
  where feasible.

## Implementation Direction

Expected implementation shape:

- Introduce a UI theme module with semantic tokens and reusable style helpers.
- Replace emoji/font-symbol buttons with SVG icons.
- Rework `MainWindow` layout from ribbon-first to toolbar + side panel + reader.
- Update `LeftPanel` into a tabbed workspace panel.
- Update list/tree styles to use shared tokens and consistent states.
- Update rendered Markdown CSS for light/dark themes.
- Add empty/loading/error HTML states through the renderer.
- Keep existing conversion, recent files, file browser, update check, and outline
  behavior unless a small adjustment is needed for the new UI.

## Verification Plan

- Run Python syntax checks for changed modules.
- Launch the app locally and inspect the main empty state.
- Open a Markdown file and verify file tree, recent files, outline, search, and
  theme switching.
- Verify error pages by opening a missing path or unsupported/oversized input
  where practical.
- Check that small controls have at least 44x44px hit targets.
- Check contrast for primary text, secondary text, selected states, and links in
  both themes.

## Most Easily Missed UX Details

- Tiny icon buttons that look polished but are hard to hit.
- Disabled actions that still look active.
- Dark mode only applied to widgets while the rendered document stays light.
- Search with no clear no-results feedback.
- Empty state that tells users what is wrong but gives no action.
- Focus rings removed during visual cleanup.
