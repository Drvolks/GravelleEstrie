// Ride star ratings backed by the Cloudflare Worker API.
(function () {
  "use strict";

  const VOTER_STORAGE_KEY = "gravelleestrie:voter-id";
  const VOTED_STORAGE_PREFIX = "gravelleestrie:rated:";
  const TURNSTILE_WAIT_MS = 5000;
  let memoryVoterId = "";

  const widgets = Array.from(document.querySelectorAll("[data-ride-rating]"));
  const ratingCards = Array.from(document.querySelectorAll(".card[data-ride-slug]"));
  if (!widgets.length && !ratingCards.length) return;

  function getStoredValue(key) {
    try {
      return localStorage.getItem(key);
    } catch (_error) {
      return null;
    }
  }

  function setStoredValue(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (_error) {
      // The server-side vote uniqueness still applies when storage is blocked.
    }
  }

  function getVoterId() {
    let voterId = getStoredValue(VOTER_STORAGE_KEY) || memoryVoterId;
    if (!voterId) {
      voterId = window.crypto && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      memoryVoterId = voterId;
      setStoredValue(VOTER_STORAGE_KEY, voterId);
    }
    return voterId;
  }

  function hasVoted(slug) {
    return getStoredValue(`${VOTED_STORAGE_PREFIX}${slug}`) === "1";
  }

  function markVoted(slug) {
    setStoredValue(`${VOTED_STORAGE_PREFIX}${slug}`, "1");
  }

  function waitForTurnstile(deadline) {
    if (window.turnstile) return Promise.resolve(window.turnstile);
    if (Date.now() > deadline) return Promise.resolve(null);
    return new Promise((resolve) => {
      window.setTimeout(() => resolve(waitForTurnstile(deadline)), 100);
    });
  }

  async function readJson(response) {
    try {
      return await response.json();
    } catch (_error) {
      return {};
    }
  }

  function setStatus(statusEl, message, tone) {
    if (!statusEl) return;
    statusEl.textContent = message || "";
    statusEl.dataset.tone = tone || "";
  }

  function setStars(starButtons, value) {
    for (const button of starButtons) {
      const rating = Number(button.dataset.ratingValue);
      button.classList.toggle("is-active", rating <= value);
      button.setAttribute("aria-checked", rating === value ? "true" : "false");
    }
  }

  function setDisplayStars(displayStars, value) {
    for (const star of displayStars) {
      const rating = displayStars.indexOf(star) + 1;
      star.classList.toggle("is-active", rating <= value);
    }
  }

  function renderSummary(averageEl, starButtons, summary, displayStars = []) {
    const count = Number(summary.vote_count || 0);
    const average = Number(summary.average_rating || 0);
    if (!count) {
      averageEl.textContent = "Aucune note pour le moment";
      setStars(starButtons, 0);
      setDisplayStars(displayStars, 0);
      return;
    }

    averageEl.textContent = `${average.toFixed(1)} / 5 (${count} vote${count > 1 ? "s" : ""})`;
    setStars(starButtons, Math.round(average));
    setDisplayStars(displayStars, Math.round(average));
  }

  function setVotingDisabled(starButtons, disabled) {
    for (const button of starButtons) {
      button.disabled = disabled;
    }
  }

  function ratingLabel(summary) {
    const count = Number(summary.vote_count || 0);
    const average = Number(summary.average_rating || 0);
    if (!count) return "";
    return `${average.toFixed(1)} ★ (${count})`;
  }

  async function loadIndexRatings() {
    if (!ratingCards.length) return;

    const apiBase = getRatingsApiBase();
    if (!apiBase) return;

    const cardsBySlug = new Map();
    for (const card of ratingCards) {
      const slug = card.dataset.rideSlug;
      const summaryEl = card.querySelector("[data-card-rating-summary]");
      if (!slug || !summaryEl) continue;
      cardsBySlug.set(slug, card);
    }
    if (!cardsBySlug.size) return;

    let response;
    try {
      response = await fetch(`${apiBase}/api/ratings`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ride_slugs: Array.from(cardsBySlug.keys()) }),
      });
    } catch (_error) {
      return;
    }

    if (!response.ok) return;
    const payload = await readJson(response);
    for (const summary of payload.summaries || []) {
      const card = cardsBySlug.get(summary.ride_slug);
      if (!card) continue;

      const average = Number(summary.average_rating || 0);
      const votes = Number(summary.vote_count || 0);
      card.dataset.ratingAverage = String(average);
      card.dataset.ratingVotes = String(votes);

      const summaryEl = card.querySelector("[data-card-rating-summary]");
      if (summaryEl) {
        summaryEl.textContent = ratingLabel(summary);
        summaryEl.hidden = votes <= 0;
        summaryEl.classList.toggle("has-rating", votes > 0);
      }
    }

    document.dispatchEvent(new CustomEvent("gravelle:ratings-updated"));
  }

  function getRatingsApiBase() {
    const detailWidget = document.querySelector("[data-ride-rating]");
    if (detailWidget && detailWidget.dataset.ratingsApiUrl) {
      return detailWidget.dataset.ratingsApiUrl.replace(/\/+$/, "");
    }
    const index = document.querySelector("[data-ratings-index]");
    const configured = index ? index.dataset.ratingsApiUrl || "" : "";
    return configured.replace(/\/+$/, "");
  }

  async function initWidget(widget) {
    const slug = widget.dataset.rideSlug;
    const apiBase = (widget.dataset.ratingsApiUrl || "").replace(/\/+$/, "");
    const siteKey = widget.dataset.turnstileSiteKey;
    const averageEl = widget.querySelector("[data-rating-average]");
    const statusEl = widget.querySelector("[data-rating-status]");
    const turnstileEl = widget.querySelector("[data-rating-turnstile]");
    const displayStars = Array.from(widget.querySelectorAll("[data-rating-display-star]"));
    const voteButton = widget.querySelector("[data-rating-open]");
    const dialog = widget.querySelector("[data-rating-dialog]");
    const closeButton = widget.querySelector("[data-rating-close]");
    const starButtons = Array.from(widget.querySelectorAll("[data-rating-value]"));

    if (!slug || !apiBase || !siteKey || !averageEl || !voteButton || !dialog || !starButtons.length) {
      widget.hidden = true;
      return;
    }

    const voted = hasVoted(slug);
    let submitting = false;
    let currentToken = "";
    let currentSummary = { vote_count: 0, average_rating: 0 };
    let turnstileWidgetId = null;
    let pendingRating = null;
    let turnstileReadyPromise = null;

    function closeDialog() {
      if (dialog.open && dialog.close) {
        dialog.close();
      } else {
        dialog.hidden = true;
      }
    }

    function openDialog() {
      if (dialog.showModal) {
        if (!dialog.open) dialog.showModal();
      } else {
        dialog.hidden = false;
      }
      setStatus(statusEl, "", "");
      ensureTurnstileReady();
    }

    function updateVotedState() {
      const votedNow = hasVoted(slug);
      voteButton.hidden = votedNow;
      widget.classList.toggle("has-voted", votedNow);
      if (votedNow) {
        setVotingDisabled(starButtons, true);
        if (turnstileEl) turnstileEl.hidden = true;
        closeDialog();
      }
    }

    function ensureTurnstileReady() {
      if (turnstileWidgetId !== null) return Promise.resolve(true);
      if (turnstileReadyPromise) return turnstileReadyPromise;

      turnstileReadyPromise = waitForTurnstile(Date.now() + TURNSTILE_WAIT_MS).then((turnstile) => {
        if (!turnstile || !turnstileEl) {
          setVotingDisabled(starButtons, true);
          setStatus(statusEl, "La validation anti-robot n'est pas disponible pour le moment.", "error");
          return false;
        }

        turnstileWidgetId = turnstile.render(turnstileEl, {
          sitekey: siteKey,
          action: "ride_rating",
          appearance: "interaction-only",
          execution: "execute",
          theme: "auto",
          callback(token) {
            currentToken = token;
            if (pendingRating !== null) {
              const rating = pendingRating;
              pendingRating = null;
              submitVote(rating);
              return;
            }
            setStatus(statusEl, "", "");
          },
          "expired-callback"() {
            currentToken = "";
            pendingRating = null;
            setVotingDisabled(starButtons, false);
            setStatus(statusEl, "Validation expirée. Réessayez votre vote.", "error");
          },
          "error-callback"() {
            currentToken = "";
            pendingRating = null;
            setVotingDisabled(starButtons, false);
            setStatus(statusEl, "Validation anti-robot indisponible.", "error");
          },
        });
        return true;
      });

      return turnstileReadyPromise;
    }

    for (const button of starButtons) {
      button.setAttribute("role", "radio");
      button.addEventListener("mouseenter", () => setStars(starButtons, Number(button.dataset.ratingValue)));
      button.addEventListener("focus", () => setStars(starButtons, Number(button.dataset.ratingValue)));
      button.addEventListener("mouseleave", () => renderSummary(averageEl, starButtons, currentSummary, displayStars));
      button.addEventListener("blur", () => renderSummary(averageEl, starButtons, currentSummary, displayStars));
      button.addEventListener("click", () => submitVote(Number(button.dataset.ratingValue)));
    }

    voteButton.addEventListener("click", openDialog);
    if (closeButton) closeButton.addEventListener("click", closeDialog);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) closeDialog();
    });

    if (voted) {
      setStatus(statusEl, "Votre vote est enregistré pour cette sortie.", "success");
    }
    updateVotedState();

    async function loadSummary() {
      const response = await fetch(`${apiBase}/api/ratings/${encodeURIComponent(slug)}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("summary failed");
      currentSummary = await response.json();
      renderSummary(averageEl, starButtons, currentSummary, displayStars);
    }

    async function submitVote(rating) {
      if (submitting || hasVoted(slug)) return;
      const turnstileReady = await ensureTurnstileReady();
      if (!turnstileReady) return;

      if (!currentToken) {
        if (window.turnstile && turnstileWidgetId !== null) {
          pendingRating = rating;
          setVotingDisabled(starButtons, true);
          setStatus(statusEl, "Validation anti-robot...", "");
          window.turnstile.execute(turnstileWidgetId);
          return;
        }
        setStatus(statusEl, "Validation anti-robot indisponible.", "error");
        return;
      }

      submitting = true;
      setVotingDisabled(starButtons, true);
      setStatus(statusEl, "Enregistrement du vote...", "");

      const response = await fetch(`${apiBase}/api/ratings/${encodeURIComponent(slug)}`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          rating,
          voter_id: getVoterId(),
          turnstile_token: currentToken,
        }),
      });

      const payload = await readJson(response);
      if (response.status === 409) {
        markVoted(slug);
        if (payload.summary) {
          currentSummary = payload.summary;
          renderSummary(averageEl, starButtons, currentSummary, displayStars);
        }
        setStatus(statusEl, "Vous avez déjà voté pour cette sortie.", "success");
        updateVotedState();
        submitting = false;
        return;
      }

      if (!response.ok) {
        if (window.turnstile && turnstileWidgetId !== null) {
          window.turnstile.reset(turnstileWidgetId);
          currentToken = "";
        }
        setVotingDisabled(starButtons, false);
        setStatus(statusEl, payload.error || "Le vote n'a pas pu être enregistré.", "error");
        submitting = false;
        return;
      }

      markVoted(slug);
      currentSummary = payload.summary || payload;
      renderSummary(averageEl, starButtons, currentSummary, displayStars);
      setStatus(statusEl, "Merci, votre vote est enregistré.", "success");
      updateVotedState();
      submitting = false;
    }

    try {
      await loadSummary();
    } catch (_error) {
      averageEl.textContent = "Note indisponible";
      setStatus(statusEl, "Impossible de charger les notes pour le moment.", "error");
    }
  }

  for (const widget of widgets) {
    initWidget(widget);
  }
  loadIndexRatings();
})();
