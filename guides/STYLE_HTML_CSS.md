# HTML & CSS Style Guide

## General Rules

- **No HTML emoji characters.** Do not use HTML entity emojis or Unicode emoji symbols in markup.

## Button Placement

- Place button rows in the **upper-right corner** of the containing DOM element.
- Order buttons from **least commonly used on the LEFT** to **most commonly used on the RIGHT** (closest to the screen edge).

**Example button order (left → right):**

```
[ Delete ] [ Kill ] [ Restart ] [ Stop ] [ Start ] [ Edit ]
```

## Tooltips

- Use **DaisyUI tooltips** for contextual help.
- Tooltip triggers should use a **circle with a question mark** icon (see example below).

![Tooltip trigger example](../img/tooltip_question_mark_example.png)

**Example implementation:**

```html
<div class="tooltip" data-tip="Your help text here">
  <button class="btn btn-ghost btn-circle btn-xs">
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="stroke-current w-4 h-4">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
        d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  </button>
</div>
```

## State Change Notifications

- When a **config or setting changes**, display feedback using a **DaisyUI Toast**.

**Example implementation:**

```html
<div class="toast toast-end">
  <div class="alert alert-success">
    <span>Setting updated successfully.</span>
  </div>
</div>
```
