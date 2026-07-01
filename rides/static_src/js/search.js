// Client-side search & filtering for the ride grid. All rides are rendered
// server-side; this just toggles card visibility based on the controls.
(function () {
  "use strict";

  const search = document.getElementById("search");
  const distanceMin = document.getElementById("distance-min");
  const distanceMax = document.getElementById("distance-max");
  const elevationMin = document.getElementById("elevation-min");
  const elevationMax = document.getElementById("elevation-max");
  const distanceMinOut = document.getElementById("distance-min-out");
  const distanceMaxOut = document.getElementById("distance-max-out");
  const elevationMinOut = document.getElementById("elevation-min-out");
  const elevationMaxOut = document.getElementById("elevation-max-out");
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
    let minDist = Number(distanceMin.value);
    let maxDist = Number(distanceMax.value);
    let minElev = Number(elevationMin.value);
    let maxElev = Number(elevationMax.value);

    if (minDist > maxDist) {
      [minDist, maxDist] = [maxDist, minDist];
    }
    if (minElev > maxElev) {
      [minElev, maxElev] = [maxElev, minElev];
    }

    distanceMinOut.textContent = minDist;
    distanceMaxOut.textContent = maxDist;
    elevationMinOut.textContent = minElev;
    elevationMaxOut.textContent = maxElev;

    let visible = 0;
    for (const card of cards) {
      const name = normalize(card.dataset.name);
      const city = normalize(card.dataset.city);
      const d = Number(card.dataset.distance);
      const e = Number(card.dataset.elevation);

      const matchesText = !q || name.includes(q) || city.includes(q);
      const matchesDist = d >= minDist && d <= maxDist;
      const matchesElev = e >= minElev && e <= maxElev;
      const show = matchesText && matchesDist && matchesElev;

      card.hidden = !show;
      if (show) visible++;
    }
    countEl.textContent = visible;
    emptyEl.hidden = visible !== 0;
  }

  search.addEventListener("input", apply);
  distanceMin.addEventListener("input", apply);
  distanceMax.addEventListener("input", apply);
  elevationMin.addEventListener("input", apply);
  elevationMax.addEventListener("input", apply);
  reset.addEventListener("click", function () {
    search.value = "";
    distanceMin.value = distanceMin.min;
    distanceMax.value = distanceMax.max;
    elevationMin.value = elevationMin.min;
    elevationMax.value = elevationMax.max;
    apply();
  });

  apply();
})();
