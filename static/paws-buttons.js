// paws-buttons.js
(() => {
  // COPY URL BUTTON
  function setupCopyButton() {
    const copyBtn = document.getElementById("copyUrlBtn");
    if (!copyBtn) return;

    copyBtn.addEventListener("click", async () => {
      const url = "https://pawslive.onrender.com";
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(url);
        } else {
          // fallback
          const ta = document.createElement("textarea");
          ta.value = url;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          ta.remove();
        }
        copyBtn.textContent = "Copied!";
        setTimeout(() => (copyBtn.textContent = "Copy URL"), 1200);
      } catch {
        copyBtn.textContent = "Failed";
        setTimeout(() => (copyBtn.textContent = "Copy URL"), 1200);
      }
    });
  }

  // INSTALL APP BUTTON
  let deferredPrompt = null;

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e;
  });

  function setupInstallButton() {
    const installBtn = document.getElementById("installBtn");
    if (!installBtn) return;

    installBtn.addEventListener("click", async () => {
      if (!deferredPrompt) {
        alert("Install not available right now — try Chrome/Android or Safari on iOS.");
        return;
      }
      deferredPrompt.prompt();
      await deferredPrompt.userChoice;
      deferredPrompt = null;
    });
  }

  // INIT once DOM is ready
  document.addEventListener("DOMContentLoaded", () => {
    setupCopyButton();
    setupInstallButton();
  });
})();