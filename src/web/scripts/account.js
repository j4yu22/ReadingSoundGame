/* Parent credentials and child information live only in memory in this page.
 * Server-side authorization is required for every private API operation. */
(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  let config = null;
  let parent = null;
  let selectedChildId = "";
  let blocked = false;
  let changing = false;
  let deletion = null;
  let progressRequest = 0;

  const verified = () => parent?.consent_status === "verified" && !parent.deletion_pending;
  const guest = () => config?.enabled === false && config?.practice_mode === "guest";
  const ready = () => !blocked && !changing && (guest() || Boolean(config?.practice_mode === "account" && config.enabled && config.child_collection_enabled && verified() && selectedChildId));
  function changed() {
    window.dispatchEvent(new CustomEvent("parent-account-change"));
  }
  function message(text, error = false) {
    const node = byId(error ? "accountError" : "accountStatus");
    node.textContent = text;
    node.hidden = !text;
  }
  function invalidate() {
    blocked = true;
    selectedChildId = "";
    progressRequest += 1;
    render();
    changed();
  }
  async function request(path, options = {}) {
    if (new URL(path, window.location.origin).origin !== window.location.origin) throw new Error("Unsupported account request.");
    const method = options.method || "GET";
    const headers = new Headers(options.headers);
    if (!["GET", "HEAD"].includes(method)) {
      if (!parent?.csrf_token) throw new Error("Sign in again before making changes.");
      headers.set("X-CSRF-Token", parent.csrf_token);
    }
    const response = await fetch(path, { ...options, method, headers, credentials: "same-origin", cache: "no-store" });
    if (!response.ok) {
      if ([401, 403, 423].includes(response.status)) invalidate();
      const payload = await response.json().catch(() => ({}));
      throw new Error(typeof payload.detail === "string" ? payload.detail : "The request could not be completed. Please try again.");
    }
    return response;
  }
  async function json(path, method = "GET", data) {
    const options = { method };
    if (data !== undefined) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(data);
    }
    const response = await request(path, options);
    return response.status === 204 ? null : response.json();
  }
  function render() {
    const accountsAvailable = Boolean(config?.enabled);
    document.body.classList.toggle("accounts-available", accountsAvailable);
    byId("parentAccount").hidden = !accountsAvailable;
    byId("accountAccess").hidden = accountsAvailable;
    byId("parentAccountLink").hidden = !accountsAvailable;
    byId("signedOutPanel").hidden = Boolean(parent);
    byId("signedInPanel").hidden = !parent;
    byId("parentLogin").hidden = !config?.enabled;
    byId("parentLogin").textContent = config?.registration_open ? "Parent sign in / sign up" : "Existing parent sign in";
    byId("parentEmail").textContent = parent?.email || "Parent account";
    byId("parentReauthenticate").hidden = !blocked;
    byId("goToPractice").hidden = !ready();
    byId("privacyContact").replaceChildren();
    if (config?.privacy_contact) {
      const link = document.createElement("a");
      link.href = `mailto:${config.privacy_contact.split(",").map((email) => encodeURIComponent(email.trim())).join(",")}`;
      link.textContent = "Privacy questions or deletion help";
      byId("privacyContact").append(" · ", link);
    }
    byId("consentRetention").textContent = config?.retention_days ? `Practice records are scheduled for deletion after ${config.retention_days} days. You can request deletion sooner.` : "Child collection remains closed until retention and privacy settings are ready.";
    byId("consentPanel").hidden = !parent || verified() || parent.deletion_pending;
    byId("consentForm").hidden = parent?.consent_status === "pending" || !config?.child_collection_enabled;
    const canCreateChild = Boolean(verified() && config?.child_collection_enabled && !blocked);
    byId("childPanel").hidden = !parent || blocked || (!parent.children?.length && !canCreateChild);
    byId("childForm").hidden = !canCreateChild;
    byId("progressPanel").hidden = !selectedChildId || !parent || blocked;
    const selector = byId("childSelect");
    selector.replaceChildren(new Option("Choose a child", ""));
    for (const child of parent?.children || []) selector.add(new Option(child.nickname, child.id));
    selector.value = selectedChildId;
    let status;
    let badge;
    if (guest()) {
      badge = "Practice without an account";
      status = "Practice is available. Accounts and saved progress are not available yet.";
    } else if (!config?.enabled) {
      badge = "Testing closed";
      status = "Parent account setup and privacy review are not complete. Child profiles, practice, and the microphone are unavailable.";
    } else if (parent?.deletion_pending) {
      badge = "Deletion requested";
      status = "Your deletion request is pending. Practice and collection have stopped. Retained backups expire under the retention policy.";
    } else if (blocked) {
      badge = "Access paused";
      status = "Your access needs to be checked again. Refresh this page or sign in again. Practice and the microphone are locked.";
    } else if (!parent) {
      badge = config.registration_open ? "Parent sign-in" : "Registration closed";
      status = config.registration_open ? "Parents and guardians: sign in to manage consent, child profiles, and progress." : "Registration is closed while account setup and privacy review are completed.";
    } else if (!config.child_collection_enabled) {
      badge = "Child collection closed";
      status = "You are signed in. Child profiles, practice, and microphone access remain closed while privacy setup and review are completed.";
    } else if (parent.consent_status === "pending") {
      badge = "Verification pending";
      status = "Your parental verification request is pending. We must complete the separate verification process before you can create a child’s profile or start practice.";
    } else if (!verified()) {
      badge = "Consent required";
      status = "Read the notice and request parental verification. No child profile or microphone collection is enabled yet.";
    } else {
      badge = selectedChildId ? "Ready to practice" : "Choose a child";
      status = selectedChildId ? "Parental consent is verified. The microphone opens only during speaking turns. Practice results will be saved for the selected child." : "Parental consent is verified. Add a nickname or select a child to begin.";
    }
    byId("accountBadge").textContent = badge;
    message(status);
  }
  async function refresh() {
    const previous = selectedChildId;
    const configuration = await fetch("/api/account/config", { credentials: "same-origin", cache: "no-store" });
    if (!configuration.ok) throw new Error("Practice availability could not be checked. Refresh to try again.");
    config = await configuration.json();
    if (config.enabled) {
      const response = await fetch("/api/account/me", { credentials: "same-origin", cache: "no-store" });
      if (response.status === 401) parent = null;
      else if (!response.ok) throw new Error("Your account could not be checked. Practice remains locked.");
      else parent = await response.json();
    } else parent = null;
    selectedChildId = (parent?.children || []).some((child) => child.id === previous) ? previous : "";
    blocked = false;
    render();
    changed();
  }
  function clearProgress() {
    progressRequest += 1;
    byId("progressRows").replaceChildren();
    byId("progressSummary").replaceChildren();
    byId("progressTableWrap").hidden = true;
    byId("progressStatus").textContent = "";
  }
  async function loadProgress() {
    clearProgress();
    if (!selectedChildId || !parent || blocked) return;
    const childId = selectedChildId;
    const requestNumber = progressRequest;
    byId("progressStatus").textContent = "Loading practice history…";
    try {
      const data = await json(`/api/account/children/${encodeURIComponent(childId)}/progress`);
      if (requestNumber !== progressRequest || childId !== selectedChildId) return;
      const attempts = Array.isArray(data.attempts) ? data.attempts : [];
      const summary = data.summary || {};
      for (const [label, value] of [["Attempts", summary.attempts ?? attempts.length], ["Completed", summary.completed], ["Correct final answers", summary.correct]]) {
        if (value === undefined) continue;
        const stat = document.createElement("div");
        const count = document.createElement("strong");
        count.textContent = String(value);
        const name = document.createElement("span");
        name.textContent = label;
        stat.append(count, name);
        byId("progressSummary").append(stat);
      }
      byId("progressStatus").textContent = attempts.length ? "Recent attempts. Microphone problems and unfinished activities are separate from incorrect answers." : "No saved attempts yet. Progress appears here after practice.";
      for (const attempt of attempts.slice(0, 20)) {
        const tr = document.createElement("tr");
        const selection = attempt.selection || {};
        const date = new Date(attempt.created_at || attempt.started_at);
        const outcome = attempt.outcome === "technical_error" ? "Speech check unavailable" : attempt.outcome === "microphone_unavailable" ? "Microphone unavailable" : attempt.outcome === "abandoned" ? "Not finished" : attempt.outcome === "no_speech" ? "No speech detected" : attempt.correct === true ? "Correct final answer" : attempt.correct === false ? "Keep practicing" : "In progress";
        for (const text of [Number.isNaN(date.valueOf()) ? "—" : date.toLocaleDateString(), `Level ${selection.level || "—"} · Exercise ${selection.exercise || "—"}`, outcome]) {
          const td = document.createElement("td");
          td.textContent = text;
          tr.append(td);
        }
        byId("progressRows").append(tr);
      }
      byId("progressTableWrap").hidden = !attempts.length;
    } catch (error) {
      if (requestNumber === progressRequest) byId("progressStatus").textContent = error.message;
    }
  }
  async function action(button, operation) {
    if (changing) return;
    changing = true;
    button.disabled = true;
    message("", true);
    changed();
    try { await operation(); }
    catch (error) { message(error.message, true); }
    finally { changing = false; button.disabled = false; render(); changed(); }
  }
  function confirmDeletion(kind) {
    deletion = { kind, childId: selectedChildId };
    const child = parent?.children?.find((item) => item.id === selectedChildId);
    byId("deleteTitle").textContent = kind === "child" ? "Delete this child’s data?" : kind === "withdraw" ? "Withdraw parental consent?" : "Delete your parent account?";
    byId("deleteDescription").textContent = kind === "child" ? `This deletes ${child?.nickname || "this child"}’s profile and practice history. Other child profiles remain.` : kind === "withdraw" ? "This stops practice for all your children and requests deletion of all child profiles and practice history." : "This requests deletion of your parent account, all child profiles, and all practice history.";
    byId("deleteConfirmation").value = "";
    byId("deleteError").textContent = "";
    byId("deleteDialog").showModal();
  }
  byId("childSelect").addEventListener("change", () => {
    selectedChildId = byId("childSelect").value;
    clearProgress();
    render();
    changed();
    void loadProgress();
  });
  byId("consentForm").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!byId("adultConfirmed").checked) return;
    void action(event.submitter, async () => {
      await json("/api/account/consent", "POST", { notice_version: config.privacy_notice_version, adult_confirmed: true });
      byId("adultConfirmed").checked = false;
      await refresh();
    });
  });
  byId("childForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const nickname = byId("childNickname").value.trim();
    if (!nickname) {
      byId("childNickname").setCustomValidity("Enter a nickname with at least one visible character.");
      byId("childNickname").reportValidity();
      return;
    }
    void action(event.submitter, async () => {
      const child = await json("/api/account/children", "POST", { nickname });
      byId("childNickname").value = "";
      await refresh();
      selectedChildId = child.id;
      render();
      await loadProgress();
    });
  });
  byId("childNickname").addEventListener("input", () => byId("childNickname").setCustomValidity(""));
  byId("refreshProgress").addEventListener("click", () => void loadProgress());
  byId("logoutButton").addEventListener("click", (event) => void action(event.currentTarget, async () => {
    // Stop all practice immediately, including while the logout request is in flight.
    selectedChildId = "";
    clearProgress();
    changed();
    const result = await json("/api/auth/logout", "POST");
    parent = null;
    await refresh();
    if (result?.logout_url) window.location.assign(result.logout_url);
  }));
  byId("exportButton").addEventListener("click", (event) => void action(event.currentTarget, async () => {
    const response = await request("/api/account/export");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "reading-sound-games-family-data.json";
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }));
  byId("deleteChildButton").addEventListener("click", () => confirmDeletion("child"));
  byId("withdrawButton").addEventListener("click", () => confirmDeletion("withdraw"));
  byId("deleteAccountButton").addEventListener("click", () => confirmDeletion("account"));
  byId("cancelDelete").addEventListener("click", () => byId("deleteDialog").close());
  byId("deleteForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (byId("deleteConfirmation").value !== "DELETE" || !deletion || changing) return;
    const task = deletion;
    const button = byId("confirmDelete");
    changing = true;
    button.disabled = true;
    selectedChildId = "";
    clearProgress();
    changed();
    try {
      const path = task.kind === "child" ? `/api/account/children/${encodeURIComponent(task.childId)}` : task.kind === "withdraw" ? "/api/account/consent/withdraw" : "/api/account";
      await json(path, task.kind === "withdraw" ? "POST" : "DELETE");
      byId("deleteDialog").close();
      deletion = null;
      await refresh();
      message("Deletion requested. Practice has stopped for the affected profiles. The server tracks the deletion job; backups expire separately.");
    } catch (error) { byId("deleteError").textContent = error.message; }
    finally { changing = false; button.disabled = false; changed(); }
  });
  window.ParentAccount = Object.freeze({
    get ready() { return ready(); },
    get guest() { return guest(); },
    get childId() { return selectedChildId; },
    get csrfToken() { return parent?.csrf_token || ""; },
    get gateMessage() {
      if (ready()) return "Press Start to hear a word.";
      if (!config?.enabled) return "Practice is temporarily unavailable. Refresh to try again.";
      return "Practice is locked. A parent must sign in, complete verification, and select a child.";
    },
    request, json, invalidate,
    refreshProgress: loadProgress
  });
  void refresh().catch((error) => { invalidate(); message(error.message, true); });
})();
