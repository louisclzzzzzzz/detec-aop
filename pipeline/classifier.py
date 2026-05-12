"""
Classification des documents extraits par rapport à la checklist DCE.

Stratégie hybride en 2 passes :
  1. Matching par mots-clés (aliases de la checklist) — rapide, gratuit
  2. Classification LLM Mistral (si ambiguïté ou aucun match) — plus lent, payant
"""

import json
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from mistralai.client import Mistral
from rich.console import Console

from config import (
    CLASSIFICATION_MIN_CONFIDENCE,
    KEYWORD_CONFIDENCE_THRESHOLD,
    LLM_CONFIDENCE_THRESHOLD,
    LLM_MAX_CONTEXT_CHARS,
    LLM_SKIP_THRESHOLD,
    MAX_WORKERS,
    MISTRAL_CHAT_MODEL,
    load_api_key,
)

# Sérialise les appels LLM pour respecter le rate limit (1 appel à la fois + sleep)
_llm_lock = threading.Lock()
from models.schemas import (
    ChecklistItem,
    ClassificationMethod,
    ClassificationResult,
    ExtractedDocument,
)

console = Console()


def _normalize(text: str) -> str:
    """Normalise un texte pour le matching : minuscules, sans accents, sans ponctuation."""
    # Minuscules
    text = text.lower()
    # Suppression des accents
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    # Suppression de la ponctuation, garder espaces et alphanumériques
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    # Normalisation des espaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _keyword_score(text: str, item: ChecklistItem, subdirectory: str = "") -> float:
    """
    Calcule un score de matching par mots-clés entre un texte et un item de checklist.

    Le score est basé sur le nombre d'aliases trouvés dans le texte,
    pondéré par la longueur de chaque alias (les aliases longs sont plus discriminants).
    Un bonus est appliqué si le nom du sous-dossier correspond à un alias.

    Returns:
        Score entre 0.0 et 1.0.
    """
    normalized_text = _normalize(text)
    # On se concentre sur les premières pages (plus discriminantes)
    # Prendre max 5000 caractères normalisés
    search_text = normalized_text[:5000]

    total_weight = 0.0
    matched_weight = 0.0

    for alias in item.aliases:
        normalized_alias = _normalize(alias)
        weight = len(normalized_alias)  # Les aliases longs comptent plus
        total_weight += weight

        if normalized_alias in search_text:
            matched_weight += weight

    # Bonus si l'ID de l'item est dans le nom du fichier ou le texte
    normalized_id = _normalize(item.id)
    if normalized_id in search_text:
        matched_weight += total_weight * 0.3

    # Bonus si le nom du sous-dossier contient un alias ou l'ID
    if subdirectory:
        normalized_folder = _normalize(subdirectory)
        if normalized_id in normalized_folder:
            matched_weight += total_weight * 0.4
        else:
            for alias in item.aliases:
                normalized_alias = _normalize(alias)
                if normalized_alias in normalized_folder:
                    matched_weight += total_weight * 0.3
                    break  # Un seul bonus dossier suffit

    if total_weight == 0:
        return 0.0

    return min(matched_weight / total_weight, 1.0)


def classify_by_folder(
    document: ExtractedDocument,
    checklist: list[ChecklistItem],
) -> Optional[ClassificationResult]:
    """
    Passe 0 : Classification par nom de dossier.

    Si un composant du chemin du document correspond exactement à un
    folder_keyword d'un item, on classifie directement sans lire le contenu.

    Returns:
        ClassificationResult si un dossier correspond, None sinon.
    """
    if not document.subdirectory:
        return None

    parts = [p.strip() for p in document.subdirectory.replace("\\", "/").split("/") if p.strip()]
    normalized_parts = [_normalize(p) for p in parts]

    best_item = None
    best_kw_len = 0

    for item in checklist:
        for keyword in item.folder_keywords:
            norm_kw = _normalize(keyword)
            for part in normalized_parts:
                if norm_kw in part and len(norm_kw) > best_kw_len:
                    best_kw_len = len(norm_kw)
                    best_item = item
                    break

    if best_item:
        return ClassificationResult(
            document=document,
            checklist_item_id=best_item.id,
            checklist_item_label=best_item.label,
            confidence=0.90,
            method=ClassificationMethod.FOLDER,
            reasoning=f"Dossier correspondant : {document.subdirectory}",
        )

    return None


