"use strict";
void fetch("/api/account/config", { credentials: "same-origin", cache: "no-store" })
  .then((response) => { if (!response.ok) throw new Error("Unavailable"); return response.json(); })
  .then((config) => {
    const mode = config.practice_mode;
    const status = document.getElementById("privacyReviewStatus");
    if (mode === "guest") {
      status.textContent = "Guest practice is available without an account, and progress is not saved. Parent account features are still under review. This draft notice and the service's speech processing arrangements require review before testing with real children.";
    } else if (mode === "account") {
      status.textContent = "Parent accounts are available. Child profiles and saved practice require verified parental consent and enabled service settings. This draft notice and the service's speech processing arrangements require review before testing with real children.";
      document.querySelectorAll("[data-privacy-return]").forEach((link) => {
        link.href = "/#parentAccount";
        link.textContent = "Back to parent account";
      });
    } else {
      status.textContent = "Practice is currently unavailable. Account features and testing with real children remain subject to privacy review. This notice is a draft.";
    }
    document.getElementById("noticeVersion").textContent = `Notice version: ${config.privacy_notice_version || "pending review"}`;
    if (config.retention_days) document.getElementById("retentionNotice").textContent = `Guest practice results are not saved. Stored account practice records are scheduled for deletion after ${config.retention_days} days; parents can request deletion sooner. Minimal account and consent records follow the separate documented retention policy.`;
    if (config.privacy_contact) {
      const container = document.getElementById("privacyContactDetails");
      container.replaceChildren("Contact the Reading Sound Games operators: ");
      const contacts = config.privacy_contact.split(",").map((email) => email.trim()).filter(Boolean);
      contacts.forEach((contact, index) => {
        if (index) container.append(" and ");
        const link = document.createElement("a");
        link.href = `mailto:${encodeURIComponent(contact)}`;
        link.textContent = contact;
        container.append(link);
      });
      container.append(".");
      if (contacts.length > 1) {
        const sharedLink = document.createElement("a");
        sharedLink.href = `mailto:${contacts.map((contact) => encodeURIComponent(contact)).join(",")}`;
        sharedLink.textContent = "Email both privacy contacts";
        container.append(" ", sharedLink, ".");
      }
    }
  })
  .catch(() => { /* Static notice remains clear that settings could not be verified. */ });
