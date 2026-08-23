await customElements.whenDefined("flamingo-chat");

const widget = document.querySelector("flamingo-chat");
requestAnimationFrame(() => {
  widget?.shadowRoot?.querySelector("button")?.click();
});
