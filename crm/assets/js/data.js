let LEADS = [];
let ACTIVITIES = [];
let SOURCES = [];
let SOURCE_STATS = [];
let RADAR = null;
let PLAYBOOK = null;

const AVATAR_COLORS = [
  "#3B82F6", "#2563EB", "#D6B25E", "#0B1220", "#60A5FA",
  "#8b5cf6", "#10b981", "#f43f5e", "#6366f1", "#ec4899",
];

function formatPublishedDate(lead) {
  const raw = lead?.published_at || lead?.listedAt;
  if (!raw) return null;
  const d = new Date(raw.length === 10 ? `${raw}T12:00:00` : raw);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
}

/** Libellé « Publié … » — date portail uniquement (pas la date de crawl). */
function formatPublishedLine(lead) {
  const d = formatPublishedDate(lead);
  return d ? `Publié ${d}` : "Publication inconnue";
}

function formatDetectedDate(lead) {
  const raw = lead?.detected_at || lead?.created_at;
  if (!raw) return null;
  const d = new Date(raw.length === 10 ? `${raw}T12:00:00` : raw);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
}

function formatPrice(priceOrLead, transactionType, pricePeriod) {
  let price = priceOrLead;
  let tx = transactionType;
  let period = pricePeriod;
  if (priceOrLead && typeof priceOrLead === "object") {
    price = priceOrLead.price;
    tx = priceOrLead.transaction_type || tx;
    period = priceOrLead.price_period || period;
  }
  if (!price) return "—";
  const formatted = new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  }).format(price);
  if (tx === "location" || period === "month") {
    return `${formatted} /mois`;
  }
  return formatted;
}

function getTransactionBadge(lead) {
  const tx = lead.transaction_type || "vente";
  if (tx === "location") {
    return `<span class="badge badge-location">Location</span>`;
  }
  return `<span class="badge badge-vente">Vente</span>`;
}

function getInitials(name) {
  if (!name || name === "—") return "?";
  return name.split(" ").map((n) => n[0]).join("").slice(0, 2).toUpperCase();
}

function getAvatarColor(id) {
  return AVATAR_COLORS[id % AVATAR_COLORS.length];
}

function getScoreClass(score) {
  if (score >= 85) return "high";
  if (score >= 70) return "medium";
  return "low";
}

function getTypeBadge(lead) {
  if (lead.type === "agence") {
    return `<span class="badge badge-agence">Avec agence</span>`;
  }
  return `<span class="badge badge-sans-agence">Sans agence</span>`;
}

function getPublisherLabel(lead) {
  if (lead.type === "agence" && lead.agency) {
    return lead.agency;
  }
  return lead.type === "agence" ? "Professionnel / agence" : "Particulier";
}

function getStatusBadge(status) {
  const labels = {
    nouveau: "Nouveau",
    contacte: "Contacté",
    rdv: "RDV",
    mandat: "Mandat",
    perdu: "Perdu",
    retire: "Retiré",
  };
  return `<span class="badge badge-status ${status}">${labels[status] || status}</span>`;
}

function getMandateScoreClass(score) {
  if (score >= 85) return "high";
  if (score >= 65) return "medium";
  return "low";
}

function isUrl(value) {
  return /^https?:\/\//i.test(value) || /^[\w.-]+\.(fr|com|net|org)\//i.test(value) || /^www\./i.test(value);
}
