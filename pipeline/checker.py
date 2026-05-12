"""
Moteur de vérification : croise les résultats de classification avec la checklist.
"""

import json
from pathlib import Path

from models.schemas import (
    ChecklistItem,
    ChecklistMatch,
    ClassificationResult,
    ExtractedDocument,
    FullReport,
    Phase,
    PhaseReport,
)
from config import CHECKLIST_PATH, KEYWORD_CONFIDENCE_THRESHOLD


def load_checklist(checklist_path: Path | None = None) -> list[ChecklistItem]:
    """
    Charge la checklist depuis le fichier JSON.

    Args:
        checklist_path: Chemin vers le fichier checklist.json.

    Returns:
        Liste de ChecklistItem.
    """
    path = checklist_path or CHECKLIST_PATH

    if not path.exists():
        raise FileNotFoundError(f"Checklist introuvable : {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return [ChecklistItem.from_dict(item) for item in data]


def check_completeness(
    classifications: list[ClassificationResult],
    checklist: list[ChecklistItem],
    input_path: str,
    total_pdfs: int,
    folder_structure: dict[str, list[str]] | None = None,
) -> FullReport:
    """
    Vérifie la complétude du dossier DCE par rapport à la checklist.

    Pour chaque item de la checklist, cherche s'il a été identifié parmi
    les documents classifiés. Regroupe les résultats par phase.

    Args:
        classifications: Résultats de classification de tous les documents.
        checklist: Liste complète des items de la checklist.
        input_path: Chemin d'entrée (pour le rapport).
        total_pdfs: Nombre total de PDFs trouvés.
        folder_structure: Dict {sous-dossier: [fichiers]} pour l'arborescence.

    Returns:
        FullReport avec le détail par phase et les documents non classifiés.
    """
    # Indexer les classifications par item_id
    best_by_item: dict[str, ClassificationResult] = {}
    all_by_item: dict[str, list[ClassificationResult]] = {}
    for cls in classifications:
        if cls.is_classified:
            item_id = cls.checklist_item_id
            all_by_item.setdefault(item_id, []).append(cls)
            if item_id not in best_by_item or cls.confidence > best_by_item[item_id].confidence:
                best_by_item[item_id] = cls

    # Construire les rapports par phase
    phases = [Phase.CONSTITUTION, Phase.CONTRAT, Phase.RECEPTION]
    phase_reports = []

    for phase in phases:
        phase_items = [item for item in checklist if item.phase == phase]
        matches = []

        for item in phase_items:
            if item.id in best_by_item:
                best = best_by_item[item.id]
                all_matches = all_by_item.get(item.id, [])

                # Alerte doublons pour les items "single" avec plusieurs candidats
                duplicates: list[dict] = []
                if item.search_type == "single" and len(all_matches) > 1:
                    duplicates = [
                        {
                            "filename": cls.document.filename,
                            "location": cls.document.location,
                            "confidence": cls.confidence,
                        }
                        for cls in all_matches
                        if cls.document.filename != best.document.filename
                        and cls.confidence > KEYWORD_CONFIDENCE_THRESHOLD
                    ]

                matches.append(ChecklistMatch(
                    item=item,
                    found=True,
                    matched_document=best,
                    matched_documents=all_matches,
                    duplicates_warning=duplicates,
                ))
            else:
                matches.append(ChecklistMatch(
                    item=item,
                    found=False,
                ))

        phase_reports.append(PhaseReport(phase=phase, matches=matches))

    # Documents non classifiés
    unclassified = [
        cls.document for cls in classifications
        if not cls.is_classified and cls.document.is_valid
    ]

    # Documents en erreur
    failed = [
        cls.document for cls in classifications
        if not cls.document.is_valid
    ]

    return FullReport(
        input_path=input_path,
        total_pdfs_found=total_pdfs,
        total_pdfs_processed=len(classifications),
        phase_reports=phase_reports,
        unclassified_documents=unclassified,
        failed_documents=failed,
        classifications=classifications,
        folder_structure=folder_structure or {},
    )