def classify_by_filename(
    document: ExtractedDocument,
    checklist: list[ChecklistItem],
) -> Optional[ClassificationResult]:
    """
    Passe 0.5 : Classification par nom de fichier (stem).

    Normalise le stem du fichier et le compare aux aliases de chaque item.
    Plus ciblé que le keyword pass (ne lit pas le contenu), plus tolérant
    que le folder pass (ne nécessite pas de dossier dédié).

    Returns:
        ClassificationResult si un alias est trouvé dans le stem, None sinon.
    """
    stem = _normalize(document.path.stem)
    if not stem:
        return None

    best_item = None
    best_len = 0

    for item in checklist:
        for alias in item.aliases:
            norm_alias = _normalize(alias)
            if norm_alias and norm_alias in stem and len(norm_alias) > best_len:
                best_len = len(norm_alias)
                best_item = item

    if best_item:
        return ClassificationResult(
            document=document,
            checklist_item_id=best_item.id,
            checklist_item_label=best_item.label,
            confidence=0.85,
            method=ClassificationMethod.FILENAME,
            reasoning=f"Alias trouvé dans le nom de fichier : {document.path.stem}",
        )

    return None


def classify_by_keywords(
    document: ExtractedDocument,
    checklist: list[ChecklistItem],
) -> Optional[ClassificationResult]:
    """
    Passe 1 : Classification par matching de mots-clés.

    Returns:
        ClassificationResult si un match est trouvé au-dessus du seuil, None sinon.
    """
    if not document.is_valid:
        return None

    best_score = 0.0
    best_item = None
    scores = {}

    for item in checklist:
        score = _keyword_score(document.text, item, subdirectory=document.subdirectory)
        scores[item.id] = score
        if score > best_score:
            best_score = score
            best_item = item

    if best_item and best_score >= KEYWORD_CONFIDENCE_THRESHOLD:
        return ClassificationResult(
            document=document,
            checklist_item_id=best_item.id,
            checklist_item_label=best_item.label,
            confidence=round(best_score, 3),
            method=ClassificationMethod.KEYWORD,
            reasoning=f"Meilleur match mots-clés (score={best_score:.2f})",
        )

    return None


