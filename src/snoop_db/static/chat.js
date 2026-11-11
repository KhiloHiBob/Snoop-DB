const chat = document.querySelector("#chat");
const question = document.querySelector("#question");
const send = document.querySelector("#send");
let history = [];

function text(parent, tag, content) {
  const element = document.createElement(tag);
  element.textContent = content;
  parent.append(element);
  return element;
}

fetch("/health").then(response => response.json()).then(data => {
  document.querySelector("#privacy").textContent =
    `Provider: ${data.provider}. Query results sent to AI: ${data.send_results_to_llm ? "ENABLED" : "disabled"}.`;
}).catch(() => {
  document.querySelector("#privacy").textContent = "Unable to check privacy settings.";
});

document.querySelector("#clear").addEventListener("click", () => {
  history = [];
  chat.replaceChildren();
});

document.querySelector("#form").addEventListener("submit", async event => {
  event.preventDefault();
  const prompt = question.value.trim();
  if (!prompt) return;
  send.disabled = true;
  const entry = text(chat, "article", "");
  text(entry, "h2", prompt);
  const status = text(entry, "p", "Inspecting schema and checking SQL…");
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: prompt, history})
    });
    const data = await response.json();
    status.textContent = data.message || data.error || "Request could not be completed.";
    if (!response.ok) return;
    history = [...history, prompt].slice(-6);
    if (data.result) {
      text(entry, "h3", data.result.explain ? "Estimated execution plan SQL" : "Validated SQL");
      text(entry, "pre", data.result.sql);
      text(entry, "pre", `Bound parameters: ${JSON.stringify(data.result.parameters)}`);
      const wrapper = text(entry, "div", "");
      wrapper.className = "results";
      const table = text(wrapper, "table", "");
      const header = text(text(table, "thead", ""), "tr", "");
      data.result.columns.forEach(column => text(header, "th", column));
      const body = text(table, "tbody", "");
      data.result.rows.forEach(row => {
        const tr = text(body, "tr", "");
        row.forEach(cell => text(tr, "td", typeof cell === "object" ?
          JSON.stringify(cell, null, 2) : String(cell)));
      });
      if (data.result.truncated) text(entry, "p", "Result limit reached; more rows exist.");
    }
    if (data.schema) {
      const details = text(entry, "details", "");
      text(details, "summary", "Schema inspected");
      text(details, "pre", JSON.stringify(data.schema, null, 2));
    }
    question.value = "";
  } catch {
    status.textContent = "Request failed. Check the local server.";
  } finally {
    send.disabled = false;
  }
});
