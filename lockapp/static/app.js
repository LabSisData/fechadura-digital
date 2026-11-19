"use strict";
document.querySelectorAll("form[data-confirm]").forEach(function(form) {
  form.addEventListener("submit", function(event) {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});
document.querySelectorAll("time[data-timestamp]").forEach(function(el) {
  el.textContent = new Date(Number(el.dataset.timestamp) * 1000).toLocaleString("pt-BR");
});
const statusEl = document.getElementById("live-status");
const enrollEl = document.getElementById("enroll-status");
if (statusEl || enrollEl) {
  let busy = false;
  async function refreshStatus() {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const response = await fetch("/api/status", {cache: "no-store"});
      if (!response.ok) throw new Error("Falha de status");
      const state = await response.json();
      if (statusEl) statusEl.textContent = state.status;
      if (enrollEl) enrollEl.textContent = state.enrollment !== null
        ? "Cadastrando: " + state.enrollment + "/5 amostras"
        : state.enrollment_result;
    } catch (error) {
      if (statusEl) statusEl.textContent = "Sem comunicação com o sistema";
    } finally { busy = false; }
  }
  refreshStatus();
  window.setInterval(refreshStatus, 750);
}
