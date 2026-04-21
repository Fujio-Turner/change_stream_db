# Changes Worker — HTML / CSS Style Guide

## Overview

The admin UI uses **DaisyUI 5.x** (component library for Tailwind CSS) with theme-aware design tokens. All pages share a common sidebar, theme switcher, and consistent visual language.

---

## Technology Stack

- **DaisyUI 5.x** — component classes (`card`, `btn`, `table`, `badge`, `alert`, `modal`, `tabs`, `steps`, etc.)
- **Tailwind CSS** — utility classes for layout, spacing, typography
- **CSS Custom Properties** — DaisyUI theme tokens (`--color-base-100`, `--color-primary`, etc.)
- **Theme System** — `data-theme="dark"` on `<html>`, stored in `localStorage` as `cw_theme`

---

## Page Boilerplate

Every page must include these in `<head>`:

```html
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Changes Worker -- Page Name</title>
  <link rel="icon" type="image/svg+xml" href="/static/favicon.svg" />
  <link href="/static/css/daisyui.css" rel="stylesheet" type="text/css" />
  <link href="/static/css/themes.css" rel="stylesheet" type="text/css" />
  <link href="/static/css/sidebar.css" rel="stylesheet" type="text/css" />
  <script src="/static/js/tailwind.js"></script>
  <script>
    (function(){
      var t = localStorage.getItem('cw_theme') || 'dark';
      document.documentElement.setAttribute('data-theme', t);
    })();
  </script>
</head>
```

---

## Body Structure

```html
<body class="min-h-screen bg-base-200">
  <div id="sidebar-root"></div>
  <div class="sidebar-main">
    <main class="w-full mx-auto p-6 space-y-6">
      <!-- page content here -->
    </main>
  </div>
  <script src="/static/js/sidebar.js"></script>
</body>
```

---

## Cards

The primary content container. Every content section should be wrapped in a card.

### Standard Card

```html
<div class="card bg-base-100 shadow rounded-2xl">
  <div class="card-body p-6">
    <h2 class="text-xl font-bold mb-3">Section Title</h2>
    <p class="opacity-80">Description text...</p>
  </div>
</div>
```

### Compact Card

```html
<div class="card bg-base-100 shadow-sm rounded-2xl">
  <div class="card-body p-4">
    <!-- content -->
  </div>
</div>
```

### Nested Card (inside a card for sub-sections)

```html
<div class="card bg-base-200 rounded-xl">
  <div class="card-body p-3">
    <!-- sub-content -->
  </div>
</div>
```

---

## Pipeline Border Accents

Three-color accent system used throughout to identify pipeline stages:

```css
.border-source  { border-left: 3px solid var(--color-success); }  /* green */
.border-process { border-left: 3px solid var(--color-info); }     /* blue */
.border-output  { border-left: 3px solid var(--color-warning); }  /* amber */
```

Apply to cards:

```html
<div class="card bg-base-100 shadow rounded-2xl border-source">
```

---

## Typography

| Usage             | Class                                                  |
| ----------------- | ------------------------------------------------------ |
| Page title        | `text-2xl font-bold`                                   |
| Section heading   | `text-xl font-bold mb-3`                               |
| Sub-heading       | `text-sm font-semibold`                                |
| Body text         | `opacity-80` (80% opacity, inherits theme color)       |
| Description/helper| `text-sm opacity-60`                                   |
| Label             | `label label-text text-xs font-semibold`               |
| Code inline       | `<code class="bg-base-200 px-1 rounded">code</code>`  |
| Monospace data    | `font-mono text-xs`                                    |

---

## Colors — Theme Tokens (DO NOT hard-code)

❌ **Never use** raw Tailwind colors: `bg-blue-50`, `text-green-500`, `border-red-200`

✅ **Always use** DaisyUI theme tokens:

| Purpose          | Token / Class                                  |
| ---------------- | ---------------------------------------------- |
| Page background  | `bg-base-200`                                  |
| Card background  | `bg-base-100`                                  |
| Nested/inset bg  | `bg-base-200`                                  |
| Borders          | `border-base-300`                              |
| Text (primary)   | inherits from theme                            |
| Text (muted)     | `opacity-60` or `opacity-70`                   |
| Success          | `text-success`, `bg-success`, `border-success` |
| Warning          | `text-warning`, `bg-warning`, `border-warning` |
| Error            | `text-error`, `bg-error`, `border-error`       |
| Info             | `text-info`, `bg-info`, `border-info`          |
| Primary action   | `text-primary`, `bg-primary`, `border-primary` |

### CSS Custom Properties

When referencing in `<style>` blocks:

```css
var(--color-success)       /* green accent */
var(--color-info)          /* blue accent */
var(--color-warning)       /* amber accent */
var(--color-error)         /* red accent */
var(--color-primary)       /* primary brand */
var(--color-base-100)      /* card bg */
var(--color-base-200)      /* page bg */
var(--color-base-300)      /* borders */
var(--color-base-content)  /* text color */
```

