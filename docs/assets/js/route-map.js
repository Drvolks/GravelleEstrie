(() => {
  const mapElement = document.querySelector("[data-route-map]");
  const pointsElement = document.getElementById("route-map-points");
  if (!mapElement || !pointsElement) return;

  const statusElement = mapElement.querySelector("[data-route-map-status]");
  if (typeof window.L === "undefined") {
    if (statusElement) {
      statusElement.textContent = "La carte n'a pas pu être chargée.";
      statusElement.dataset.tone = "error";
    }
    return;
  }
  let points = [];
  try {
    points = JSON.parse(pointsElement.textContent);
  } catch (_error) {
    if (statusElement) statusElement.textContent = "La carte n'a pas pu être chargée.";
    return;
  }

  const markerStyles = {
    start: { label: "D", title: "Point de départ" },
    parking: { label: "P", title: "Stationnement" },
    ravito: { label: "R", title: "Ravito" },
    interest: { label: "i", title: "Point d'intérêt" },
    pleasure: { label: "+", title: "Plaisir après ride" },
  };
  const firstPoint = points[0];
  const initialCenter = firstPoint ? [firstPoint.lat, firstPoint.lng] : [45.4, -71.9];
  const map = window.L.map(mapElement, {
    center: initialCenter,
    zoom: 11,
    scrollWheelZoom: false,
    preferCanvas: true,
  });
  const bounds = window.L.latLngBounds([]);

  window.L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);

  const createPopup = (point) => {
    const popup = document.createElement("div");
    popup.className = "route-map-popup";

    const name = document.createElement("strong");
    name.textContent = point.name;
    popup.appendChild(name);

    if (point.detail) {
      const detail = document.createElement("span");
      detail.textContent = point.detail;
      popup.appendChild(detail);
    }

    if (point.map_url) {
      const link = document.createElement("a");
      link.href = point.map_url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "Ouvrir dans Google Maps";
      popup.appendChild(link);
    }
    return popup;
  };

  points.forEach((point) => {
    const markerStyle = markerStyles[point.category];
    const lat = Number(point.lat);
    const lng = Number(point.lng);
    if (!markerStyle || !Number.isFinite(lat) || !Number.isFinite(lng)) return;

    if (point.category === "start") {
      window.L.circleMarker([lat, lng], {
        radius: 11,
        color: "#e87722",
        weight: 5,
        fillColor: "#ffffff",
        fillOpacity: 0.92,
      })
        .bindPopup(createPopup(point))
        .addTo(map);
      bounds.extend([lat, lng]);
      return;
    }

    const icon = window.L.divIcon({
      className: `route-map-marker route-map-marker-${point.category}`,
      html: `<span aria-hidden="true">${markerStyle.label}</span>`,
      iconSize: [30, 30],
      iconAnchor: [15, 30],
      popupAnchor: [0, -27],
    });
    window.L.marker([lat, lng], {
      icon,
      title: `${markerStyle.title} : ${point.name}`,
      riseOnHover: true,
    })
      .bindPopup(createPopup(point))
      .addTo(map);
    bounds.extend([lat, lng]);
  });

  const fitMap = () => {
    if (bounds.isValid()) {
      map.fitBounds(bounds, { padding: [28, 28], maxZoom: 15 });
    }
    map.invalidateSize({ pan: false });
  };

  fetch(mapElement.dataset.gpxUrl)
    .then((response) => {
      if (!response.ok) throw new Error(`GPX HTTP ${response.status}`);
      return response.text();
    })
    .then((gpxText) => {
      const xml = new DOMParser().parseFromString(gpxText, "application/xml");
      if (xml.querySelector("parsererror")) throw new Error("GPX invalide");

      const trackElements = [
        ...xml.getElementsByTagNameNS("*", "trkpt"),
        ...xml.getElementsByTagNameNS("*", "rtept"),
      ];
      const route = trackElements
        .map((element) => [
          Number(element.getAttribute("lat")),
          Number(element.getAttribute("lon")),
        ])
        .filter(([lat, lng]) => Number.isFinite(lat) && Number.isFinite(lng));
      if (route.length < 2) throw new Error("Tracé GPX vide");

      window.L.polyline(route, {
        color: "#0f1e3d",
        weight: 8,
        opacity: 0.8,
        interactive: false,
      }).addTo(map);
      window.L.polyline(route, {
        color: "#e87722",
        weight: 4,
        opacity: 1,
        interactive: false,
      }).addTo(map);
      route.forEach((coordinate) => bounds.extend(coordinate));
      fitMap();
      if (statusElement) statusElement.remove();
    })
    .catch(() => {
      fitMap();
      if (statusElement) {
        statusElement.textContent =
          "Le tracé n'a pas pu être chargé. Les lieux restent disponibles.";
        statusElement.dataset.tone = "error";
      }
    });
})();
