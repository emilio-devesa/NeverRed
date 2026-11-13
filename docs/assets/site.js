// NeverRed project site: theme, mobile navigation and copy buttons.
(function () {
  "use strict";

  var root = document.documentElement;
  var toggle = document.getElementById("theme-toggle");
  var media = window.matchMedia("(prefers-color-scheme: light)");

  function currentTheme() {
    return root.getAttribute("data-theme") === "light" ? "light" : "dark";
  }

  function applyTheme(theme, persist) {
    root.setAttribute("data-theme", theme);
    if (toggle) {
      var next = theme === "dark" ? "light" : "dark";
      toggle.setAttribute("aria-label", "Switch to " + next + " theme");
      toggle.setAttribute("title", "Switch to " + next + " theme");
    }
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute("content", theme === "dark" ? "#0b1020" : "#f6f7fb");
    }
    if (persist) {
      try {
        localStorage.setItem("theme", theme);
      } catch (error) {
        /* Storage can be unavailable in private modes. */
      }
    }
  }

  applyTheme(currentTheme(), false);

  if (toggle) {
    toggle.addEventListener("click", function () {
      applyTheme(currentTheme() === "dark" ? "light" : "dark", true);
    });
  }

  if (media.addEventListener) {
    media.addEventListener("change", function (event) {
      var stored = null;
      try {
        stored = localStorage.getItem("theme");
      } catch (error) {
        stored = null;
      }
      if (!stored) {
        applyTheme(event.matches ? "light" : "dark", false);
      }
    });
  }

  var navToggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("site-nav");

  function closeNav() {
    if (!nav || !navToggle) {
      return;
    }
    nav.classList.remove("is-open");
    navToggle.setAttribute("aria-expanded", "false");
  }

  if (nav && navToggle) {
    navToggle.addEventListener("click", function () {
      var open = navToggle.getAttribute("aria-expanded") !== "true";
      nav.classList.toggle("is-open", open);
      navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });

    nav.addEventListener("click", function (event) {
      if (event.target.closest("a")) {
        closeNav();
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closeNav();
      }
    });
  }

  var header = document.querySelector(".site-header");
  function updateHeader() {
    if (header) {
      header.classList.toggle("is-scrolled", window.scrollY > 8);
    }
  }

  updateHeader();
  window.addEventListener("scroll", updateHeader, { passive: true });

  document.querySelectorAll(".copy-button").forEach(function (button) {
    button.addEventListener("click", function () {
      var target = document.getElementById(button.getAttribute("data-copy-target"));
      if (!target) {
        return;
      }

      var write = navigator.clipboard && navigator.clipboard.writeText
        ? navigator.clipboard.writeText(target.innerText)
        : Promise.reject(new Error("Clipboard unavailable"));

      write.then(function () {
        button.textContent = "Copied";
        button.setAttribute("data-copied", "true");
        window.setTimeout(function () {
          button.textContent = "Copy";
          button.removeAttribute("data-copied");
        }, 1800);
      }).catch(function () {
        button.textContent = "Select code";
        window.setTimeout(function () {
          button.textContent = "Copy";
        }, 1800);
      });
    });
  });
})();
