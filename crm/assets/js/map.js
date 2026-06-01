/**
 * Carte Google Maps — géolocalisation, agence, annonces CRM.
 */
(function () {
  const state = {
    map: null,
    data: null,
    userMarker: null,
    agencyMarker: null,
    leadMarkers: [],
    infoWindow: null,
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
      const existing = document.getElementById("veliora-gmaps-script");
      if (existing) {
        existing.addEventListener("load", () => resolve());
        existing.addEventListener("error", () => reject(new Error("Google Maps")));
        return;
      }
      const s = document.createElement("script");
      s.id = "veliora-gmaps-script";
      s.async = true;
      s.defer = true;
      s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(apiKey)}&language=fr&region=FR&callback=${cb}`;
      s.onerror = () => reject(new Error("Impossible de charger Google Maps"));
      document.head.appendChild(s);
    });
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
        pending.textContent = `${stats.pending_geocode} en cours de placement (rechargez dans un instant)`;
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

  function markerIcon(kind) {
    const colors = { agency: "#1e3a5f", lead: "#c45c26", user: "#2563eb" };
    const fill = colors[kind] || colors.lead;
    return {
      path: google.maps.SymbolPath.CIRCLE,
      fillColor: fill,
      fillOpacity: 1,
      strokeColor: "#ffffff",
      strokeWeight: 2,
      scale: kind === "agency" ? 11 : kind === "user" ? 9 : 7,
    };
  }

  function clearLeadMarkers() {
    state.leadMarkers.forEach((m) => m.setMap(null));
    state.leadMarkers = [];
  }

  function filteredMarkers() {
    const markers = state.data?.markers || [];
    if (!state.aroundMe || !state.userPos) return markers;
    return markers.filter(
      (m) => haversineKm(state.userPos.lat, state.userPos.lng, m.lat, m.lng) <= state.aroundKm
    );
  }

  function renderLeadMarkers() {
    const { escapeHtml: esc, formatPrice, openDrawer } = deps();
    if (!state.map || !window.google?.maps) return;
    clearLeadMarkers();
    const list = filteredMarkers();
    list.forEach((m) => {
      const marker = new google.maps.Marker({
        map: state.map,
        position: { lat: m.lat, lng: m.lng },
        title: m.title,
        icon: markerIcon("lead"),
      });
      marker.addListener("click", () => {
        const html = `
          <div class="map-infowindow">
            <strong>${esc(m.title)}</strong>
            <p class="map-infowindow-addr">${esc(m.address || "")}</p>
            <p class="map-infowindow-meta">${esc(typeof formatPrice === "function" ? formatPrice(m) : `${Number(m.price || 0).toLocaleString("fr-FR")} €`)} · Score ${m.mandate_score || m.score || 0} · ${esc(pipelineLabel(m.pipeline))}</p>
            <button type="button" class="btn btn-primary btn-sm map-infowindow-btn" data-lead-id="${m.id}">Ouvrir la fiche</button>
          </div>`;
        state.infoWindow.setContent(html);
        state.infoWindow.open({ anchor: marker, map: state.map });
        setTimeout(() => {
          document.querySelector(".map-infowindow-btn")?.addEventListener("click", () => {
            state.infoWindow.close();
            if (openDrawer) openDrawer(m.id);
          });
        }, 0);
      });
      state.leadMarkers.push(marker);
    });
    fitMapBounds();
  }

  function fitMapBounds() {
    if (!state.map || !window.google?.maps) return;
    const bounds = new google.maps.LatLngBounds();
    let n = 0;
    if (state.agencyMarker) {
      const p = state.agencyMarker.getPosition();
      if (p) {
        bounds.extend(p);
        n++;
      }
    }
    if (state.userMarker && state.userPos) {
      bounds.extend(state.userPos);
      n++;
    }
    filteredMarkers().forEach((m) => {
      bounds.extend({ lat: m.lat, lng: m.lng });
      n++;
    });
    if (n === 0) {
      state.map.setCenter({ lat: 46.6, lng: 2.4 });
      state.map.setZoom(6);
      return;
    }
    if (n === 1) {
      state.map.setCenter(bounds.getCenter());
      state.map.setZoom(14);
      return;
    }
    state.map.fitBounds(bounds, { top: 80, right: 40, bottom: 40, left: 40 });
  }

  function placeAgencyMarker() {
    const ag = state.data?.agency;
    if (state.agencyMarker) {
      state.agencyMarker.setMap(null);
      state.agencyMarker = null;
    }
    if (!ag?.lat || !ag?.lng || !state.map) return;
    state.agencyMarker = new google.maps.Marker({
      map: state.map,
      position: { lat: ag.lat, lng: ag.lng },
      title: ag.name || "Agence",
      icon: markerIcon("agency"),
      zIndex: 1000,
    });
    const { escapeHtml: esc } = deps();
    state.agencyMarker.addListener("click", () => {
      state.infoWindow.setContent(
        `<div class="map-infowindow"><strong>${esc(ag.name || "Agence")}</strong><p>${esc(ag.address_line || "")}</p></div>`
      );
      state.infoWindow.open({ anchor: state.agencyMarker, map: state.map });
    });
  }

  function initMapInstance() {
    const el = document.getElementById("map-canvas");
    if (!el || state.map) return;
    state.map = new google.maps.Map(el, {
      center: { lat: 46.6, lng: 2.4 },
      zoom: 6,
      mapTypeControl: false,
      streetViewControl: false,
      fullscreenControl: true,
      gestureHandling: "greedy",
    });
    state.infoWindow = new google.maps.InfoWindow();
    placeAgencyMarker();
    renderLeadMarkers();
    state.mapsReady = true;
  }

  function locateUser() {
    const { showToast } = deps();
    if (!navigator.geolocation) {
      showToast("Géolocalisation non disponible sur cet appareil", "warning");
      return;
    }
    showToast("Localisation en cours…", "info");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        state.userPos = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        if (!state.map) return;
        if (state.userMarker) state.userMarker.setMap(null);
        state.userMarker = new google.maps.Marker({
          map: state.map,
          position: state.userPos,
          title: "Vous",
          icon: markerIcon("user"),
          zIndex: 999,
        });
        state.map.panTo(state.userPos);
        if (state.map.getZoom() < 13) state.map.setZoom(13);
        renderLeadMarkers();
        showToast("Position affichée sur la carte", "success");
      },
      (err) => {
        const msg =
          err.code === 1
            ? "Autorisez la géolocalisation dans les réglages du navigateur"
            : "Impossible d'obtenir votre position";
        showToast(msg, "warning");
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
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
    state.map?.panTo({ lat: ag.lat, lng: ag.lng });
    if (state.map && state.map.getZoom() < 14) state.map.setZoom(14);
  }

  async function fetchMapData() {
    const { api: apiFn, showToast } = deps();
    if (!apiFn) throw new Error("API indisponible");
    const data = await apiFn("/map");
    if (data.error) throw new Error(data.error);
    return data;
  }

  function showSetupPanel(data) {
    const panel = document.getElementById("map-setup-panel");
    const canvas = document.getElementById("map-canvas-wrap");
    if (!panel) return;
    const hints = data?.hints || {};
    let msg = "";
    if (hints.no_api_key) {
      msg =
        "<p><strong>Clé Google Maps manquante</strong> — ajoutez <code>GOOGLE_MAPS_API_KEY</code> sur le serveur (Maps JavaScript API + Geocoding API).</p>";
    } else if (hints.no_agency_address) {
      msg =
        "<p><strong>Adresse agence</strong> — complétez la fiche agence pour afficher votre bureau sur la carte.</p><button type=\"button\" class=\"btn btn-secondary btn-sm\" id=\"map-setup-agency\">Ouvrir la fiche agence</button>";
    }
    if (msg) {
      panel.innerHTML = msg;
      panel.hidden = false;
      if (canvas) canvas.classList.add("has-setup-banner");
      document.getElementById("map-setup-agency")?.addEventListener("click", () => {
        document.getElementById("btn-agency-legal-profile")?.click();
      });
    } else {
      panel.hidden = true;
      panel.innerHTML = "";
      if (canvas) canvas.classList.remove("has-setup-banner");
    }
  }

  async function enter() {
    if (state.loading) return;
    state.loading = true;
    setStatus("Chargement de la carte…");
    try {
      const data = await fetchMapData();
      state.data = data;
      setLegend(data.stats);
      showSetupPanel(data);

      if (!data.maps_api_key) {
        setStatus("Configurez Google Maps pour afficher la carte interactive.");
        state.loading = false;
        return;
      }

      await loadGoogleMaps(data.maps_api_key);
      initMapInstance();
      if (!state.mapsReady) initMapInstance();
      placeAgencyMarker();
      renderLeadMarkers();
      showSetupPanel(data);

      const ag = data.agency;
      if (ag?.address_line) {
        setStatus(
          `Agence : ${ag.address_line} · ${data.stats?.on_map || 0} annonce(s) géolocalisée(s)`
        );
      } else {
        setStatus(`${data.stats?.on_map || 0} annonce(s) sur la carte`);
      }

      if (!state.userPos && navigator.geolocation) {
        locateUser();
      }
    } catch (err) {
      deps().showToast(err.message || "Carte indisponible", "error");
      setStatus("Erreur de chargement");
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
      const n = filteredMarkers().length;
      if (state.aroundMe && state.userPos) {
        setStatus(`${n} annonce(s) dans un rayon de ${state.aroundKm} km autour de vous`);
      }
    });
  }

  wireControls();

  function resize() {
    if (!state.map || !window.google?.maps) return;
    google.maps.event.trigger(state.map, "resize");
    fitMapBounds();
  }

  window.VelioraMap = { enter, locateUser, refresh: enter, resize };
})();
