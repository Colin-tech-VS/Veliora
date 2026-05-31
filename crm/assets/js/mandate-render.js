/* Rendu mandat côté client — aperçu instantané (miroir de crm/mandates/templates.py) */

(function (global) {
  function fmtPrice(val) {
    if (val === null || val === undefined || val === "") return "—";
    const n = parseFloat(String(val).replace(/\s/g, "").replace(",", "."));
    if (Number.isNaN(n)) return String(val);
    return Math.round(n).toLocaleString("fr-FR");
  }

  function fmtVal(val) {
    if (val === null || val === undefined || val === "") return "—";
    return String(val);
  }

  function exclLabel(exclusivity) {
    return (
      { exclusif: "EXCLUSIF", simple: "SIMPLE", "semi-exclusif": "SEMI-EXCLUSIF" }[
        exclusivity
      ] || String(exclusivity || "").toUpperCase()
    );
  }

  function exclText(exclusivity) {
    return (
      { exclusif: "exclusif", simple: "simple", "semi-exclusif": "semi-exclusif" }[
        exclusivity
      ] || exclusivity
    );
  }

  function renderLi(label, val) {
    const v = fmtVal(val);
    if (v === "—") return "";
    return `<li><strong>${label} :</strong> ${escapeHtml(v)}</li>`;
  }

  function renderUl(items) {
    const filtered = items.filter(Boolean);
    if (!filtered.length) return "";
    return `<ul>${filtered.join("")}</ul>`;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function defaultAgency() {
    return {
      legal_name: "",
      brand_name: "",
      address: "",
      postal_code: "",
      city: "",
      siret: "",
      rcs: "",
      capital: "",
      tva_intra: "",
      professional_card: "",
      insurance_company: "",
      insurance_policy: "",
      representative_name: "",
      representative_title: "Gérant",
      phone: "",
      email: "",
      website: "",
    };
  }

  function exclClauseVente(exclusivity) {
    if (exclusivity === "simple") {
      return `<p>Le mandant confie au mandataire un mandat <strong>simple</strong> : il conserve la faculté de vendre le bien par lui-même ou par l'intermédiaire d'un autre professionnel, sans commission due au mandataire dans ce cas. Si la vente est réalisée par le mandataire, les honoraires prévus au présent mandat restent dus.</p>`;
    }
    if (exclusivity === "semi-exclusif") {
      return `<p>Le mandant confie au mandataire un mandat <strong>semi-exclusif</strong> : en cas de vente conclue avec un acquéreur présenté par le mandataire ou ayant visité le bien par son intermédiaire, les honoraires sont dus intégralement. En cas de vente réalisée par le mandant seul, sans intervention du mandataire, les honoraires ne sont pas dus.</p>`;
    }
    return `<p>Le mandant confie au mandataire un mandat <strong>exclusif</strong> : il s'interdit de confier la vente du bien à un autre professionnel et de le vendre directement pendant la durée du mandat. Toute vente réalisée pendant cette période, même par le mandant seul, entraîne le paiement des honoraires au mandataire.</p>`;
  }

  function exclClauseLocation(exclusivity) {
    if (exclusivity === "simple") {
      return `<p>Le mandant confie au mandataire un mandat <strong>simple</strong> de location : il peut rechercher un locataire par ses propres moyens. Les honoraires ne sont dus que si le bail est signé avec un locataire présenté par le mandataire.</p>`;
    }
    if (exclusivity === "semi-exclusif") {
      return `<p>Le mandant confie au mandataire un mandat <strong>semi-exclusif</strong> : les honoraires sont dus si le locataire a visité le bien par l'intermédiaire du mandataire, même si le bail est signé directement avec le mandant.</p>`;
    }
    return `<p>Le mandant confie au mandataire un mandat <strong>exclusif</strong> de location : il s'interdit de confier la recherche de locataire à un autre professionnel pendant la durée du mandat.</p>`;
  }

  function renderVente(f, a, exclusivity) {
    const agName = a.legal_name || a.brand_name || "[Nom de l'agence]";
    const agAddr = [a.address, `${a.postal_code || ""} ${a.city || ""}`.trim()]
      .filter(Boolean)
      .join(", ");
    const rep = a.representative_name || "[Représentant]";
    const card = a.professional_card || "[Carte professionnelle]";
    const seller = `${f.seller_civility || "M."} ${f.seller_first_name || ""} ${f.seller_last_name || ""}`.trim();
    const addrFull = [f.property_address, `${f.postal_code || ""} ${f.city || ""}`.trim()]
      .filter(Boolean)
      .join(", ");

    let mandateDates = "";
    if (f.mandate_start_date || f.mandate_end_date) {
      mandateDates = ` du <strong>${fmtVal(f.mandate_start_date)}</strong> au <strong>${fmtVal(f.mandate_end_date)}</strong>`;
    }

    const identification = renderUl([
      renderLi("Adresse", addrFull || f.property_address),
      renderLi("Quartier", f.neighborhood),
      renderLi("Type", f.property_type),
      renderLi("Surface habitable", f.surface_carrez ? `${f.surface_carrez} m²` : null),
      renderLi("Pièces", f.rooms),
      renderLi("Chambres", f.bedrooms),
      renderLi("Étage", f.floor),
      renderLi("Ascenseur", f.has_elevator),
      renderLi("Année construction", f.construction_year),
    ]);

    const prix = renderUl([
      renderLi("Prix FAI", f.price_fai ? `${fmtPrice(f.price_fai)} €` : null),
      renderLi("Prix net vendeur", f.price_net_seller ? `${fmtPrice(f.price_net_seller)} €` : null),
      renderLi("Prix HAI", f.price_hai ? `${fmtPrice(f.price_hai)} €` : null),
      renderLi("Négociation", f.negotiable),
      renderLi("Estimation marché", f.market_estimate ? `${fmtPrice(f.market_estimate)} €` : null),
    ]);

    const technique = renderUl([
      renderLi("DPE énergie", f.dpe_energy),
      renderLi("DPE GES", f.dpe_ges),
      renderLi("Chauffage", f.heating_type),
      renderLi("Cuisine", f.kitchen_type),
      renderLi("État", f.general_condition),
      renderLi("Charges mensuelles", f.monthly_charges ? `${fmtPrice(f.monthly_charges)} €` : null),
      renderLi("Taxe foncière", f.property_tax ? `${fmtPrice(f.property_tax)} €` : null),
    ]);

    const legal = renderUl([
      renderLi("Diagnostics", f.diagnostics_ok),
      renderLi("Titre de propriété", f.clear_title),
      renderLi("Copropriété", f.is_copro),
      renderLi("Servitudes", f.easements),
      renderLi("Procédure copro", f.copro_procedure),
    ]);

    const honAmount = f.honoraires_amount ? ` (soit ${fmtPrice(f.honoraires_amount)} €)` : "";

    return `
<div class="mandate-doc">
  <h1>MANDAT DE VENTE ${exclLabel(exclusivity)}</h1>
  <p class="mandate-meta">Document généré par Veliora — à faire signer par les parties</p>

  <h2>1. Le mandant (vendeur)</h2>
  <p><strong>${escapeHtml(seller || "—")}</strong><br>
  Statut : ${escapeHtml(fmtVal(f.owner_legal_status))} · Propriétaires : ${escapeHtml(fmtVal(f.owner_count || "1"))}<br>
  Demeurant : ${escapeHtml(fmtVal(f.seller_address))}<br>
  Email : ${escapeHtml(fmtVal(f.seller_email))} · Tél. : ${escapeHtml(fmtVal(f.seller_phone))}</p>

  <h2>2. Le mandataire (agence)</h2>
  <p><strong>${escapeHtml(agName)}</strong><br>
  ${escapeHtml(agAddr)}<br>
  SIRET : ${escapeHtml(fmtVal(a.siret))} · RCS : ${escapeHtml(fmtVal(a.rcs))}<br>
  Carte professionnelle : ${escapeHtml(card)}<br>
  Représentée par : ${escapeHtml(rep)}, ${escapeHtml(a.representative_title || "Gérant")}</p>

  <h2>3. Objet du mandat — Identification du bien</h2>
  <p>Le mandant confie au mandataire, qui l'accepte, la mission de rechercher un acquéreur pour le bien suivant :</p>
  ${identification || "<ul><li>—</li></ul>"}
  ${prix}

  <h2>4. Exclusivité et durée</h2>
  ${exclClauseVente(exclusivity)}
  <p>Mandat <strong>${exclText(exclusivity)}</strong> d'une durée de <strong>${escapeHtml(f.mandate_duration_months || "3")} mois</strong>${mandateDates},
  renouvelable par tacite reconduction pour des périodes successives de même durée, sauf dénonciation
  avec préavis d'un mois par lettre recommandée.</p>

  <h2>5. Honoraires</h2>
  <p>En cas de réalisation de l'opération, le mandant s'engage à verser au mandataire des honoraires de
  <strong>${escapeHtml(f.honoraires_pct || "5")} % TTC</strong>${honAmount}
  du prix de vente, à la charge du <strong>${escapeHtml(f.honoraires_charge || "Vendeur")}</strong>,
  exigibles à la signature de l'acte authentique de vente.</p>

  <h2>6. Caractéristiques et données légales</h2>
  ${technique}
  ${legal}

  <h2>7. Clauses particulières</h2>
  <p>${escapeHtml(f.clauses || "Néant.")}</p>

  <h2>8. Protection des données</h2>
  <p>Les données personnelles sont traitées conformément au RGPD pour les besoins de la commercialisation du bien.</p>

  <div class="mandate-signatures">
    <p>Fait à ${escapeHtml(a.city || "………………")}, le ……………………</p>
    <div class="sig-grid">
      <div><p><strong>Le mandant</strong><br><br><br>_____________________</p></div>
      <div><p><strong>Le mandataire</strong><br><br><br>_____________________</p></div>
    </div>
  </div>
</div>`;
  }

  function renderLocation(f, a, exclusivity) {
    const agName = a.legal_name || a.brand_name || "[Nom de l'agence]";
    const agAddr = [a.address, `${a.postal_code || ""} ${a.city || ""}`.trim()]
      .filter(Boolean)
      .join(", ");
    const rep = a.representative_name || "[Représentant]";
    const card = a.professional_card || "[Carte professionnelle]";
    const owner = `${f.owner_civility || "M."} ${f.owner_first_name || ""} ${f.owner_last_name || ""}`.trim();
    const addrFull = [f.property_address, `${f.postal_code || ""} ${f.city || ""}`.trim()]
      .filter(Boolean)
      .join(", ");

    let mandateDates = "";
    if (f.mandate_start_date || f.mandate_end_date) {
      mandateDates = ` du <strong>${fmtVal(f.mandate_start_date)}</strong> au <strong>${fmtVal(f.mandate_end_date)}</strong>`;
    }

    const identification = renderUl([
      renderLi("Adresse", addrFull || f.property_address),
      renderLi("Quartier", f.neighborhood),
      renderLi("Type", f.property_type),
      renderLi("Surface", f.surface ? `${f.surface} m²` : null),
      renderLi("Pièces", f.rooms),
      renderLi("Étage", f.floor),
      renderLi("Ascenseur", f.has_elevator),
      renderLi("Meublé", f.furnished),
    ]);

    const loyer = renderUl([
      renderLi("Loyer HC", f.rent_hc ? `${fmtPrice(f.rent_hc)} € / mois` : null),
      renderLi("Charges", f.charges ? `${fmtPrice(f.charges)} € / mois` : null),
      renderLi("Loyer CC", f.rent_cc ? `${fmtPrice(f.rent_cc)} € / mois` : null),
      renderLi("Dépôt de garantie", f.deposit ? `${fmtPrice(f.deposit)} €` : null),
      renderLi("Encadrement loyers", f.rent_control_zone),
    ]);

    const caracteristiques = renderUl([
      renderLi("DPE", f.dpe_energy),
      renderLi("Chauffage", f.heating_type),
      renderLi("État", f.general_condition),
      renderLi("Cuisine équipée", f.equipped_kitchen),
      renderLi("Mobilier", f.furniture_level),
      renderLi("Internet / fibre", f.internet_fiber),
    ]);

    return `
<div class="mandate-doc">
  <h1>MANDAT DE LOCATION ${exclLabel(exclusivity)}</h1>
  <p class="mandate-meta">Document généré par Veliora — à faire signer par les parties</p>

  <h2>1. Le mandant (bailleur)</h2>
  <p><strong>${escapeHtml(owner || "—")}</strong><br>
  Type : ${escapeHtml(fmtVal(f.owner_type))}<br>
  Demeurant : ${escapeHtml(fmtVal(f.owner_address))}<br>
  Email : ${escapeHtml(fmtVal(f.owner_email))} · Tél. : ${escapeHtml(fmtVal(f.owner_phone))}<br>
  Disponibilité visites : ${escapeHtml(fmtVal(f.visit_availability))}</p>

  <h2>2. Le mandataire (agence)</h2>
  <p><strong>${escapeHtml(agName)}</strong><br>
  ${escapeHtml(agAddr)}<br>
  SIRET : ${escapeHtml(fmtVal(a.siret))} · RCS : ${escapeHtml(fmtVal(a.rcs))}<br>
  Carte professionnelle : ${escapeHtml(card)}<br>
  Représentée par : ${escapeHtml(rep)}, ${escapeHtml(a.representative_title || "Gérant")}</p>

  <h2>3. Objet du mandat — Identification du bien</h2>
  <p>Le mandant confie au mandataire la mission de rechercher un locataire pour le bien suivant :</p>
  ${identification || "<ul><li>—</li></ul>"}
  ${loyer}

  <h2>4. Exclusivité et durée</h2>
  ${exclClauseLocation(exclusivity)}
  <p>Mandat <strong>${exclText(exclusivity)}</strong> pour une durée de <strong>${escapeHtml(f.mandate_duration_months || "3")} mois</strong>${mandateDates}.</p>

  <h2>5. Honoraires</h2>
  <p>Honoraires de location d'un montant de <strong>${fmtPrice(f.honoraires_location)} € TTC</strong>,
  à la charge du <strong>${escapeHtml(f.fee_paid_by || "Locataire")}</strong>,
  dus à la signature du bail par le locataire présenté par le mandataire.</p>

  <h2>6. Caractéristiques du bien</h2>
  ${caracteristiques || "<p>—</p>"}

  <h2>7. Clauses particulières</h2>
  <p>${escapeHtml(f.clauses || "Néant.")}</p>

  <div class="mandate-signatures">
    <p>Fait à ${escapeHtml(a.city || "………………")}, le ……………………</p>
    <div class="sig-grid">
      <div><p><strong>Le mandant</strong><br><br><br>_____________________</p></div>
      <div><p><strong>Le mandataire</strong><br><br><br>_____________________</p></div>
    </div>
  </div>
</div>`;
  }

  function renderMandateHtml(mandateType, exclusivity, fields, agency) {
    const a = { ...defaultAgency(), ...(agency || {}) };
    const f = fields || {};
    if (mandateType === "location") {
      return renderLocation(f, a, exclusivity || "exclusif");
    }
    return renderVente(f, a, exclusivity || "exclusif");
  }

  global.MandateRender = {
    renderMandateHtml,
    defaultAgency,
  };
})(typeof window !== "undefined" ? window : globalThis);
