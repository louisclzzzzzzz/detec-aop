"""
Configuration globale du pipeline DCE.
"""

import os
from pathlib import Path

# ─── Chemins ─────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
CHECKLIST_PATH = BASE_DIR / "checklist.json"
OUTPUT_DIR = BASE_DIR / "output"

# ─── API Mistral (classification LLM uniquement) ────────────────────
def load_api_key() -> str:
    """Charge la clé API depuis la variable d'environnement MISTRAL_API_KEY."""
    key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if not key:
        raise EnvironmentError(
            "Variable d'environnement MISTRAL_API_KEY absente ou vide.\n"
            "Exportez-la avant de lancer le pipeline :\n"
            "  export MISTRAL_API_KEY=votre_clé_mistral"
        )
    return key

MISTRAL_CHAT_MODEL = "mistral-small-latest"

# ─── Parallélisation ────────────────────────────────────────────────
# Nombre de workers pour l'extraction et la classification parallèles
MAX_WORKERS = 4

# ─── Seuils de classification ───────────────────────────────────────
# Seuil min de confiance pour accepter un match par mots-clés
KEYWORD_CONFIDENCE_THRESHOLD = 0.55

# Seuil min pour retourner immédiatement sans consulter le pass suivant
CLASSIFICATION_MIN_CONFIDENCE = 0.75

# Si la confiance du meilleur candidat atteint ce seuil avant la passe LLM,
# on saute le LLM (économie d'appels API)
LLM_SKIP_THRESHOLD = 0.80

# Seuil min de confiance pour accepter une classification LLM
LLM_CONFIDENCE_THRESHOLD = 0.6

# Nombre max de caractères envoyés au LLM pour classification
LLM_MAX_CONTEXT_CHARS = 4000

# ─── OCR Tesseract ──────────────────────────────────────────────────
# Seuil min de caractères par page pour considérer un PDF comme "natif"
SCANNED_TEXT_THRESHOLD = 50

# Langue Tesseract (français)
TESSERACT_LANG = "fra"

# DPI pour la conversion PDF → image (pour Tesseract)
TESSERACT_DPI = 300

# Nombre maximum de pages à scanner avec OCR (pour des raisons de performance)
OCR_MAX_PAGES = 2