def classify_by_llm(
    document: ExtractedDocument,
    checklist: list[ChecklistItem],
) -> Optional[ClassificationResult]:
    """
    Passe 2 : Classification par LLM Mistral.

    Envoie un extrait du document au LLM avec la liste des catégories possibles,
    et demande une classification structurée en JSON.

    Returns:
        ClassificationResult si le LLM identifie le document, None sinon.
    """
    if not document.is_valid:
        return None

    try:
        api_key = load_api_key()
        client = Mistral(api_key=api_key)
    except Exception as e:
        console.print(f"    ⚠️  [yellow]LLM indisponible[/yellow] : {e}")
        return None

    # Préparer les catégories
    categories = [
        {"id": item.id, "label": item.label, "phase": item.phase.value}
        for item in checklist
    ]

    # Extrait du texte (premières pages)
    text_extract = document.text[:LLM_MAX_CONTEXT_CHARS]

    prompt = f"""Tu es un expert en assurance construction SMABTP, spécialisé dans les polices de chantier.

Voici un extrait d'un document PDF nommé "{document.filename}"""

    if document.subdirectory:
        prompt += f""", situé dans le sous-dossier "{document.subdirectory}" """

    prompt += f""" :
---
{text_extract}
---

Ce document fait partie d'un Dossier de Consultation des Entreprises (DCE).
Parmi les catégories suivantes, à laquelle correspond ce document ?

{json.dumps(categories, ensure_ascii=False, indent=2)}

IMPORTANT :
- Si le document correspond clairement à une catégorie, indique-la avec un score de confiance élevé.
- Si le document ne correspond à aucune catégorie, réponds avec "id": null.
- Analyse le contenu, le nom du fichier ET le nom du sous-dossier pour ta classification.

Réponds UNIQUEMENT en JSON valide, sans commentaire ni markdown :
{{"id": "ID_CATEGORIE_OU_NULL", "confidence": 0.0, "reasoning": "..."}}"""

    try:
        with _llm_lock:
            response = client.chat.complete(
                model=MISTRAL_CHAT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            time.sleep(1.1)

        result_text = response.choices[0].message.content.strip()
        result = json.loads(result_text)

        item_id = result.get("id")
        confidence = float(result.get("confidence", 0))
        reasoning = result.get("reasoning", "")

        if item_id and confidence >= LLM_CONFIDENCE_THRESHOLD:
            # Retrouver le label
            label = next(
                (item.label for item in checklist if item.id == item_id),
                item_id,
            )
            return ClassificationResult(
                document=document,
                checklist_item_id=item_id,
                checklist_item_label=label,
                confidence=round(confidence, 3),
                method=ClassificationMethod.LLM,
                reasoning=reasoning,
            )

    except Exception as e:
        console.print(f"    ⚠️  [yellow]Erreur LLM[/yellow] : {e}")

    return None


def classify_document(
    document: ExtractedDocument,
    checklist: list[ChecklistItem],
    use_llm: bool = True,
) -> ClassificationResult:
    """
    Classifie un document en utilisant la stratégie hybride.

    Args:
        document: Document dont le texte a été extrait.
        checklist: Liste des items de la checklist.
        use_llm: Activer la passe LLM en cas d'échec du matching par mots-clés.

    Returns:
        ClassificationResult (peut être non classifié).
    """
    candidates: list[ClassificationResult] = []

    # Passe 0 : Dossier
    result = classify_by_folder(document, checklist)
    if result:
        candidates.append(result)
        suffix = "" if result.confidence >= CLASSIFICATION_MIN_CONFIDENCE else " [yellow](faible, passage suivant)[/yellow]"
        console.print(f"    🗂️  [blue]Dossier[/blue] → {result.checklist_item_id} ({result.confidence:.0%}){suffix}")
        if result.confidence >= CLASSIFICATION_MIN_CONFIDENCE:
            return result

    # Passe 0.5 : Nom de fichier
    result = classify_by_filename(document, checklist)
    if result:
        candidates.append(result)
        suffix = "" if result.confidence >= CLASSIFICATION_MIN_CONFIDENCE else " [yellow](faible, passage suivant)[/yellow]"
        console.print(f"    📁 [blue]Filename[/blue] → {result.checklist_item_id} ({result.confidence:.0%}){suffix}")
        if result.confidence >= CLASSIFICATION_MIN_CONFIDENCE:
            return result

    # Passe 1 : Mots-clés
    result = classify_by_keywords(document, checklist)
    if result:
        candidates.append(result)
        suffix = "" if result.confidence >= CLASSIFICATION_MIN_CONFIDENCE else " [yellow](faible, passage suivant)[/yellow]"
        console.print(f"    🏷️  [green]Mots-clés[/green] → {result.checklist_item_id} ({result.confidence:.0%}){suffix}")
        if result.confidence >= CLASSIFICATION_MIN_CONFIDENCE:
            return result

    # Passe 2 : LLM — sauté si un candidat dépasse déjà LLM_SKIP_THRESHOLD
    if use_llm and candidates:
        best_so_far = max(candidates, key=lambda r: r.confidence)
        if best_so_far.confidence >= LLM_SKIP_THRESHOLD:
            console.print(
                f"    ⏭️  [dim]LLM ignoré (confiance suffisante : {best_so_far.confidence:.0%})[/dim]"
            )
            return best_so_far

    if use_llm:
        console.print(f"    🤖 Classification LLM en cours...")
        result = classify_by_llm(document, checklist)
        if result:
            candidates.append(result)
            console.print(f"    🏷️  [cyan]LLM[/cyan] → {result.checklist_item_id} ({result.confidence:.0%})")

    # Retourner le meilleur candidat toutes passes confondues
    if candidates:
        best = max(candidates, key=lambda r: r.confidence)
        return best

    console.print(f"    🏷️  [dim]Non classifié[/dim]")
    return ClassificationResult(
        document=document,
        method=ClassificationMethod.NONE,
        reasoning="Aucun match trouvé (mots-clés et LLM)",
    )


def classify_all(
    documents: list[ExtractedDocument],
    checklist: list[ChecklistItem],
    use_llm: bool = True,
) -> list[ClassificationResult]:
    """
    Classifie tous les documents extraits.

    Args:
        documents: Liste de documents extraits.
        checklist: Liste des items de la checklist.
        use_llm: Activer la classification LLM pour les documents ambigus.

    Returns:
        Liste de ClassificationResult.
    """
    total = len(documents)

    def _classify_one(item: tuple[int, ExtractedDocument]) -> ClassificationResult:
        i, doc = item
        console.print(f"  [{i + 1}/{total}] [bold]{doc.filename}[/bold]")
        return classify_document(doc, checklist, use_llm=use_llm)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        return list(executor.map(_classify_one, enumerate(documents)))
