<svelte:options
  customElement={{
    tag: "flamingo-chat",
    shadow: "open",
    props: {
      apiBase: { attribute: "api-base", reflect: false, type: "String" },
      assetBase: { attribute: "asset-base", reflect: false, type: "String" },
      buttonLabel: { attribute: "button-label", reflect: false, type: "String" },
    },
  }}
/>

<script lang="ts">
  import { afterUpdate, onDestroy, onMount, tick } from "svelte";

  import { buildHistory } from "./lib/history";
  import { refusalBubble, refusalMessage, refusalReason } from "./lib/refusal";
  import { parseRichText, type InlineNode } from "./lib/richtext";
  import { consumeEventStream, type ServerEvent } from "./lib/sse";
  import { selectSuggestions, type SelectedSuggestion } from "./lib/suggestions";

  type Citation = {
    id: string;
    title: string;
    url: string;
    source: string;
  };

  type Message = {
    id: string;
    role: "assistant" | "user";
    text: string;
    citations: Citation[];
    pending?: boolean;
    failed?: boolean;
    excluded?: boolean;
  };

  const moduleDirectory = import.meta.url.slice(0, import.meta.url.lastIndexOf("/") + 1);
  const defaultAssetBase = `${moduleDirectory}media/`;
  const moduleUrl = new URL(import.meta.url);
  const defaultApiBase = `${moduleUrl.protocol}//${moduleUrl.host}`;

  export let apiBase = defaultApiBase;
  export let assetBase = defaultAssetBase;
  export let buttonLabel = "Ask Flamingo";

  let open = false;
  let question = "";
  let errorMessage = "";
  let submitting = false;
  let prefersReducedMotion = false;
  let inputElement: HTMLTextAreaElement;
  let dialogElement: HTMLElement;
  let launcherElement: HTMLButtonElement;
  let messagesElement: HTMLElement;
  let followLatestMessage = true;
  let previousFocus: HTMLElement | null = null;
  let abortController: AbortController | null = null;
  let motionQuery: MediaQueryList | null = null;
  let messageSerial = 0;
  let suggestions: SelectedSuggestion[] = [];

  // Albanian first, because it is the first signal that a visitor may write in
  // their own language. The blank line renders the two as separate paragraphs.
  const welcomeMessage = (): Message => ({
    id: "welcome",
    role: "assistant",
    text:
      "Më pyet për Revolucionin Flamingo, pse po protestojnë shqiptarët " +
      "dhe për skandalet e kësaj qeverie.\n\n" +
      "Ask me about the Flamingo Revolution, why Albanians are protesting, " +
      "and the scandals of this government.",
    citations: [],
    excluded: true,
  });

  let messages: Message[] = [welcomeMessage()];

  const normalizedApiBase = () => apiBase.replace(/\/+$/, "");
  const mediaUrl = (name: string) => `${assetBase.replace(/\/+$/, "")}/${name}`;

  function safeSourceUrl(value: string): string | null {
    try {
      const url = new URL(value);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch {
      return null;
    }
  }

  function updateMessage(id: string, update: Partial<Message>) {
    messages = messages.map((message) =>
      message.id === id ? { ...message, ...update } : message,
    );
  }

  function appendDelta(id: string, text: string) {
    const target = messages.find((message) => message.id === id);
    if (target) updateMessage(id, { text: target.text + text });
  }

  function appendCitation(id: string, citation: Citation) {
    const target = messages.find((message) => message.id === id);
    if (target && !target.citations.some((item) => item.id === citation.id)) {
      updateMessage(id, { citations: [...target.citations, citation] });
    }
  }

  function handleMessagesScroll() {
    if (!messagesElement) return;
    followLatestMessage =
      messagesElement.scrollHeight - messagesElement.scrollTop - messagesElement.clientHeight < 48;
  }

  function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null;
  }

  function handleServerEvent(messageId: string, event: ServerEvent) {
    if (!isRecord(event.data)) return;
    if (event.event === "delta" && typeof event.data.text === "string") {
      appendDelta(messageId, event.data.text);
    } else if (
      event.event === "citation" &&
      typeof event.data.id === "string" &&
      typeof event.data.title === "string" &&
      typeof event.data.url === "string" &&
      typeof event.data.source === "string"
    ) {
      appendCitation(messageId, event.data as Citation);
    } else if (event.event === "error") {
      throw new Error(
        typeof event.data.code === "string" ? event.data.code : "stream_failed",
      );
    }
  }

  async function submitQuestion(rawQuestion: string) {
    const trimmed = rawQuestion.trim();
    if (submitting || trimmed.length < 2) return;

    errorMessage = "";
    submitting = true;
    followLatestMessage = true;
    question = "";
    // Captured before the new turn is appended: the service keeps no session,
    // so these replayed turns are the only way a follow-up is understood.
    const history = buildHistory(messages);
    const userId = `user-${++messageSerial}`;
    const answerId = `answer-${++messageSerial}`;
    messages = [
      ...messages,
      { id: userId, role: "user", text: trimmed, citations: [] },
      { id: answerId, role: "assistant", text: "", citations: [], pending: true },
    ];
    abortController = new AbortController();

    try {
      const response = await fetch(`${normalizedApiBase()}/v1/chat`, {
        method: "POST",
        headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
        body: JSON.stringify({ question: trimmed, history }),
        signal: abortController.signal,
      });
      if (!response.ok || !response.body) {
        throw new Error(await refusalReason(response));
      }
      await consumeEventStream(response.body, (event) => handleServerEvent(answerId, event));
      const answer = messages.find((message) => message.id === answerId);
      if (!answer?.text) throw new Error("empty_response");
      updateMessage(answerId, { pending: false });
    } catch (error) {
      // A cancelled or failed answer stays visible but never returns as history,
      // because replaying it would present an incomplete exchange as a real one.
      if (error instanceof DOMException && error.name === "AbortError") {
        const streamed = messages.find((message) => message.id === answerId)?.text;
        if (streamed) updateMessage(answerId, { pending: false, failed: true });
        else messages = messages.filter((message) => message.id !== answerId);
        return;
      }
      updateMessage(userId, { failed: true });
      const reason = error instanceof Error ? error.message : "request_failed";
      updateMessage(answerId, {
        pending: false,
        failed: true,
        text: refusalBubble(reason),
      });
      errorMessage = refusalMessage(reason);
    } finally {
      submitting = false;
      abortController = null;
      await tick();
      inputElement?.focus();
    }
  }

  function submit() {
    void submitQuestion(question);
  }

  async function startNewConversation() {
    abortController?.abort();
    messages = [welcomeMessage()];
    suggestions = selectSuggestions(document.documentElement.lang);
    messageSerial = 0;
    errorMessage = "";
    followLatestMessage = true;
    await tick();
    inputElement?.focus();
  }

  async function showDialog() {
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (messages.length === 1) suggestions = selectSuggestions(document.documentElement.lang);
    open = true;
    await tick();
    inputElement?.focus();
  }

  async function closeDialog() {
    abortController?.abort();
    open = false;
    errorMessage = "";
    await tick();
    if (launcherElement) launcherElement.focus();
    else previousFocus?.focus();
  }

  function focusableElements(): HTMLElement[] {
    if (!dialogElement) return [];
    return Array.from(
      dialogElement.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    );
  }

  function handleDialogKeydown(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeDialog();
      return;
    }
    if (event.key !== "Tab") return;
    const elements = focusableElements();
    if (elements.length === 0) return;
    const first = elements[0];
    const last = elements[elements.length - 1];
    const root = dialogElement.getRootNode();
    const activeElement = root instanceof ShadowRoot ? root.activeElement : document.activeElement;
    if (event.shiftKey && activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function handleInputKeydown(event: KeyboardEvent) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  }

  function updateMotionPreference(event: MediaQueryListEvent | MediaQueryList) {
    prefersReducedMotion = event.matches;
  }

  onMount(() => {
    motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    updateMotionPreference(motionQuery);
    motionQuery.addEventListener("change", updateMotionPreference);
  });

  afterUpdate(() => {
    if (followLatestMessage && messagesElement) {
      messagesElement.scrollTop = messagesElement.scrollHeight;
    }
  });

  onDestroy(() => {
    abortController?.abort();
    motionQuery?.removeEventListener("change", updateMotionPreference);
  });
