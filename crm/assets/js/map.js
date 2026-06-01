/**
 * Carte prospects — Google Maps si clé API, sinon Leaflet + OpenStreetMap.
 */
(function () {
  const state = {
    provider: "osm",
    map: null,
    leafletLayer: null,
    data: null,
    userMarker: null,
    agencyMarker: null,
    leadMarkers: [],
    infoWindow: null,
    leafletPopups: [],
    userPos: null,
    aroundMe: false,
    aroundKm: 15,
    mapsReady: false,
    loading: false,
  };

  function deps() {
    return {
      api: typeof api === "function" ? api : null,
      showToast: typeof showToast === "function" ? showToast : () => {},
      openDrawer: typeof openDrawer === "function" ? openDrawer : null,
      escapeHtml: typeof escapeHtml === "function" ? escapeHtml : (s) => String(s),
      formatPrice:
        typeof formatPrice === "function"
          ? formatPrice
          : (n) => `${Number(n || 0).toLocaleString("fr-FR")} €`,
    };
  }

  function haversineKm(lat1, lng1, lat2, lng2) {
    const R = 6371;
    const dLat = ((lat2 - lat1) * Math.PI) / 180;
    const dLng = ((lng2 - lng1) * Math.PI) / 180;
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos((lat1 * Math.PI) / 180) *
        Math.cos((lat2 * Math.PI) / 180) *
        Math.sin(dLng / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function loadGoogleMaps(apiKey) {
    if (window.google?.maps) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const cb = "__velioraGmapsReady";
      window[cb] = () => {
        delete window[cb];
        resolve();
      };
      const s = document.createElement("script");
      s.id = "veliora-gmaps-script";
      s.async = true;
      s.defer = true;
      s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(apiKey)}&language=fr&region=FR&callback=${cb}`;
      s.onerror = () => reject(new Error("Google Maps"));
      document.head.appendChild(s);
    });
  }

  function loadLeaflet() {
    if (window.L) return Promise.resolve();
    return new Promise((resolve, reject) => {
      if (!document.getElementById("leaflet-css")) {
        const link = document.createElement("link");
        link.id = "leaflet-css";
        link.rel = "stylesheet";
        link.href = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css";
        document.head.appendChild(link);
      }
      const existing = document.getElementById("leaflet-js");
      if (existing) {
        existing.addEventListener("load", () => resolve(), { once: true });
        existing.addEventListener("error", () => reject(new Error("Impossible de charger la carte (Leaflet bloqué)")), {
          once: true,
        });
        return;
      }
      const s = document.createElement("script");
      s.id = "leaflet-js";
      s.src = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js";
      s.onload = () => resolve();
      s.onerror = () => reject(new Error("Impossible de charger la carte (réseau ou pare-feu)"));
      document.head.appendChild(s);
    });
  }

  function resetMapContainer() {
    if (state.map?.remove) {
      try {
        state.map.remove();
      } catch {
        /* ignore */
      }
      state.map = null;
    }
    state.agencyMarker = null;
    state.userMarker = null;
    clearLeadMarkers();
    const el = document.getElementById("map-canvas");
    if (el && el._leaflet_id) {
      delete el._leaflet_id;
      el.innerHTML = "";
    }
  }

  function mapErrorMessage(err) {
    if (!err) return "Carte indisponible";
    const msg = err.message || String(err);
    if (!msg || msg === "0") {
      return "Carte indisponible — vérifiez votre connexion ou réessayez Actualiser";
    }
    return msg;
  }

  function setStatus(html) {
    const el = document.getElementById("map-status");
    if (el) el.innerHTML = html;
  }

  function setLegend(stats) {
    const el = document.getElementById("map-legend-count");
    if (!el || !stats) return;
    el.textContent = `${stats.on_map} annonce${stats.on_map > 1 ? "s" : ""} sur la carte`;
    const pending = document.getElementById("map-legend-pending");
    if (pending) {
      if (stats.pending_geocode > 0) {
        pending.hidden = false;
        pending.textContent = `${stats.pending_geocode} adresse(s) en cours de placement — cliquez Actualiser dans quelques secondes`;
      } else {
        pending.hidden = true;
      }
    }
  }

  function pipelineLabel(key) {
    const labels = {
      nouveau: "À contacter",
      contacte: "Contacté",
      visite: "Visite",
      mandat: "Mandat",
      perdu: "Perdu",
    };
    return labels[key] || key;
  }

  function popupHtml(m) {
    const { escapeHtml: esc, formatPrice } = deps();
    const price =
      typeof formatPrice === "function"
        ? formatPrice(m)
        : `${Number(m.price || 0).toLocaleString("fr-FR")} €`;
    return `<strong>${esc(m.title)}</strong><br>${esc(m.address || "")}<br>${esc(price)} · Score ${m.mandate_score || m.score || 0}<br><button type="button" class="btn btn-primary btn-sm map-infowindow-btn" data-lead-id="${m.id}">Ouvrir la fiche</button>`;
  }

  function wirePopupButton(container) {
    container?.querySelector(".map-infowindow-btn")?.addEventListener("click", (e) => {
      const id = parseInt(e.currentTarget.dataset.leadId, 10);
      if (id && deps().openDrawer) deps().openDrawer(id);
    });
  }

  function filteredMarkers() {
    const markers = state.data?.markers || [];
    if (!state.aroundMe || !state.userPos) return markers;
    return markers.filter(
      (m) => haversineKm(state.userPos.lat, state.userPos.lng, m.lat, m.lng) <= state.aroundKm,
    );
  }

  function clearLeadMarkers() {
    if (state.provider === "google") {
      state.leadMarkers.forEach((m) => m.setMap(null));
    } else if (state.map && window.L) {
      state.leadMarkers.forEach((m) => state.map.removeLayer(m));
      state.leafletPopups.forEach((p) => state.map.removeLayer(p));
    }
    state.leadMarkers = [];
    state.leafletPopups = [];
  }

  function fitMapBounds() {
    const list = filteredMarkers();
    const ag = state.data?.agency;

    if (state.provider === "google" && state.map && window.google?.maps) {
      const bounds = new google.maps.LatLngBounds();
      let n = 0;
      if (state.agencyMarker?.getPosition()) {
        bounds.extend(state.agencyMarker.getPosition());
        n++;
      }
      if (state.userPos) {
        bounds.extend(state.userPos);
        n++;
      }
      list.forEach((m) => {
        bounds.extend({ lat: m.lat, lng: m.lng });
        n++;
      });
      if (n === 0) {
        state.map.setCenter({ lat: 46.6, lng: 2.4 });
        state.map.setZoom(6);
      } else if (n === 1) {
        state.map.setCenter(bounds.getCenter());
        state.map.setZoom(14);
      } else {
        state.map.fitBounds(bounds, { top: 80, right: 40, bottom: 40, left: 40 });
      }
      return;
    }

    if (state.provider === "osm" && state.map && window.L) {
      const pts = [];
      if (ag?.lat && ag?.lng) pts.push([ag.lat, ag.lng]);
      if (state.userPos) pts.push([state.userPos.lat, state.userPos.lng]);
      list.forEach((m) => pts.push([m.lat, m.lng]));
      if (!pts.length) {
        state.map.setView([46.6, 2.4], 6);
      } else if (pts.length === 1) {
        state.map.setView(pts[0], 14);
      } else {
        state.map.fitBounds(L.latLngBounds(pts), { padding: [50, 50] });
      }
    }
  }

  function renderLeadMarkers() {
    const list = filteredMarkers();
    const { openDrawer } = deps();
    clearLeadMarkers();

    if (state.provider === "google" && state.map && window.google?.maps) {
      list.forEach((m) => {
        const marker = new google.maps.Marker({
          map: state.map,
          position: { lat: m.lat, lng: m.lng },
          title: m.title,
          icon: {
            path: google.maps.SymbolPath.CIRCLE,
            fillColor: "#c45c26",
            fillOpacity: 1,
            strokeColor: "#ffffff",
            strokeWeight: 2,
            scale: 7,
          },
        });
        marker.addListener("click", () => {
          state.infoWindow.setContent(popupHtml(m));
          state.infoWindow.open({ anchor: marker, map: state.map });
          setTimeout(() => wirePopupButton(document.querySelector(".map-infowindow")), 0);
        });
        state.leadMarkers.push(marker);
      });
    } else if (state.provider === "osm" && state.map && window.L) {
      list.forEach((m) => {
        const marker = L.circleMarker([m.lat, m.lng], {
          radius: 8,
          color: "#fff",
          weight: 2,
          fillColor: "#c45c26",
          fillOpacity: 1,
        }).addTo(state.map);
        marker.bindPopup(popupHtml(m));
        marker.on("popupopen", (ev) => wirePopupButton(ev.popup.getElement()));
        state.leadMarkers.push(marker);
      });
    }
    fitMapBounds();
  }

  function placeAgencyMarker() {
    const ag = state.data?.agency;
    if (state.agencyMarker) {
      if (state.provider === "google") state.agencyMarker.setMap(null);
      else if (state.map?.removeLayer) state.map.removeLayer(state.agencyMarker);
      state.agencyMarker = null;
    }
    if (!ag?.lat || !ag?.lng || !state.map) return;

    if (state.provider === "google" && window.google?.maps) {
      state.agencyMarker = new google.maps.Marker({
        map: state.map,
        position: { lat: ag.lat, lng: ag.lng },
        title: ag.name || "Agence",
        icon: {
          path: google.maps.SymbolPath.CIRCLE,
          fillColor: "#1e3a5f",
          fillOpacity: 1,
          strokeColor: "#ffffff",
          strokeWeight: 2,
          scale: 11,
        },
        zIndex: 1000,
      });
      const { escapeHtml: esc } = deps();
      state.agencyMarker.addListener("click", () => {
        state.infoWindow.setContent(
          `<div class="map-infowindow"><strong>${esc(ag.name || "Agence")}</strong><p>${esc(ag.address_line || "")}</p></div>`,
        );
        state.infoWindow.open({ anchor: state.agencyMarker, map: state.map });
      });
    } else if (window.L) {
      state.agencyMarker = L.circleMarker([ag.lat, ag.lng], {
        radius: 11,
        color: "#fff",
        weight: 2,
        fillColor: "#1e3a5f",
        fillOpacity: 1,
      }).addTo(state.map);
      const { escapeHtml: esc } = deps();
      state.agencyMarker.bindPopup(
        `<strong>${esc(ag.name || "Agence")}</strong><br>${esc(ag.address_line || "")}`,
      );
    }
  }

  function initGoogleMap() {
    const el = document.getElementById("map-canvas");
    if (!el) return;
    state.map = new google.maps.Map(el, {
      center: { lat: 46.6, lng: 2.4 },
      zoom: 6,
      mapTypeControl: false,
      streetViewControl: false,
      fullscreenControl: true,
      gestureHandling: "greedy",
    });
    state.infoWindow = new google.maps.InfoWindow();
    state.mapsReady = true;
  }

  function initOsmMap() {
    const el = document.getElementById("map-canvas");
    if (!el || !window.L) {
      throw new Error("Carte OpenStreetMap non initialisée");
    }
    state.map = L.map(el, { zoomControl: true }).setView([46.6, 2.4], 6);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap",
      maxZoom: 19,
    }).addTo(state.map);
    state.mapsReady = true;
  }

  async function initMapInstance(data) {
    resetMapContainer();
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

    const wantGoogle = data.maps_provider === "google" && data.maps_api_key;
    let lastErr = null;

    if (wantGoogle) {
      try {
        await loadGoogleMaps(data.maps_api_key);
        state.provider = "google";
        initGoogleMap();
      } catch (err) {
        lastErr = err;
        resetMapContainer();
      }
    }

    if (!state.map) {
      try {
        await loadLeaflet();
        state.provider = "osm";
        initOsmMap();
      } catch (err) {
        throw lastErr || err;
      }
    }

    if (!state.map) {
      throw new Error("Impossible d’afficher la carte");
    }

    placeAgencyMarker();
    renderLeadMarkers();

    setTimeout(() => {
      if (state.provider === "google" && window.google?.maps) {
        google.maps.event.trigger(state.map, "resize");
      } else if (state.map?.invalidateSize) {
        state.map.invalidateSize(true);
      }
      fitMapBounds();
    }, 150);
  }

  async function fetchMapData() {
    const { api: apiFn } = deps();
    if (!apiFn) throw new Error("API indisponible");
    return apiFn("/map", { timeoutMs: 45000 });
  }

  function locateUser() {
    const { showToast } = deps();
    if (!navigator.geolocation) {
      showToast("Géolocalisation non disponible", "warning");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        state.userPos = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        if (!state.map) return;

        if (state.userMarker) {
          if (state.provider === "google") state.userMarker.setMap(null);
          else state.map.removeLayer(state.userMarker);
        }

        if (state.provider === "google" && window.google?.maps) {
          state.userMarker = new google.maps.Marker({
            map: state.map,
            position: state.userPos,
            title: "Vous",
            icon: {
              path: google.maps.SymbolPath.CIRCLE,
              fillColor: "#2563eb",
              fillOpacity: 1,
              strokeColor: "#ffffff",
              strokeWeight: 2,
              scale: 9,
            },
            zIndex: 999,
          });
          state.map.panTo(state.userPos);
          if (state.map.getZoom() < 13) state.map.setZoom(13);
        } else if (window.L) {
          state.userMarker = L.circleMarker([state.userPos.lat, state.userPos.lng], {
            radius: 9,
            color: "#fff",
            weight: 2,
            fillColor: "#2563eb",
            fillOpacity: 1,
          }).addTo(state.map);
          state.map.setView([state.userPos.lat, state.userPos.lng], 13);
        }
        renderLeadMarkers();
        showToast("Position affichée", "success");
      },
      (err) => {
        const msg =
          err.code === 1
            ? "Autorisez la géolocalisation dans le navigateur"
            : "Impossible d'obtenir votre position";
        showToast(msg, "warning");
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 },
    );
  }

  function centerOnAgency() {
    const ag = state.data?.agency;
    const { showToast } = deps();
    if (!ag?.lat || !ag?.lng) {
      showToast("Renseignez l'adresse dans Fiche agence (Mandats)", "warning");
      document.getElementById("btn-agency-legal-profile")?.click();
      return;
    }
    if (state.provider === "google" && state.map) {
      state.map.panTo({ lat: ag.lat, lng: ag.lng });
      if (state.map.getZoom() < 14) state.map.setZoom(14);
    } else if (state.map) {
      state.map.setView([ag.lat, ag.lng], 14);
    }
  }

  function showSetupPanel(data) {
    const panel = document.getElementById("map-setup-panel");
    const canvas = document.getElementById("map-canvas-wrap");
    if (!panel) return;
    const hints = data?.hints || {};
    let msg = "";
    if (hints.using_osm) {
      msg =
        "<p class=\"form-hint\">Carte OpenStreetMap (aucune clé Google requise). Optionnel : <code>GOOGLE_MAPS_API_KEY</code> pour Google Maps.</p>";
    }
    if (hints.no_agency_address) {
      msg +=
        "<p><strong>Adresse agence</strong> — complétez la fiche agence pour afficher votre bureau.</p><button type=\"button\" class=\"btn btn-secondary btn-sm\" id=\"map-setup-agency\">Fiche agence</button>";
    }
    if (msg) {
      panel.innerHTML = msg;
      panel.hidden = false;
      canvas?.classList.add("has-setup-banner");
      document.getElementById("map-setup-agency")?.addEventListener("click", () => {
        document.getElementById("btn-agency-legal-profile")?.click();
      });
    } else {
      panel.hidden = true;
      panel.innerHTML = "";
      canvas?.classList.remove("has-setup-banner");
    }
  }

  async function enter() {
    if (state.loading) return;
    state.loading = true;
    setStatus("Chargement de la carte…");
    try {
      const data = await fetchMapData();
      if (data.ok === false) throw new Error(data.error || "Carte indisponible");
      if (data.error) throw new Error(data.error);
      state.data = data;
      setLegend(data.stats);
      showSetupPanel(data);

      state.mapsReady = false;
      await initMapInstance(data);

      const mode = state.provider === "google" ? "Google Maps" : "OpenStreetMap";
      const ag = data.agency;
      const onMap = data.stats?.on_map ?? 0;
      const pending = data.stats?.pending_geocode ?? 0;
      let status = `${mode} · ${onMap} annonce(s) sur la carte`;
      if (pending > 0) {
        status += ` · ${pending} en cours de géolocalisation (Actualiser)`;
      }
      if (ag?.address_line) status += ` · Agence : ${ag.address_line}`;
      setStatus(status);

      if (onMap === 0 && pending === 0) {
        deps().showToast(
          "Aucune annonce géolocalisée pour l’instant — lancez un crawl ou Actualiser",
          "info",
          6000,
        );
      }

      if (!state.userPos && navigator.geolocation) {
        setTimeout(() => locateUser(), 300);
      }
    } catch (err) {
      const msg = mapErrorMessage(err);
      deps().showToast(msg, "error");
      setStatus("Erreur de chargement — cliquez Actualiser");
    } finally {
      state.loading = false;
    }
  }

  function wireControls() {
    document.getElementById("map-btn-locate")?.addEventListener("click", locateUser);
    document.getElementById("map-btn-agency")?.addEventListener("click", centerOnAgency);
    document.getElementById("map-btn-refresh")?.addEventListener("click", () => enter());
    document.getElementById("map-filter-around")?.addEventListener("change", (e) => {
      state.aroundMe = e.target.checked;
      renderLeadMarkers();
    });
  }

  wireControls();

  function resize() {
    if (!state.map) return;
    if (state.provider === "google" && window.google?.maps) {
      google.maps.event.trigger(state.map, "resize");
    } else if (state.map?.invalidateSize) {
      state.map.invalidateSize(true);
    }
    setTimeout(() => fitMapBounds(), 80);
  }

  window.VelioraMap = { enter, locateUser, refresh: enter, resize };
})();