### oklch for Opacity Variants in CSS

```css
oklch(var(--p) / 0.15)   /* primary at 15% opacity */
oklch(var(--er) / 0.5)   /* error at 50% opacity */
```

---

## Tables

```html
<div class="overflow-x-auto">
  <table class="table table-sm">
    <thead><tr><th>Header</th><th>Header</th></tr></thead>
    <tbody>
      <tr><td class="font-semibold">Label</td><td>Value</td></tr>
    </tbody>
  </table>
</div>
```

For striped rows: `table-zebra`. For very compact: `table-xs`.

---

## Buttons

| Type           | Class                              |
| -------------- | ---------------------------------- |
| Primary action | `btn btn-primary`                  |
| Success/save   | `btn btn-success`                  |
| Secondary      | `btn btn-ghost` or `btn btn-outline` |
| Small          | add `btn-sm`                       |
| Extra small    | add `btn-xs`                       |
| With loading   | add `loading` class dynamically    |

---

## Badges

Used for section labels, status indicators, counts:

```html
<span class="badge badge-primary badge-outline">Overview</span>
<span class="badge badge-ghost badge-sm">0</span>
<span class="badge badge-success badge-sm">no data loss</span>
```

---

## Alerts

```html
<div class="alert alert-info mt-4 text-sm">
  <span>Informational message here.</span>
</div>
```

Variants: `alert-info`, `alert-warning`, `alert-error`, `alert-success`.

---

## Help Tooltips

Pattern used next to section headings:

```html
<div class="flex items-center gap-2 mb-3">
  <h3 class="text-sm font-semibold">Section Title</h3>
  <div class="tooltip tooltip-right" data-tip="Explanation text.">
    <span class="badge badge-ghost badge-sm cursor-help">?</span>
  </div>
</div>
```

---

## Form Inputs

```html
<label class="label label-text text-xs font-semibold">Label</label>
<input type="text" class="input input-bordered input-sm w-full" />
<select class="select select-bordered select-sm w-full">...</select>
<textarea class="textarea textarea-bordered w-full font-mono text-sm" rows="8"></textarea>
```

Inline help tooltip pattern:

```html
<label class="label label-text text-xs">
  Label
  <div class="tooltip tooltip-bottom inline" data-tip="Help text">
    <span class="opacity-40 cursor-help">(?)</span>
  </div>
</label>
```

---

## Modals (Dialogs)

```html
<dialog id="myModal" class="modal">
  <div class="modal-box w-11/12 max-w-3xl">
    <h3 class="font-bold text-lg mb-4">Modal Title</h3>
    <!-- content -->
    <div class="modal-action">
      <button class="btn btn-success">Action</button>
      <form method="dialog"><button class="btn">Close</button></form>
    </div>
  </div>
  <form method="dialog" class="modal-backdrop"><button>close</button></form>
</dialog>
```

---

## Tabs

```html
<div class="tabs tabs-boxed mb-4">
  <a class="tab tab-active" onclick="switchTab('a')">Tab A</a>
  <a class="tab" onclick="switchTab('b')">Tab B</a>
</div>
```

---

## Progress Steps

```html
<ul class="steps steps-horizontal w-full text-sm">
  <li class="step step-primary">Step 1</li>
  <li class="step">Step 2</li>
</ul>
```

Step states: `step-primary`, `step-success` (done), `step-error` (invalid).

---

## Progress Bar

```html
<div class="w-full bg-base-300 rounded-full h-2 mb-6">
  <div class="bg-primary h-2 rounded-full transition-all" style="width:50%"></div>
</div>
```

---

## Code Blocks / Config Preview

```html
<pre class="bg-base-200 p-4 rounded-xl font-mono text-sm overflow-x-auto whitespace-pre-wrap"></pre>
```

DaisyUI `mockup-code` for styled examples:

```html
<div class="mockup-code bg-success text-success-content">
  <pre data-prefix="#"><code>Comment line</code></pre>
  <pre data-prefix="▸"><code>Command or config line</code></pre>
</div>
```

---

## Grid Layouts

```html
<!-- 3-column responsive grid -->
<div class="grid grid-cols-1 lg:grid-cols-3 gap-6">...</div>

<!-- 2-column form grid -->
<div class="grid grid-cols-1 md:grid-cols-2 gap-4">...</div>

<!-- 2-column card grid (landing/chooser) -->
<div class="grid grid-cols-2 gap-6 max-w-3xl mx-auto">...</div>
```

---

## Dividers

```html
<div class="divider text-xs opacity-60">Section Label</div>
```

---

## Collapsible Sections