</script>

<!--
  The answer arrives as light Markdown. It is parsed into nodes and rendered
  with real elements, never through `{@html}`, so model or evidence text can
  never become markup on the host page.
-->
{#snippet inline(nodes: InlineNode[])}{#each nodes as node}{#if node.kind === "strong"}<strong
        >{node.text}</strong
      >{:else if node.kind === "em"}<em>{node.text}</em>{:else}{node.text}{/if}{/each}{/snippet}

{#snippet listItems(entries: InlineNode[][])}{#each entries as entry}<li>{@render inline(entry)}</li
    >{/each}{/snippet}

{#snippet answerBody(message: Message)}
  {#each parseRichText(message.text, message.pending === true) as block}
    {#if block.kind === "list"}
      {#if block.ordered}
        <ol class="rich-list">{@render listItems(block.items)}</ol>
      {:else}
        <ul class="rich-list">{@render listItems(block.items)}</ul>
      {/if}
    {:else}
      <p>{@render inline(block.inlines)}</p>
    {/if}
  {/each}
{/snippet}

{#if open}
  <dialog
    open
    bind:this={dialogElement}
    class="dialog"
    aria-modal="true"
    aria-labelledby="flamingo-chat-title"
    on:keydown={handleDialogKeydown}
  >
    <header class="header">
      <div class="identity">
        <div class="avatar" aria-hidden="true">
          {#if prefersReducedMotion}
            <img src={mediaUrl("flamingo-avatar-poster.webp")} alt="" />
          {:else}
            <video autoplay loop muted playsinline poster={mediaUrl("flamingo-avatar-poster.webp")}>
              <source src={mediaUrl("flamingo-avatar-loop.webm")} type="video/webm" />
              <source src={mediaUrl("flamingo-avatar-loop.mp4")} type="video/mp4" />
            </video>
          {/if}
        </div>
        <div>
          <!-- The name is too long for the panel header, so it wraps. The
               non-breaking space keeps the break after the hyphen. -->
          <h2 id="flamingo-chat-title">Diella - Flamingo&nbsp;Style</h2>
          <p>Source-grounded AI</p>
        </div>
      </div>
      <div class="actions">
        <button
          class="reset"
          type="button"
          aria-label="Start a new conversation"
          title="Forget this conversation and start over"
          disabled={messages.length < 2}
          on:click={startNewConversation}
        >
          <span class="reset-icon" aria-hidden="true">↺</span>
          <span class="reset-label">New chat</span>
        </button>
        <button class="close" type="button" aria-label="Close assistant" on:click={closeDialog}>×</button>
      </div>
    </header>

    <p class="disclosure">
      Diella 2.0 është parodi e Diellës së qeverisë shqiptare dhe projekt i pavarur i Revolucionit
      Flamingo; nuk është shërbim shtetëror. Mesazhet dërgohen për përpunim, por historiku i bisedës
      mbahet vetëm në browserin tënd dhe nuk ruhet në serverin tonë.
    </p>

    <div
      bind:this={messagesElement}
      class="messages"
      role="log"
      aria-live="polite"
      aria-relevant="additions text"
      on:scroll={handleMessagesScroll}
    >
      {#each messages as message (message.id)}
        <article class:assistant={message.role === "assistant"} class:user={message.role === "user"}>
          {#if message.role === "user"}
            <p>{message.text}</p>
          {:else if message.pending && !message.text}
            <p>Thinking…</p>
          {:else}
            {@render answerBody(message)}
          {/if}
          {#if message.citations.length > 0}
            <ol class="citations" aria-label="Sources">
              {#each message.citations as citation}
                {@const href = safeSourceUrl(citation.url)}
                <li>
                  {#if href}
                    <a href={href} target="_blank" rel="noopener noreferrer">
                      <span>{citation.id}</span> {citation.title}
                    </a>
                  {:else}
                    <span><strong>{citation.id}</strong> {citation.title}</span>
                  {/if}
                  <small>{citation.source}</small>
                </li>
              {/each}
            </ol>
          {/if}
        </article>
      {/each}
    </div>

    <form on:submit|preventDefault={submit}>
      <label for="flamingo-question">Your question</label>
      {#if messages.length === 1 && suggestions.length === 2}
        <div
          class="suggestions"
          role="group"
          aria-label={document.documentElement.lang.toLowerCase().startsWith("en")
            ? "Suggested questions"
            : "Pyetje të sugjeruara"}
        >
          {#each suggestions as suggestion (suggestion.id)}
            <button
              type="button"
              disabled={submitting}
              aria-label={suggestion.question}
              on:click={() => void submitQuestion(suggestion.question)}
            >{suggestion.label}</button>
          {/each}
        </div>
      {/if}
      <div class="composer">
        <textarea
          id="flamingo-question"
          bind:this={inputElement}
          bind:value={question}
          maxlength="1000"
          rows="2"
          placeholder="Ask about the revolution…"
          disabled={submitting}
          on:keydown={handleInputKeydown}
        ></textarea>
        <button type="submit" disabled={submitting || question.trim().length < 2} aria-label="Send question">
          <span aria-hidden="true">→</span>
        </button>
      </div>
      {#if errorMessage}<p class="error" role="alert">{errorMessage}</p>{/if}
    </form>
  </dialog>
{:else}
  <button bind:this={launcherElement} class="launcher" type="button" on:click={showDialog} aria-label={buttonLabel}>
    <span class="mark" aria-hidden="true">F</span>
    <span>{buttonLabel}</span>
  </button>
{/if}

<style>
  :host {
    --flamingo-accent: #d92366;
    --flamingo-ink: #211e1c;
    --flamingo-muted: #6f6762;
    --flamingo-paper: #fffdfb;
    position: fixed;
    z-index: 2147483000;
    right: max(18px, env(safe-area-inset-right));
    bottom: max(18px, env(safe-area-inset-bottom));
    color: var(--flamingo-ink);
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 16px;
    line-height: 1.45;
  }

  * {
    box-sizing: border-box;
  }

  button,
  textarea {
    font: inherit;
  }

  button {
    cursor: pointer;
  }

  button:focus-visible,
  textarea:focus-visible,
  a:focus-visible {
    outline: 3px solid #ff9fc1;
    outline-offset: 2px;
  }

  .launcher {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 54px;
    padding: 7px 18px 7px 8px;
    border: 0;
    border-radius: 999px;
    background: var(--flamingo-ink);
    box-shadow: 0 12px 34px rgb(33 30 28 / 24%);
    color: white;
    font-weight: 750;
  }

  .mark {
    display: grid;
    width: 40px;
    height: 40px;
    place-items: center;
    border-radius: 50%;
    background: var(--flamingo-accent);
    font-family: Georgia, serif;
    font-size: 23px;
    font-style: italic;
  }

  .dialog {
    position: relative;
    display: grid;
    grid-template-rows: auto auto minmax(180px, 1fr) auto;
    width: min(430px, calc(100vw - 28px));
    height: min(700px, calc(100dvh - 36px));
    overflow: hidden;
    margin: 0;
    padding: 0;
    border: 1px solid rgb(33 30 28 / 10%);
    border-radius: 24px;
    background: var(--flamingo-paper);
    box-shadow: 0 24px 80px rgb(33 30 28 / 28%);
  }

  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 17px 12px;
    border-bottom: 1px solid #eee7e2;
  }

  .identity {
    display: flex;
    min-width: 0;
    align-items: center;
    gap: 12px;
  }

  .identity h2,
  .identity p {
    margin: 0;
  }

  .identity h2 {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 20px;
    font-weight: 600;
    letter-spacing: -0.01em;
    line-height: 1.15;
    overflow-wrap: break-word;
  }

  .identity p {
    color: var(--flamingo-muted);
    font-size: 12px;
  }

  .avatar {
    width: 52px;
    height: 52px;
    flex: 0 0 auto;
    overflow: hidden;
    border: 2px solid var(--flamingo-accent);
    border-radius: 50%;
    background: #eee7e2;
  }

  .avatar img,
  .avatar video {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .actions {
    display: flex;
    flex: 0 0 auto;
    align-items: center;
    gap: 4px;
  }

  .reset {
    display: flex;
    align-items: center;
    padding: 7px 11px;
    border: 1px solid #e2d9d4;
    border-radius: 999px;
    background: transparent;
    color: var(--flamingo-muted);
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
  }

  .reset-icon {
    display: none;
    font-size: 19px;
    line-height: 1;
  }

  /* Narrow screens keep the header on one line by dropping the text label. */
  @media (max-width: 430px) {
    .identity h2 {
      font-size: 18px;
    }

    .reset {
      width: 40px;
      height: 40px;
      justify-content: center;
      padding: 0;
      border-radius: 50%;
    }

    .reset-icon {
      display: block;
    }

    .reset-label {
      display: none;
    }
  }

  .reset:hover:not(:disabled) {
    border-color: var(--flamingo-accent);
    color: var(--flamingo-accent);
  }

  .reset:disabled {
    cursor: default;
    opacity: 0.45;
  }

  .close {
    display: grid;
    width: 42px;
    height: 42px;
    place-items: center;
    border: 0;
    border-radius: 50%;
    background: transparent;
    color: var(--flamingo-ink);
    font-size: 28px;
  }

  .close:hover {
    background: #f1ebe7;
  }

  .disclosure {
    margin: 0;
    padding: 10px 17px;
    border-bottom: 1px solid #eee7e2;
    background: #fff6f9;
    color: #5e4e54;
    font-size: 11px;
  }

  .messages {
    display: flex;
    min-height: 0;
    flex-direction: column;
    gap: 12px;
    overflow-y: auto;
    padding: 18px 16px;
    overscroll-behavior: contain;
  }

  article {
    max-width: 88%;
    padding: 11px 13px;
    border-radius: 16px;
  }

  article p {
    margin: 0;
    white-space: pre-wrap;
  }

  .rich-list {
    margin: 0;
    padding-left: 21px;
  }

  article p:not(:first-child),
  .rich-list:not(:first-child) {
    margin-top: 8px;
  }

  .rich-list li + li {
    margin-top: 5px;
  }

  article strong {
    font-weight: 700;
  }

  article em {
    font-style: italic;
  }

  article.assistant {
    align-self: flex-start;
    border-bottom-left-radius: 4px;
    background: #f1ebe7;
  }

  article.user {
    align-self: flex-end;
    border-bottom-right-radius: 4px;
    background: var(--flamingo-ink);
    color: white;
  }

  .citations {
    display: grid;
    gap: 7px;
    margin: 12px 0 0;
    padding: 10px 0 0;
    border-top: 1px solid rgb(33 30 28 / 12%);
    list-style: none;
  }

  .citations li {
    display: grid;
    gap: 1px;
  }

  .citations a,
  .citations li > span {
    color: var(--flamingo-ink);
    font-size: 12px;
    font-weight: 700;
    text-decoration-thickness: 1px;
    text-underline-offset: 2px;
  }

  .citations a span,
  .citations strong {
    color: var(--flamingo-accent);
  }

  .citations small {
    color: var(--flamingo-muted);
    font-size: 10px;
  }

  form {
    padding: 12px;
    border-top: 1px solid #eee7e2;
    background: white;
  }

  form > label {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
  }

  .suggestions {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 7px;
    margin-bottom: 9px;
  }

  .suggestions button {
    min-width: 0;
    min-height: 44px;
    padding: 7px 9px;
    border: 1px solid #eab7ca;
    border-radius: 12px;
    background: #fff6f9;
    color: #8f1745;
    font-size: 12px;
    font-weight: 600;
    line-height: 1.2;
    overflow-wrap: anywhere;
    text-align: left;
  }

  .suggestions button:hover {
    background: #ffeaf1;
  }

  .suggestions button:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }

  .composer {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 8px;
    align-items: end;
    padding: 5px 5px 5px 12px;
    border: 1px solid #d8cfca;
    border-radius: 18px;
    background: #fffdfb;
  }

  textarea {
    width: 100%;
    max-height: 120px;
    resize: none;
    border: 0;
    background: transparent;
    color: var(--flamingo-ink);
    outline: 0;
  }

  textarea::placeholder {
    color: #8a817b;
  }

  .composer button {
    display: grid;
    width: 44px;
    height: 44px;
    place-items: center;
    border: 0;
    border-radius: 14px;
    background: var(--flamingo-accent);
    color: white;
    font-size: 25px;
  }

  .composer button:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }

  .error {
    margin: 8px 3px 0;
    color: #a00f3e;
    font-size: 12px;
    /* The alert carries Albanian and English on their own lines. */
    white-space: pre-line;
  }

  @media (max-width: 520px) {
    :host {
      right: max(8px, env(safe-area-inset-right));
      bottom: max(8px, env(safe-area-inset-bottom));
    }

    .dialog {
      width: calc(100vw - 16px);
      height: calc(100dvh - 16px);
      border-radius: 18px;
    }

    .launcher span:last-child {
      display: none;
    }

    .launcher {
      padding-right: 8px;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
      scroll-behavior: auto !important;
      transition-duration: 0.01ms !important;
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
    }
  }
</style>
