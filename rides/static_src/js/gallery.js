// Lightweight photo popover for ride detail pages.
(function () {
  "use strict";

  const gallery = document.querySelector("[data-photo-gallery]");
  const dialog = document.getElementById("photo-dialog");
  if (!gallery || !dialog) return;

  const dialogImage = dialog.querySelector(".photo-dialog-image");
  const close = dialog.querySelector("[data-photo-close]");
  const buttons = Array.from(gallery.querySelectorAll(".ride-photo-thumb"));

  function openPhoto(button) {
    dialogImage.src = button.dataset.fullSrc || "";
    dialogImage.alt = button.dataset.fullAlt || "";
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
  }

  function closePhoto() {
    if (typeof dialog.close === "function") {
      dialog.close();
    } else {
      dialog.removeAttribute("open");
    }
    dialogImage.removeAttribute("src");
  }

  for (const button of buttons) {
    button.addEventListener("click", function () {
      openPhoto(button);
    });
  }

  close.addEventListener("click", closePhoto);
  dialog.addEventListener("close", function () {
    dialogImage.removeAttribute("src");
  });
  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) {
      closePhoto();
    }
  });
})();
