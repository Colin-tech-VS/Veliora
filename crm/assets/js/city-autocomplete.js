/**
 * Autocomplete communes françaises (34k+) via /api/geo/communes
 */
(function (global) {
  function escAttr(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
  }

  function apiBase() {
    if (global.getApiBase) return global.getApiBase();
    const { protocol, hostname, port } = window.location;
    if (protocol === "file:") return "http://127.0.0.1:8000/api";
    const devPorts = new Set(["5500", "5501", "5173", "3000", "8080", "4173"]);
    const isLocal = hostname === "localhost" || hostname === "127.0.0.1";
    if (isLocal && devPorts.has(port)) return `http://${hostname}:8000/api`;
    if (isLocal && port && port !== "8000") return `http://${hostname}:8000/api`;
    return "/api";
  }

  function setupFrenchCityAutocomplete(input, options = {}) {
    if (!input || input.dataset.cityAc === "1") return;
    input.dataset.cityAc = "1";
    const multi = Boolean(options.multi);
    const listId = `city-ac-${input.id || "field"}`;
    let datalist = document.getElementById(listId);
    if (!datalist) {
      datalist = document.createElement("datalist");
      datalist.id = listId;
      document.body.appendChild(datalist);
    }
    input.setAttribute("list", listId);
    input.setAttribute("autocomplete", "off");
    let timer = null;

    input.addEventListener("input", () => {
      clearTimeout(timer);
      const raw = input.value || "";
      const query = multi
        ? raw.split(",").pop().trim()
        : raw.trim();
      if (query.length < 2) {
        datalist.innerHTML = "";
        return;
      }
      timer = setTimeout(async () => {
        try {
          const res = await fetch(
            `${apiBase()}/geo/communes?q=${encodeURIComponent(query)}&limit=25`,
          );
          const data = await res.json();
          if (!res.ok || !data.communes) {
            datalist.innerHTML = "";
            return;
          }
          datalist.innerHTML = data.communes
            .map(
              (c) =>
                `<option value="${escAttr(c.name)}">${escAttr(c.label)}</option>`,
            )
            .join("");
        } catch {
          datalist.innerHTML = "";
        }
      }, 220);
    });
  }

  global.setupFrenchCityAutocomplete = setupFrenchCityAutocomplete;
})(window);
