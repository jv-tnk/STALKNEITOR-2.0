(function () {
  "use strict";

  const PAGE_ID = "train-page";
  const KEY_HIDE_RATING = "train:blind:hide_rating";
  const KEY_HIDE_TAGS = "train:blind:hide_tags";

  function readBool(key) {
    try {
      return localStorage.getItem(key) === "1";
    } catch (_) {
      return false;
    }
  }

  function writeBool(key, value) {
    try {
      localStorage.setItem(key, value ? "1" : "0");
    } catch (_) {
      // ignore
    }
  }

  function applyClasses(pageEl, hideRating, hideTags) {
    if (!pageEl) return;
    pageEl.classList.toggle("train-hide-rating", !!hideRating);
    pageEl.classList.toggle("train-hide-tags", !!hideTags);
  }

  function syncControls(hideRating, hideTags) {
    const ratingInputs = document.querySelectorAll("[data-blind-toggle='rating']");
    const tagsInputs = document.querySelectorAll("[data-blind-toggle='tags']");
    ratingInputs.forEach((el) => {
      if (el instanceof HTMLInputElement) el.checked = !!hideRating;
    });
    tagsInputs.forEach((el) => {
      if (el instanceof HTMLInputElement) el.checked = !!hideTags;
    });
  }

  function configure() {
    const pageEl = document.getElementById(PAGE_ID);
    if (!pageEl) return;

    const hideRating = readBool(KEY_HIDE_RATING);
    const hideTags = readBool(KEY_HIDE_TAGS);
    applyClasses(pageEl, hideRating, hideTags);
    syncControls(hideRating, hideTags);
  }

  function onToggleChange(event) {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    const kind = target.dataset.blindToggle;
    if (kind !== "rating" && kind !== "tags") return;

    if (kind === "rating") {
      writeBool(KEY_HIDE_RATING, target.checked);
    } else {
      writeBool(KEY_HIDE_TAGS, target.checked);
    }
    configure();
  }

  document.addEventListener("DOMContentLoaded", configure);
  document.body.addEventListener("htmx:afterSwap", configure);
  document.body.addEventListener("change", onToggleChange);
})();