```html
<div class="collapse collapse-arrow bg-base-100 shadow-sm rounded-2xl">
  <input type="checkbox" />
  <div class="collapse-title font-semibold text-base">Title</div>
  <div class="collapse-content">
    <!-- content -->
  </div>
</div>
```

---

## Button Placement

### Keep buttons away from edges and corners

Buttons should never sit flush against screen edges or be pinned to far corners. When a pointer is near a screen edge it becomes harder to target accurately (especially on large monitors or with trackpads), and corner-placed primary actions are easy to miss entirely.

### Rules

1. **Primary actions belong inside a card body**, not in a toolbar pinned to the viewport edge.
2. **All action buttons in a group should be the same size** (`btn-sm` or the default). Do not make the primary action physically larger than its siblings — use color/variant to distinguish it instead.
3. **Left-align action groups** when possible. If right-aligning, ensure the button group is inside a card with padding so the button never touches the card edge.
4. **Group related actions together** — don't scatter Save on the right and Cancel on the left of a wide bar.

### ✅ Good — buttons inside card with consistent sizing

```html
<div class="card bg-base-100 shadow-sm rounded-2xl">
  <div class="card-body p-4">
    <!-- ... form content ... -->
    <div class="flex items-center gap-3 mt-4">
      <button class="btn btn-success btn-sm">Save & Apply</button>
      <button class="btn btn-ghost btn-sm">Cancel</button>
      <span class="text-sm opacity-60">Status text</span>
    </div>
  </div>
</div>
```

### ❌ Bad — oversized primary button pushed to far-right edge

```html
<!-- DON'T: different sizes + ml-auto pushes to corner -->
<div class="flex items-center gap-2 ml-auto">
  <button class="btn btn-ghost btn-sm">Secondary</button>
  <button class="btn btn-info btn-md text-base font-bold px-6">Save</button>
</div>
```

### Action bar pattern (when a top toolbar is needed)

Keep all buttons the same size and give the bar enough internal padding:

```html
<div class="card bg-base-100 shadow-sm rounded-2xl">
  <div class="card-body p-4 flex-row items-center gap-3 flex-wrap">
    <div class="flex items-center gap-2">
      <!-- left tools (ghost/icon buttons) -->
    </div>
    <div class="flex items-center gap-2 ml-auto">
      <!-- right actions — all btn-sm -->
      <button class="btn btn-ghost btn-sm">Import</button>
      <button class="btn btn-success btn-sm">Save</button>
    </div>
  </div>
</div>
```

---

## Navigation Pattern — Back Button

```html
<div class="flex items-center justify-between mb-4">
  <button class="btn btn-ghost btn-sm" onclick="goBack()">← Back</button>
  <span class="text-sm opacity-60">Context label</span>
</div>
```

---

## Spacing Conventions

| Context                        | Class / Value     |
| ------------------------------ | ----------------- |
| Page padding                   | `p-6`             |
| Between cards                  | `space-y-6` (on `<main>`) |
| Card body padding (standard)   | `p-6`             |
| Card body padding (compact)    | `p-4`             |
| Card body padding (tight)      | `p-3`             |
| Grid gap (cards)               | `gap-6`           |
| Grid gap (form fields)         | `gap-4`           |
| Grid gap (compact elements)    | `gap-2`           |
| Heading to content             | `mb-3` or `mb-4`  |
| Divider margins                | inherits from DaisyUI |

---

## File References

| File                        | Purpose                                      |
| --------------------------- | -------------------------------------------- |
| `/static/css/daisyui.css`   | DaisyUI component styles                     |
| `/static/css/themes.css`    | Theme definitions (dark, light, cupcake, etc.)|
| `/static/css/sidebar.css`   | Sidebar layout and navigation                |
| `/static/js/tailwind.js`    | Tailwind CSS JIT engine                      |
| `/static/js/sidebar.js`     | Sidebar JS (loaded at end of body)           |
| `/static/js/echarts.min.js` | Charts (only on pages with graphs)           |
| `/static/js/ai-assist.js`   | AI assist popup (only on schema/wizard pages)|

---

## Anti-Patterns

1. ❌ Hard-coded colors (`bg-blue-50`, `text-green-500`, `border-red-200`) — these break in dark theme
2. ❌ Inline `style="color: ..."` for theme colors — use DaisyUI classes
3. ❌ Content outside of cards — all sections should be wrapped in cards
4. ❌ Missing `rounded-2xl` on top-level cards
5. ❌ Using `bg-opacity-10` with semantic colors — use `border-l-4 border-success` for accenting instead
6. ❌ Mismatched heading hierarchy within cards
7. ❌ Non-theme-aware shadows — use DaisyUI `shadow` or `shadow-sm`
8. ❌ Buttons pushed to screen edges/corners with `ml-auto` — keep actions inside padded cards
9. ❌ Making the primary action button physically larger than siblings (`btn-md` next to `btn-sm`) — use color to distinguish, keep sizes uniform
