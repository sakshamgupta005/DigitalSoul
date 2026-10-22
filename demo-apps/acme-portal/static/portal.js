"use strict";

// Posts the weekly summary to POST /api/team/notes (the request the workflow's network_2xx check expects).
document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("team-note-form");
  if (!form) return;
  const textarea = document.querySelector('[data-testid="team-note-body"]');
  const status = document.querySelector('[data-testid="post-status"]');
  const list = document.querySelector('[data-testid="team-note-list"]');

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.textContent = "Posting...";
    try {
      const response = await fetch("/api/team/notes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: textarea.value }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error ? data.error.message : response.statusText);
      const item = document.createElement("li");
      item.dataset.testid = "team-note";
      item.dataset.noteId = String(data.note.note_id);
      const body = document.createElement("p");
      body.className = "note-body";
      body.textContent = data.note.body;
      const meta = document.createElement("p");
      meta.className = "muted";
      meta.textContent = `${data.note.author}, ${data.note.created_at}`;
      item.append(body, meta);
      list.prepend(item);
      textarea.value = "";
      status.textContent = "Posted";
    } catch (error) {
      status.textContent = `Post failed: ${error.message}`;
    }
  });
});
