// Client-side search & filtering for the ride grid. All rides are rendered
// server-side; this just toggles card visibility based on the controls.
(function () {
  "use strict";

  const search = document.getElementById("search");
  const distance = document.getElementById("distance");
  const elevation = document.getElementById("elevation");
  const distanceOut = document.getElementById("distance-out");
  const elevationOut = document.getElementById("elevation-out");
  const reset = document.getElementById("reset");
  const countEl = document.getElementById("result-count");
  const emptyEl = document.getElementById("empty");
  const cards = Array.from(document.querySelectorAll(".card"));

  if (!cards.length) return;

  function normalize(s) {
    return (s || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, ""); // strip accents
  }

  function apply() {
    const q = normalize(search.value.trim());
    const maxDist = Number(distance.value);
    const maxElev = Number(elevation.value);
    distanceOut.textContent = maxDist;
    elevationOut.textContent = maxElev;

    let visible = 0;
    for (const card of cards) {
      const name = normalize(card.dataset.name);
      const city = normalize(card.dataset.city);
      const d = Number(card.dataset.distance);
      const e = Number(card.dataset.elevation);

      const matchesText = !q || name.includes(q) || city.includes(q);
      const matchesDist = d <= maxDist;
      const matchesElev = e <= maxElev;
      const show = matchesText && matchesDist && matchesElev;

      card.hidden = !show;
      if (show) visible++;
    }
    countEl.textContent = visible;
    emptyEl.hidden = visible !== 0;
  }

  search.addEventListener("input", apply);
  distance.addEventListener("input", apply);
  elevation.addEventListener("input", apply);
  reset.addEventListener("click", function () {
    search.value = "";
    distance.value = distance.max;
    elevation.value = elevation.max;
    apply();
  });

  apply();
})();
