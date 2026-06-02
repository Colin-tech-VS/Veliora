"""Paramètres de l'assistant IA (Ollama local)."""

from __future__ import annotations

import os

# URL Ollama : par défaut le démon local (`ollama serve`). Configurable via env
# OLLAMA_BASE_URL pour pointer vers un autre hôte (LAN, GPU mutualisé…).
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")

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
