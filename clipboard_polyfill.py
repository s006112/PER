
CLIPBOARD_POLYFILL = """
<script>
(function () {
  function fallbackWriteText(text) {
    return new Promise(function (resolve, reject) {
      var textarea = document.createElement("textarea");
      textarea.value = text == null ? "" : String(text);
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.top = "-10000px";
      textarea.style.left = "-10000px";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      var succeeded = false;
      try {
        succeeded = document.execCommand("copy");
      } catch (error) {
        reject(error);
        return;
      } finally {
        textarea.remove();
      }
      if (!succeeded) {
        reject(new Error("execCommand('copy') returned false."));
        return;
      }
      resolve();
    });
  }

  function installPolyfill() {
    if (typeof navigator === "undefined") {
      return;
    }

    var original = navigator.clipboard && navigator.clipboard.writeText
      ? navigator.clipboard.writeText.bind(navigator.clipboard)
      : null;

    if (!navigator.clipboard) {
      try {
        Object.defineProperty(navigator, "clipboard", {
          value: {},
          configurable: true
        });
      } catch (error) {
        console.warn("Clipboard polyfill unavailable:", error);
        return;
      }
    }

    if (!navigator.clipboard) {
      return;
    }

    navigator.clipboard.writeText = function (text) {
      text = text == null ? "" : String(text);
      if (original) {
        return original(text).catch(function () {
          return fallbackWriteText(text);
        });
      }
      return fallbackWriteText(text);
    };

    if (typeof navigator.clipboard.readText !== "function") {
      navigator.clipboard.readText = function () {
        return Promise.reject(new Error("navigator.clipboard.readText is not implemented in this context."));
      };
    }
  }

  if (typeof window !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", installPolyfill, { once: true });
    } else {
      installPolyfill();
    }
  }
})();
</script>
"""
