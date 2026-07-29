(() => {
  const elevationHoverEvent = "gravelle:elevation-hover";
  const elevationLeaveEvent = "gravelle:elevation-leave";
  const chart = document.querySelector("[data-elevation-chart]");
  const dataElement = document.getElementById("route-elevation-profile");
  if (!chart || !dataElement) return;

  let profile = [];
  try {
    profile = JSON.parse(dataElement.textContent)
      .map((point) => [Number(point[0]), Number(point[1])])
      .filter(
        ([distance, elevation]) =>
          Number.isFinite(distance) &&
          Number.isFinite(elevation) &&
          distance >= 0,
      )
      .sort((left, right) => left[0] - right[0]);
  } catch (_error) {
    return;
  }
  if (profile.length < 2) return;

  const svgNamespace = "http://www.w3.org/2000/svg";
  const tooltip = chart.querySelector("[data-elevation-tooltip]");
  const tooltipAltitude = chart.querySelector(
    "[data-elevation-tooltip-altitude]",
  );
  const tooltipDistance = chart.querySelector(
    "[data-elevation-tooltip-distance]",
  );
  let animationFrame = 0;

  const formatDistance = (distanceM) => {
    if (distanceM < 1000) return `${Math.round(distanceM)} m`;
    return `${(distanceM / 1000).toFixed(1).replace(".", ",")} km`;
  };

  const svgElement = (name, attributes = {}) => {
    const element = document.createElementNS(svgNamespace, name);
    Object.entries(attributes).forEach(([attribute, value]) => {
      element.setAttribute(attribute, String(value));
    });
    return element;
  };

  const appendText = (svg, x, y, text, anchor, className) => {
    const label = svgElement("text", {
      x,
      y,
      "text-anchor": anchor,
      class: className,
    });
    label.textContent = text;
    svg.appendChild(label);
  };

  const closestPoint = (distanceM) => {
    let low = 0;
    let high = profile.length - 1;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (profile[middle][0] < distanceM) low = middle + 1;
      else high = middle;
    }
    if (
      low > 0 &&
      Math.abs(profile[low - 1][0] - distanceM) <
        Math.abs(profile[low][0] - distanceM)
    ) {
      return profile[low - 1];
    }
    return profile[low];
  };

  const render = () => {
    const width = Math.max(Math.round(chart.clientWidth), 300);
    const height = width < 520 ? 190 : 220;
    const margin = {
      top: 14,
      right: 18,
      bottom: 30,
      left: width < 520 ? 44 : 52,
    };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const maxDistance = Math.max(profile.at(-1)[0], 1);
    const elevations = profile.map((point) => point[1]);
    const rawMin = Math.min(...elevations);
    const rawMax = Math.max(...elevations);
    const tickStep = Math.max(25, Math.ceil((rawMax - rawMin) / 4 / 25) * 25);
    const minElevation = Math.floor(rawMin / tickStep) * tickStep;
    const maxElevation = Math.max(
      Math.ceil(rawMax / tickStep) * tickStep,
      minElevation + tickStep,
    );

    const xPosition = (distance) =>
      margin.left + (distance / maxDistance) * plotWidth;
    const yPosition = (elevation) =>
      margin.top +
      ((maxElevation - elevation) / (maxElevation - minElevation)) * plotHeight;

    const svg = svgElement("svg", {
      viewBox: `0 0 ${width} ${height}`,
      width: "100%",
      height,
      "aria-hidden": "true",
    });

    const horizontalTicks = 4;
    for (let index = 0; index <= horizontalTicks; index += 1) {
      const elevation =
        minElevation +
        ((maxElevation - minElevation) * index) / horizontalTicks;
      const y = yPosition(elevation);
      svg.appendChild(
        svgElement("line", {
          x1: margin.left,
          y1: y,
          x2: width - margin.right,
          y2: y,
          class: "elevation-grid-line",
        }),
      );
      appendText(
        svg,
        margin.left - 8,
        y + 4,
        `${Math.round(elevation)} m`,
        "end",
        "elevation-axis-label",
      );
    }

    const distanceTickCount = width < 520 ? 3 : 4;
    for (let index = 0; index <= distanceTickCount; index += 1) {
      const distance = (maxDistance * index) / distanceTickCount;
      const x = xPosition(distance);
      appendText(
        svg,
        x,
        height - 8,
        formatDistance(distance),
        index === 0 ? "start" : index === distanceTickCount ? "end" : "middle",
        "elevation-axis-label",
      );
    }

    const linePath = profile
      .map(
        ([distance, elevation], index) =>
          `${index === 0 ? "M" : "L"}${xPosition(distance).toFixed(2)},${yPosition(elevation).toFixed(2)}`,
      )
      .join(" ");
    const baseline = margin.top + plotHeight;
    const areaPath = `${linePath} L${xPosition(maxDistance)},${baseline} L${margin.left},${baseline} Z`;
    svg.appendChild(
      svgElement("path", {
        d: areaPath,
        class: "elevation-area",
      }),
    );
    svg.appendChild(
      svgElement("path", {
        d: linePath,
        class: "elevation-line",
      }),
    );

    const crosshair = svgElement("line", {
      y1: margin.top,
      y2: baseline,
      class: "elevation-crosshair",
    });
    const pointMarker = svgElement("circle", {
      r: 5,
      class: "elevation-point",
    });
    crosshair.setAttribute("visibility", "hidden");
    pointMarker.setAttribute("visibility", "hidden");
    svg.append(crosshair, pointMarker);

    const interaction = svgElement("rect", {
      x: margin.left,
      y: margin.top,
      width: plotWidth,
      height: plotHeight,
      class: "elevation-interaction",
    });
    interaction.addEventListener("pointermove", (event) => {
      const bounds = svg.getBoundingClientRect();
      const pointerX = Math.max(
        margin.left,
        Math.min(
          width - margin.right,
          ((event.clientX - bounds.left) / bounds.width) * width,
        ),
      );
      const distance = ((pointerX - margin.left) / plotWidth) * maxDistance;
      const point = closestPoint(distance);
      const x = xPosition(point[0]);
      const y = yPosition(point[1]);

      crosshair.setAttribute("x1", x);
      crosshair.setAttribute("x2", x);
      pointMarker.setAttribute("cx", x);
      pointMarker.setAttribute("cy", y);
      crosshair.setAttribute("visibility", "visible");
      pointMarker.setAttribute("visibility", "visible");

      if (tooltip && tooltipAltitude && tooltipDistance) {
        tooltipAltitude.textContent = `${Math.round(point[1])} m`;
        tooltipDistance.textContent = formatDistance(point[0]);
        tooltip.style.left = `${Math.max(62, Math.min(chart.clientWidth - 62, (x / width) * chart.clientWidth))}px`;
        const placeBelow = y < 54;
        tooltip.dataset.position = placeBelow ? "below" : "above";
        tooltip.style.top = `${placeBelow ? y + 10 : y - 8}px`;
        tooltip.hidden = false;
      }
      window.dispatchEvent(
        new CustomEvent(elevationHoverEvent, {
          detail: {
            distanceM: point[0],
            maxDistanceM: maxDistance,
          },
        }),
      );
    });
    const clearInteraction = () => {
      crosshair.setAttribute("visibility", "hidden");
      pointMarker.setAttribute("visibility", "hidden");
      if (tooltip) tooltip.hidden = true;
      window.dispatchEvent(new CustomEvent(elevationLeaveEvent));
    };
    interaction.addEventListener("pointerleave", clearInteraction);
    interaction.addEventListener("pointercancel", clearInteraction);
    svg.appendChild(interaction);

    chart.querySelector("svg")?.remove();
    chart.prepend(svg);
  };

  const scheduleRender = () => {
    cancelAnimationFrame(animationFrame);
    animationFrame = requestAnimationFrame(render);
  };
  render();
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(scheduleRender).observe(chart);
  } else {
    window.addEventListener("resize", scheduleRender);
  }
})();
