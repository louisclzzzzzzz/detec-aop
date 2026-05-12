# Pipeline d'Analyse DCE — Polices de Chantier SMABTP

Analyse automatisée d'un Dossier de Consultation des Entreprises (DCE) : extraction de texte, classification hybride (mots-clés + LLM Mistral) et vérification des 18 pièces obligatoires de la checklist Police de Chantier SMABTP.

---

## Prérequis système

| Outil | Version | Installation |
|---|---|---|
| Python | 3.10 + | [python.org](https://python.org) |
| Tesseract OCR | 5.x + (avec pack `fra`) | `brew install tesseract tesseract-lang` (macOS) |
| Clé API Mistral | — | Requise pour la classification LLM |

### Installation de Tesseract OCR

*   **macOS :** `brew install tesseract tesseract-lang`
*   **Linux :** `sudo apt-get install tesseract-ocr tesseract-ocr-fra`
*   **Windows :** 
    1. Téléchargez l'installeur depuis [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki).
    2. Lors de l'installation, assurez-vous de cocher **"Additional script data"** et **"Additional language data"** -> **"French"**.
    3. **Important :** Ajoutez le chemin d'installation (ex: `C:\Program Files\Tesseract-OCR`) à votre variable d'environnement `PATH` système, ou modifiez `config.py` pour pointer vers l'exécutable.

---

## Installation du projet

### Sur macOS / Linux
```bash
# 1. Se placer dans le dossier du projet
cd detection_aop/

# 2. Créer et activer un environnement virtuel
python3 -m venv .venv
source .venv/bin/activate

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Configurer la clé API Mistral
export MISTRAL_API_KEY=votre_clé_mistral
```

### Sur Windows
```powershell
# 1. Se placer dans le dossier du projet
cd detection_aop/

# 2. Créer et activer l'environnement virtuel
python -m venv .venv
.\.venv\Scripts\activate

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Configurer la clé API Mistral
$env:MISTRAL_API_KEY="votre_clé_mistral"
```

---

## Interface graphique (Streamlit)

```bash
streamlit run gui.py
```

Ouvre **http://localhost:8501** dans votre navigateur.

**Fonctionnalités de l'interface :**
- Déposer une archive ZIP ou indiquer un chemin de dossier local
- Activer / désactiver la classification LLM depuis la barre latérale
- Suivi en direct des 6 étapes du pipeline avec journal de traitement
- Rapport final interactif : métriques, tableaux par phase, pièces manquantes
- Téléchargement du rapport en Markdown et JSON

---

## Ligne de commande (CLI)

```bash
# Analyse basique
python main.py ./DCE/dce1/

# Depuis une archive ZIP
python main.py ./DCE/dce1.zip

# Sans LLM (mots-clés uniquement — rapide, sans appel API)
python main.py ./DCE/dce1/ --no-llm

# Dossier de sortie personnalisé
python main.py ./DCE/dce1/ --output ./rapports/

# Mode verbeux
python main.py ./DCE/dce1/ --verbose

# Checklist personnalisée
python main.py ./DCE/dce1/ --checklist ./ma_checklist.json
```

**Codes de retour :** `0` = dossier complet · `2` = pièces manquantes · `1` = erreur

---

## Architecture

```
detection_aop/
├── gui.py               # Interface Streamlit
├── main.py              # Point d'entrée CLI
├── config.py            # Seuils de classification, chemins, paramètres Mistral
├── checklist.json       # 18 pièces attendues en 3 phases
├── requirements.txt     # Dépendances Python
├── models/
│   └── schemas.py       # Modèles de données (ChecklistItem, ExtractedDocument, …)
├── utils/
│   ├── file_utils.py    # Gestion des ZIP, listing récursif
│   └── pdf_utils.py     # Détection PDF natif vs scanné
└── pipeline/
    ├── extractor.py     # Extraction texte (PyMuPDF / Tesseract / docx / xlsx)
    ├── classifier.py    # Classification : dossier → nom fichier → mots-clés → LLM
    ├── checker.py       # Croisement classifications × checklist
    └── reporter.py      # Génération rapports JSON + Markdown + tableau Rich
```

### Pipeline en 6 étapes

1. **Checklist** — chargement des 18 pièces depuis `checklist.json`
2. **Documents** — découverte récursive (PDF, DOCX, DOC, XLSX, XLS) + extraction ZIPs imbriqués
3. **Extraction** — PyMuPDF pour PDFs natifs, Tesseract OCR pour PDFs scannés, python-docx / openpyxl pour les autres formats
4. **Classification** — 4 passes par document (dossier → nom fichier → mots-clés → LLM Mistral) ; le meilleur score l'emporte
5. **Vérification** — croisement avec la checklist, détection des doublons
6. **Rapport** — `output/rapport_dce_TIMESTAMP.{md,json}` + résumé console Rich

### Seuils de classification (`config.py`)

| Constante | Défaut | Rôle |
|---|:---:|---|
| `KEYWORD_CONFIDENCE_THRESHOLD` | 0.55 | Score min pour entrer dans le pool de candidats |
| `CLASSIFICATION_MIN_CONFIDENCE` | 0.75 | Court-circuite la passe suivante si atteint |
| `LLM_SKIP_THRESHOLD` | 0.80 | Passe le LLM si la confiance est déjà suffisante |
| `LLM_CONFIDENCE_THRESHOLD` | 0.60 | Score min pour accepter une réponse LLM |

---

## Sorties

Après chaque analyse, le dossier `output/` (configurable) contient :

- `rapport_dce_YYYYMMDD_HHMMSS.md` — rapport lisible avec résumé, tableaux par phase et alertes
- `rapport_dce_YYYYMMDD_HHMMSS.json` — même contenu en JSON structuré pour intégration
