"""Paramètres de l'assistant IA — multi-providers (Ollama + APIs hébergées)."""

from __future__ import annotations

import os

# === Sélection du fournisseur d'IA ===========================================
# Valeurs supportées : groq (défaut), ollama, openai,
# mistral, openrouter, anthropic. Pour les APIs hébergées, il suffit d'ajouter
# AI_API_KEY=<clé> — le reste est auto-configuré.
AI_PROVIDER = (os.getenv("AI_PROVIDER") or "groq").strip().lower()

# Clé d'API du fournisseur hébergé choisi (groq, openai, mistral, openrouter…).
# Inutile pour Ollama (qui utilise OLLAMA_API_KEY si reverse-proxy auth).
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()

# Modèle préféré du fournisseur (laisse vide pour le défaut sain de chaque provider).
AI_MODEL = os.getenv("AI_MODEL", "").strip()

# Override manuel de la base URL (utile pour OpenRouter custom, proxies privés, etc.).
AI_BASE_URL = os.getenv("AI_BASE_URL", "").strip().rstrip("/")

# === Ollama ==================================================================
# URL Ollama : par défaut le démon local (`ollama serve`).
# En prod : pointer vers ton VPS Ollama (ex. `https://ollama.veliora.fr`).
# Configurable via env OLLAMA_BASE_URL.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")

# Clé d'API protégeant le démon Ollama exposé en HTTPS (cf. infra/ollama/Caddyfile).
# Vide en local — obligatoire en prod si tu ne veux pas que tout internet
# puisse appeler ton GPU à ta place.
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "").strip()

# Modèle par défaut — bon compromis vitesse / qualité / tool-use sur CPU récent.
# Remplaçable via env (`OLLAMA_MODEL=llama3.1:8b` par ex.).
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")

# Modèle de fallback si le principal n'est pas disponible (plus petit, ultra-rapide).
OLLAMA_FALLBACK_MODEL = os.getenv("OLLAMA_FALLBACK_MODEL", "llama3.2:3b")

# Limites de génération — éviter les réponses fleuves qui ralentissent l'UI.
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "900"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.4"))
OLLAMA_CONTEXT_TOKENS = int(os.getenv("OLLAMA_CONTEXT_TOKENS", "8192"))

# Durée d'attente max sur le streaming (sécurité — coupe la requête si bloquée).
OLLAMA_STREAM_TIMEOUT = int(os.getenv("OLLAMA_STREAM_TIMEOUT", "180"))

# Combien de messages d'historique on renvoie au modèle à chaque tour.
# Trop court = l'IA oublie le fil ; trop long = lent. 16 ≈ ~8 échanges.
MAX_HISTORY_MESSAGES = int(os.getenv("AI_MAX_HISTORY", "16"))

# Combien d'activités récentes on injecte dans le contexte.
RECENT_ACTIVITY_LIMIT = int(os.getenv("AI_RECENT_ACTIVITY", "25"))

# Top N annonces et clients résumés dans le contexte système.
TOP_LEADS_IN_CONTEXT = int(os.getenv("AI_TOP_LEADS", "15"))
TOP_CLIENTS_IN_CONTEXT = int(os.getenv("AI_TOP_CLIENTS", "15"))
