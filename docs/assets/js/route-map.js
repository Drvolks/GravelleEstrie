(() => {
  const elevationHoverEvent = "gravelle:elevation-hover";
  const elevationLeaveEvent = "gravelle:elevation-leave";
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
  let routeCoordinates = [];
  let routeDistances = [];
  let routeDistance = 0;
  let profileCursor = null;
  let cursorAnimationFrame = 0;
  let pendingCursorPosition = null;

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

  const coordinateAtProfilePosition = ({ distanceM, maxDistanceM }) => {
    if (
      routeCoordinates.length < 2 ||
      routeDistance <= 0 ||
      !Number.isFinite(distanceM) ||
      !Number.isFinite(maxDistanceM) ||
      maxDistanceM <= 0
    ) {
      return null;
    }

    const progress = Math.max(0, Math.min(1, distanceM / maxDistanceM));
    const targetDistance = progress * routeDistance;
    let low = 1;
    let high = routeDistances.length - 1;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (routeDistances[middle] < targetDistance) low = middle + 1;
      else high = middle;
    }

    const segmentStartDistance = routeDistances[low - 1];
    const segmentLength = routeDistances[low] - segmentStartDistance;
    const segmentProgress =
      segmentLength > 0
        ? (targetDistance - segmentStartDistance) / segmentLength
        : 0;
    const start = routeCoordinates[low - 1];
    const end = routeCoordinates[low];
    return [
      start[0] + (end[0] - start[0]) * segmentProgress,
      start[1] + (end[1] - start[1]) * segmentProgress,
    ];
  };

  const updateProfileCursor = () => {
    cursorAnimationFrame = 0;
    if (!pendingCursorPosition) return;

    const coordinate = coordinateAtProfilePosition(pendingCursorPosition);
    if (!coordinate) return;
    if (!profileCursor) {
      profileCursor = window.L.circleMarker(coordinate, {
        radius: 8,
        color: "#ffffff",
        weight: 3,
        fillColor: "#0f1e3d",
        fillOpacity: 1,
        opacity: 1,
        interactive: false,
        className: "route-map-profile-cursor",
      }).addTo(map);
    } else {
      profileCursor.setLatLng(coordinate);
    }
    profileCursor.bringToFront();
  };

  window.addEventListener(elevationHoverEvent, (event) => {
    pendingCursorPosition = event.detail;
    if (cursorAnimationFrame) return;
    cursorAnimationFrame = requestAnimationFrame(updateProfileCursor);
  });
  window.addEventListener(elevationLeaveEvent, () => {
    pendingCursorPosition = null;
    cancelAnimationFrame(cursorAnimationFrame);
    cursorAnimationFrame = 0;
    if (profileCursor) {
      map.removeLayer(profileCursor);
      profileCursor = null;
    }
  });

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

      routeCoordinates = route;
      routeDistances = [0];
      for (let index = 1; index < route.length; index += 1) {
        routeDistance += map.distance(route[index - 1], route[index]);
        routeDistances.push(routeDistance);
      }

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
      if (pendingCursorPosition) updateProfileCursor();
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
