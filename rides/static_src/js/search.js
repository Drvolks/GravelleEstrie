// Client-side search & filtering for the ride grid. All rides are rendered
// server-side; this just toggles card visibility based on the controls.
(function () {
  "use strict";

  const search = document.getElementById("search");
  const sortBy = document.getElementById("sort-by");
  const sortDirection = document.getElementById("sort-direction");
  const sortDirectionIcon = sortDirection.querySelector(".sort-direction-icon");
  const distanceSlider = document.getElementById("distance-slider");
  const distanceMin = document.getElementById("distance-min");
  const distanceMax = document.getElementById("distance-max");
  const elevationSlider = document.getElementById("elevation-slider");
  const elevationMin = document.getElementById("elevation-min");
  const elevationMax = document.getElementById("elevation-max");
  const distanceMinOut = document.getElementById("distance-min-out");
  const distanceMaxOut = document.getElementById("distance-max-out");
  const elevationMinOut = document.getElementById("elevation-min-out");
  const elevationMaxOut = document.getElementById("elevation-max-out");
  const adminWithoutRavitoFilter = document.getElementById("admin-without-ravito-filter");
  const adminWithoutRavito = document.getElementById("admin-without-ravito");
  const adminWithoutParkingFilter = document.getElementById("admin-without-parking-filter");
  const adminWithoutParking = document.getElementById("admin-without-parking");
  const adminWithoutPlaisirFilter = document.getElementById("admin-without-plaisir-filter");
  const adminWithoutPlaisir = document.getElementById("admin-without-plaisir");
  const reset = document.getElementById("reset");
  const countEl = document.getElementById("result-count");
  const emptyEl = document.getElementById("empty");
  const grid = document.getElementById("rides");
  const cards = Array.from(document.querySelectorAll(".card"));
  const isAdmin = new URLSearchParams(window.location.search).get("admin") === "true";
  let previousSortField = sortBy.value;

  if (!cards.length) return;

  if (isAdmin && adminWithoutRavitoFilter) {
    adminWithoutRavitoFilter.hidden = false;
  }
  if (isAdmin && adminWithoutParkingFilter) {
    adminWithoutParkingFilter.hidden = false;
  }
  if (isAdmin && adminWithoutPlaisirFilter) {
    adminWithoutPlaisirFilter.hidden = false;
  }

  function normalize(s) {
    return (s || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, ""); // strip accents
  }

  function compareText(a, b) {
    return a.localeCompare(b, "fr", { sensitivity: "base" });
  }

  function getSortDirection() {
    return sortDirection.dataset.direction === "desc" ? "desc" : "asc";
  }

  function setSortDirection(direction) {
    const value = direction === "desc" ? "desc" : "asc";
    const label = value === "desc" ? "Tri descendant" : "Tri ascendant";
    sortDirection.dataset.direction = value;
    sortDirection.setAttribute("aria-label", label);
    sortDirection.title = label;
    sortDirectionIcon.textContent = value === "desc" ? "\u2193" : "\u2191";
  }

  function normalizeRange(minInput, maxInput, activeInput) {
    let min = Number(minInput.value);
    let max = Number(maxInput.value);

    if (min > max) {
      if (activeInput === minInput) {
        min = max;
        minInput.value = String(min);
      } else if (activeInput === maxInput) {
        max = min;
        maxInput.value = String(max);
      } else {
        [min, max] = [max, min];
        minInput.value = String(min);
        maxInput.value = String(max);
      }
    }

    return [min, max];
  }

  function updateRangeSlider(slider, minInput, maxInput) {
    const min = Number(minInput.min);
    const max = Number(maxInput.max);
    const span = max - min || 1;
    const low = ((Number(minInput.value) - min) / span) * 100;
    const high = ((Number(maxInput.value) - min) / span) * 100;

    slider.style.setProperty("--range-low", `${low}%`);
    slider.style.setProperty("--range-high", `${high}%`);
  }

  function sortCards() {
    const sorted = cards.slice();
    const field = sortBy.value;
    const direction = getSortDirection() === "desc" ? -1 : 1;

    sorted.sort(function (a, b) {
      let result;
      if (field === "rating") {
        result = Number(a.dataset.ratingAverage || 0) - Number(b.dataset.ratingAverage || 0)
          || Number(a.dataset.ratingVotes || 0) - Number(b.dataset.ratingVotes || 0)
          || compareText(a.dataset.name, b.dataset.name);
        return direction * result;
      }
      if (field === "votes") {
        result = Number(a.dataset.ratingVotes || 0) - Number(b.dataset.ratingVotes || 0)
          || Number(a.dataset.ratingAverage || 0) - Number(b.dataset.ratingAverage || 0)
          || compareText(a.dataset.name, b.dataset.name);
        return direction * result;
      }
      if (field === "distance") {
        result = Number(a.dataset.distance) - Number(b.dataset.distance)
          || compareText(a.dataset.name, b.dataset.name);
        return direction * result;
      }
      if (field === "elevation") {
        result = Number(a.dataset.elevation) - Number(b.dataset.elevation)
          || compareText(a.dataset.name, b.dataset.name);
        return direction * result;
      }
      if (field === "created") {
        result = compareText(a.dataset.created, b.dataset.created)
          || compareText(a.dataset.name, b.dataset.name);
        return direction * result;
      }
      if (field === "city") {
        if (!a.dataset.city && b.dataset.city) return 1;
        if (a.dataset.city && !b.dataset.city) return -1;
        result = compareText(a.dataset.city, b.dataset.city)
          || compareText(a.dataset.name, b.dataset.name);
        return direction * result;
      }
      return direction * compareText(a.dataset.name, b.dataset.name);
    });

    for (const card of sorted) {
      grid.appendChild(card);
    }
  }

  function apply(activeInput) {
    sortCards();

    const q = normalize(search.value.trim());
    const [minDist, maxDist] = normalizeRange(distanceMin, distanceMax, activeInput);
    const [minElev, maxElev] = normalizeRange(elevationMin, elevationMax, activeInput);
    const onlyWithoutRavito = Boolean(isAdmin && adminWithoutRavito && adminWithoutRavito.checked);
    const onlyWithoutParking = Boolean(isAdmin && adminWithoutParking && adminWithoutParking.checked);
    const onlyWithoutPlaisir = Boolean(isAdmin && adminWithoutPlaisir && adminWithoutPlaisir.checked);

    updateRangeSlider(distanceSlider, distanceMin, distanceMax);
    updateRangeSlider(elevationSlider, elevationMin, elevationMax);

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
      const ravitoCount = Number(card.dataset.ravitos || 0);
      const parkingCount = Number(card.dataset.parkings || 0);
      const plaisirCount = Number(card.dataset.plaisirs || 0);

      const matchesText = !q || name.includes(q) || city.includes(q);
      const matchesDist = d >= minDist && d <= maxDist;
      const matchesElev = e >= minElev && e <= maxElev;
      const matchesRavito = !onlyWithoutRavito || ravitoCount === 0;
      const matchesParking = !onlyWithoutParking || parkingCount === 0;
      const matchesPlaisir = !onlyWithoutPlaisir || plaisirCount === 0;
      const show = matchesText && matchesDist && matchesElev && matchesRavito && matchesParking && matchesPlaisir;

      card.hidden = !show;
      if (show) visible++;
    }
    countEl.textContent = visible;
    emptyEl.hidden = visible !== 0;
  }

  search.addEventListener("input", function () { apply(); });
  sortBy.addEventListener("change", function () {
    if (
      (sortBy.value === "rating" || sortBy.value === "votes") &&
      previousSortField !== "rating" &&
      previousSortField !== "votes"
    ) {
      setSortDirection("desc");
    }
    previousSortField = sortBy.value;
    apply();
  });
  document.addEventListener("gravelle:ratings-updated", function () { apply(); });
  sortDirection.addEventListener("click", function () {
    setSortDirection(getSortDirection() === "desc" ? "asc" : "desc");
    apply();
  });
  distanceMin.addEventListener("input", function (event) { apply(event.currentTarget); });
  distanceMax.addEventListener("input", function (event) { apply(event.currentTarget); });
  elevationMin.addEventListener("input", function (event) { apply(event.currentTarget); });
  elevationMax.addEventListener("input", function (event) { apply(event.currentTarget); });
  if (adminWithoutRavito) {
    adminWithoutRavito.addEventListener("change", function () { apply(); });
  }
  if (adminWithoutPlaisir) {
    adminWithoutPlaisir.addEventListener("change", function () { apply(); });
  }
  if (adminWithoutParking) {
    adminWithoutParking.addEventListener("change", function () { apply(); });
  }
  reset.addEventListener("click", function () {
    search.value = "";
    sortBy.value = "name";
    previousSortField = "name";
    setSortDirection("asc");
    distanceMin.value = distanceMin.min;
    distanceMax.value = distanceMax.max;
    elevationMin.value = elevationMin.min;
    elevationMax.value = elevationMax.max;
    if (adminWithoutRavito) {
      adminWithoutRavito.checked = false;
    }
    if (adminWithoutPlaisir) {
      adminWithoutPlaisir.checked = false;
    }
    if (adminWithoutParking) {
      adminWithoutParking.checked = false;
    }
    apply();
  });

  setSortDirection(getSortDirection());
  apply();
})();
