# Widget integration

The production container serves the custom element bundle and its optimized
avatar media from `/widget`. A host page needs one module script and one element:

```html
<script type="module" src="https://BOT_HOST/widget/flamingo-chat.js"></script>
<flamingo-chat></flamingo-chat>
```

When the bundle and API share an origin, `api-base` is optional. For a separate
API origin or a local preview, configure it explicitly:

```html
<flamingo-chat
  api-base="https://BOT_API_HOST"
  button-label="Ask Flamingo"
></flamingo-chat>
```

`asset-base` can point at a separately hosted media directory. By default it is
the `media/` directory beside the JavaScript bundle.

Add every real host origin to `FLAMINGO_ALLOWED_ORIGINS` without wildcarding.
The backend applies both CORS and a server-side origin guard, but that is not an
authentication mechanism; public-endpoint rate and concurrency limits still
apply.

If the host uses Content Security Policy, allow `BOT_HOST` in `script-src`,
`connect-src`, and `media-src`. The host may need to add the module script's
origin to `default-src` as well when it does not declare those directives
separately. Keep the policy narrowly host-based; the widget does not require
`unsafe-inline` or `unsafe-eval`.

The widget uses a shadow root, works at phone width, traps focus while open,
closes with Escape, exposes streamed text through a live region, validates
source-link protocols, and renders the static poster when the visitor prefers
reduced motion. The video element is mounted only while the dialog is open.

An answer is rendered as paragraphs, bullet and numbered lists, bold, and
italic. That subset is fixed by the model prompt and by
`frontend/src/lib/richtext.ts`, which must be changed together. Parsing produces
a node tree that becomes real elements; no model or evidence text ever reaches
`innerHTML`. Anything outside the subset, a heading or a Markdown link for
example, stays on screen as the literal characters the model wrote, which is the
signal that the two sides have drifted apart.

## Conversation memory on the host page

The widget keeps the conversation in the page it is embedded in and sends the
recent turns in the `history` field of each `POST /v1/chat` body. Nothing is
written to `localStorage`, `sessionStorage`, or cookies, so the conversation
ends when the visitor reloads, navigates away, or uses the widget's new-chat
control. A host page needs no configuration or consent banner for this, and a
host that navigates between documents rather than routing in place will simply
start a new conversation on each page.

The service applies its own caps regardless of what a client sends. If an
operator needs to turn the feature off for a deployment, set
`FLAMINGO_HISTORY_TURNS=0`; the widget keeps displaying the transcript while the
backend answers each question independently again.

## Name and disclosure

The assistant is called **Diella - Flamingo Style**. The name deliberately
references the Albanian government's own AI program, which makes the disclosure
load-bearing rather than decorative: a visitor must not be able to mistake this
for the state service it comments on.

The visible disclosure must remain: this is an independent Flamingo Revolution
AI project, not a government service or live representative, and the disclosure
also states that the conversation stays in the visitor's browser. Do not shorten
it, move it below the fold, or embed the widget on a page that presents it as
official.

The welcome message is bilingual, Albanian first, which is also the first signal
to a visitor that they may write in their own language. The rest of the widget
chrome is English.

The name is long enough to wrap in the panel header. It is typeset to break
after the hyphen, and the header was verified at 1280, 390, and 320 pixels wide.
Keep the non-breaking space in the title if you edit that markup.
