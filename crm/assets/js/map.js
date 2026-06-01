/**
 * Carte prospects — Google Maps si clé API, sinon Leaflet + OpenStreetMap.
 */
(function () {
  const LEAFLET_LOCAL = {
    css: "/crm/assets/vendor/leaflet/leaflet.css",
    js: "/crm/assets/vendor/leaflet/leaflet.js",
  };
  const LEAFLET_CDN = {
    css: "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css",
    js: "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js",
  };
  const TILE_URLS = [
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
  ];

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
    lastError: null,
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

  function loadScript(src, id, timeoutMs = 20000) {
    return new Promise((resolve, reject) => {
      if (id && document.getElementById(id)) {
        const el = document.getElementById(id);
        if (el.dataset.loaded === "1") {
          resolve();
          return;
        }
        el.addEventListener("load", () => resolve(), { once: true });
        el.addEventListener(
          "error",
          () => reject(new Error(`Impossible de charger ${src}`)),
          { once: true },
        );
        return;
      }
      const timer = setTimeout(() => {
        reject(new Error("Chargement de la carte expiré (réseau lent ou bloqué)"));
      }, timeoutMs);
      const s = document.createElement("script");
      if (id) s.id = id;
      s.async = true;
      s.src = src;
      s.onload = () => {
        clearTimeout(timer);
        s.dataset.loaded = "1";
        resolve();
      };
      s.onerror = () => {
        clearTimeout(timer);
        reject(new Error(`Impossible de charger la carte (${src})`));
      };
      document.head.appendChild(s);
    });
  }

  function loadStylesheet(href, id) {
    if (id && document.getElementById(id)) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const link = document.createElement("link");
      if (id) link.id = id;
      link.rel = "stylesheet";
      link.href = href;
      link.onload = () => resolve();
      link.onerror = () => reject(new Error("Feuille de style carte introuvable"));
      document.head.appendChild(link);
    });
  }

  function loadGoogleMaps(apiKey) {
    if (window.google?.maps) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const cb = "__velioraGmapsReady";
      const timer = setTimeout(() => {
        reject(new Error("Google Maps — délai dépassé"));
      }, 25000);
      window[cb] = () => {
        clearTimeout(timer);
        delete window[cb];
        resolve();
      };
      const s = document.createElement("script");
      s.id = "veliora-gmaps-script";
      s.async = true;
      s.defer = true;
      s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(apiKey)}&language=fr&region=FR&callback=${cb}`;
      s.onerror = () => {
        clearTimeout(timer);
        reject(new Error("Google Maps indisponible — bascule OpenStreetMap"));
      };
      document.head.appendChild(s);
    });
  }

  async function loadLeaflet() {
    if (window.L) return;
    const sources = [LEAFLET_LOCAL, LEAFLET_CDN];
    let lastErr = null;
    for (const src of sources) {
      try {
        await loadStylesheet(src.css, "leaflet-css");
        await loadScript(src.js, "leaflet-js");
        if (window.L) return;
      } catch (err) {
        lastErr = err;
        const css = document.getElementById("leaflet-css");
        const js = document.getElementById("leaflet-js");
        css?.remove();
        js?.remove();
      }
    }
    throw lastErr || new Error("Leaflet indisponible — vérifiez votre connexion");
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
    state.leafletLayer = null;
    state.agencyMarker = null;
    state.userMarker = null;
    clearLeadMarkers();
    const el = document.getElementById("map-canvas");
    if (el) {
      if (el._leaflet_id) delete el._leaflet_id;
      el.innerHTML = "";
      el.classList.remove("leaflet-container");
    }
  }

  function mapErrorMessage(err) {
    if (!err) return "Carte indisponible";
    if (typeof err === "string") return err || "Carte indisponible";
    const msg = (err.message || String(err) || "").trim();
    if (!msg || msg === "0") {
      if (err.name === "AbortError") {
        return "Délai dépassé — le serveur met trop de temps à répondre. Réessayez Actualiser.";
      }
      return "Carte indisponible — rechargez la page (Ctrl+F5) ou vérifiez que le serveur Veliora tourne.";
    }
    if (/billing|facturation|payment/i.test(msg)) {
      return "Google Maps nécessite la facturation GCP — la carte utilise OpenStreetMap sans facturation.";
    }
    return msg;
  }

  function setStatus(html, isError = false) {
    const el = document.getElementById("map-status");
    if (!el) return;
    el.innerHTML = html;
    el.classList.toggle("map-status--error", isError);
  }

  function setLegend(stats) {
    const el = document.getElementById("map-legend-count");
    if (!el || !stats) return;
    el.textContent = `${stats.on_map} annonce${stats.on_map > 1 ? "s" : ""} sur la carte`;
    const pending = document.getElementById("map-legend-pending");
    if (pending) {
      if (stats.pending_geocode > 0) {
        pending.hidden = false;
        pending.textContent = `${stats.pending_geocode} adresse(s) en cours de placement — Actualiser dans 30 s`;
      } else {
        pending.hidden = true;
      }
    }
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

  function waitForMapContainerSize() {
    return new Promise((resolve) => {
      const el = document.getElementById("map-canvas");
      if (!el) {
        resolve();
        return;
      }
      let tries = 0;
      const tick = () => {
        const rect = el.getBoundingClientRect();
        if (rect.width > 40 && rect.height > 40) {
          resolve();
          return;
        }
        tries += 1;
        if (tries > 24) {
          resolve();
          return;
        }
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
  }

  function initGoogleMap() {
    const el = document.getElementById("map-canvas");
    if (!el) throw new Error("Conteneur carte introuvable");
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

  function addOsmTileLayer() {
    if (!state.map || !window.L) return;
    if (state.leafletLayer) {
      state.map.removeLayer(state.leafletLayer);
      state.leafletLayer = null;
    }
    const url = TILE_URLS[0];
    state.leafletLayer = L.tileLayer(url, {
      attribution: "© OpenStreetMap",
      maxZoom: 19,
    });
    state.leafletLayer.addTo(state.map);
    state.leafletLayer.on("tileerror", () => {
      if (TILE_URLS[1] && state.leafletLayer?._url === TILE_URLS[0]) {
        state.map.removeLayer(state.leafletLayer);
        state.leafletLayer = L.tileLayer(TILE_URLS[1], {
          attribution: "© OpenStreetMap · HOT",
          maxZoom: 19,
          subdomains: "abc",
        }).addTo(state.map);
      }
    });
  }

  function initOsmMap() {
    const el = document.getElementById("map-canvas");
    if (!el || !window.L) {
      throw new Error("Carte OpenStreetMap non initialisée");
    }
    state.map = L.map(el, { zoomControl: true, preferCanvas: true }).setView([46.6, 2.4], 6);
    addOsmTileLayer();
    state.mapsReady = true;
  }

  async function initMapInstance(data) {
    resetMapContainer();
    await waitForMapContainerSize();
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

    const wantGoogle = data.maps_provider === "google" && data.maps_api_key;
    let lastErr = null;

    async function bootOsm() {
      await loadLeaflet();
      state.provider = "osm";
      initOsmMap();
    }

    if (!wantGoogle) {
      try {
        await bootOsm();
      } catch (err) {
        throw new Error(
          err?.message ||
            "OpenStreetMap indisponible — vérifiez /crm/assets/vendor/leaflet/ sur le serveur",
        );
      }
    } else {
      try {
        await loadGoogleMaps(data.maps_api_key);
        state.provider = "google";
        initGoogleMap();
      } catch (err) {
        lastErr = err;
        resetMapContainer();
        try {
          await bootOsm();
          deps().showToast(
            "Google Maps indisponible — carte OpenStreetMap affichée",
            "info",
            7000,
          );
        } catch (osmErr) {
          throw osmErr;
        }
      }
    }

    if (!state.map) {
      try {
        await bootOsm();
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
    }, 200);
  }

  async function fetchMapData(geocode = false) {
    const { api: apiFn } = deps();
    if (!apiFn) throw new Error("API indisponible — rechargez la page depuis le CRM Veliora");
    const q = geocode ? "?geocode=1" : "";
    return apiFn(`/map${q}`, { timeoutMs: geocode ? 45000 : 25000 });
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
    if (hints.billing_hint) {
      msg += `<p class="form-hint map-billing-hint">${hints.billing_hint}</p>`;
    } else if (hints.using_osm) {
      msg =
        '<p class="form-hint">Carte <strong>OpenStreetMap</strong> — fonctionne sans facturation Google. Clé optionnelle pour géocodage / Google Maps.</p>';
    }
    if (hints.google_key_set_osm_mode) {
      msg +=
        '<p class="form-hint">Pour passer en tuiles Google : facturation GCP + variable <code>GOOGLE_MAPS_JS=true</code>.</p>';
    }
    if (hints.no_agency_address) {
      msg +=
        '<p><strong>Adresse agence</strong> — complétez la fiche agence pour afficher votre bureau.</p><button type="button" class="btn btn-secondary btn-sm" id="map-setup-agency">Fiche agence</button>';
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

  function showMapError(msg) {
    const { escapeHtml: esc } = deps();
    setStatus(
      `<span class="map-status-error-text">${esc(msg)}</span> — <button type="button" class="btn btn-link btn-sm map-status-retry" id="map-status-retry">Actualiser</button>`,
      true,
    );
    document.getElementById("map-status-retry")?.addEventListener("click", () => enter(true));
  }

  async function enter(forceGeocode = false) {
    if (state.loading) return;
    state.loading = true;
    state.lastError = null;
    setStatus("Chargement de la carte…", false);
    try {
      const data = await fetchMapData(forceGeocode);
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
        status += ` · ${pending} en cours de géolocalisation (Actualiser dans ~30 s)`;
      }
      if (ag?.address_line) status += ` · Agence : ${ag.address_line}`;
      setStatus(status, false);

      if (onMap === 0 && pending === 0) {
        deps().showToast(
          "Aucune annonce géolocalisée — vérifiez les adresses des fiches ou lancez un crawl",
          "info",
          7000,
        );
      } else if (onMap === 0 && pending > 0) {
        deps().showToast(
          `${pending} adresse(s) en cours de placement — recliquez Actualiser dans 30 s`,
          "info",
          8000,
        );
      }

      if (!state.userPos && navigator.geolocation) {
        setTimeout(() => locateUser(), 400);
      }
    } catch (err) {
      state.lastError = err;
      console.error("[VelioraMap]", err);
      const msg = mapErrorMessage(err);
      deps().showToast(msg, "error");
      showMapError(msg);
    } finally {
      state.loading = false;
    }
  }

  function wireControls() {
    document.getElementById("map-btn-locate")?.addEventListener("click", locateUser);
    document.getElementById("map-btn-agency")?.addEventListener("click", centerOnAgency);
    document.getElementById("map-btn-refresh")?.addEventListener("click", () => enter(true));
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

  window.VelioraMap = { enter, locateUser, refresh: () => enter(true), resize };
})();
