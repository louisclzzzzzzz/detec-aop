"""
Modèles de données pour le pipeline d'analyse DCE.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from pathlib import Path


class Phase(str, Enum):
    """Phases du dossier DCE."""
    CONSTITUTION = "constitution"
    CONTRAT = "contrat"
    RECEPTION = "reception"


class ExtractionMethod(str, Enum):
    """Méthode utilisée pour extraire le texte d'un document."""
    NATIVE = "native"       # Extraction directe via PyMuPDF (PDF natif)
    OCR_MISTRAL = "ocr"     # OCR via Tesseract (PDF scanné)
    DOCX = "docx"           # Extraction via python-docx / docx2txt
    DOC = "doc"             # Extraction via docx2txt (format .doc binaire)
    XLSX = "xlsx"           # Extraction via openpyxl
    XLS = "xls"             # Extraction via xlrd
    FAILED = "failed"       # Extraction échouée


class ClassificationMethod(str, Enum):
    """Méthode utilisée pour classifier un document."""
    FOLDER = "folder"       # Matching par nom de dossier
    FILENAME = "filename"   # Matching par nom de fichier
    KEYWORD = "keyword"     # Matching par mots-clés / aliases
    LLM = "llm"             # Classification par LLM Mistral
    NONE = "none"           # Non classifié


@dataclass
class ChecklistItem:
    """Un élément de la checklist DCE."""
    id: str
    label: str
    phase: Phase
    obligatoire: bool
    aliases: list[str] = field(default_factory=list)
    search_type: str = "single"       # "single" | "set"
    folder_keywords: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "ChecklistItem":
        return cls(
            id=data["id"],
            label=data["label"],
            phase=Phase(data["phase"]),
            obligatoire=data["obligatoire"],
            aliases=data.get("aliases", []),
            search_type=data.get("search_type", "single"),
            folder_keywords=data.get("folder_keywords", []),
        )


@dataclass
class ExtractedDocument:
    """Un document PDF dont le texte a été extrait."""
    path: Path
    filename: str
    text: str
    num_pages: int
    extraction_method: ExtractionMethod
    subdirectory: str = ""  # Chemin relatif du sous-dossier (ex: "lot3/plans")
    extraction_error: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        """Le document a-t-il été extrait avec succès ?"""
        return self.extraction_method != ExtractionMethod.FAILED and len(self.text.strip()) > 0

    @property
    def location(self) -> str:
        """Chemin lisible du document dans l'arborescence."""
        if self.subdirectory:
            return f"{self.subdirectory}/{self.filename}"
        return self.filename


@dataclass
class ClassificationResult:
    """Résultat de la classification d'un document."""
    document: ExtractedDocument
    checklist_item_id: Optional[str] = None
    checklist_item_label: Optional[str] = None
    confidence: float = 0.0
    method: ClassificationMethod = ClassificationMethod.NONE
    reasoning: str = ""

    @property
    def is_classified(self) -> bool:
        return self.checklist_item_id is not None and self.confidence > 0


@dataclass
class ChecklistMatch:
    """Correspondance entre un item de checklist et un document trouvé."""
    item: ChecklistItem
    found: bool = False
    matched_document: Optional[ClassificationResult] = None
    matched_documents: list["ClassificationResult"] = field(default_factory=list)
    # Pour search_type="single" : autres docs ayant aussi matché avec confiance significative
    duplicates_warning: list[dict] = field(default_factory=list)

    @property
    def status_icon(self) -> str:
        if self.found:
            return "✅"
        elif not self.item.obligatoire:
            return "⚠️"
        else:
            return "❌"


@dataclass
class PhaseReport:
    """Rapport pour une phase du DCE."""
    phase: Phase
    matches: list[ChecklistMatch] = field(default_factory=list)

    @property
    def phase_label(self) -> str:
        labels = {
            Phase.CONSTITUTION: "Constitution du dossier",
            Phase.CONTRAT: "Établissement du contrat",
            Phase.RECEPTION: "Réception du chantier",
        }
        return labels.get(self.phase, self.phase.value)

    @property
    def total_items(self) -> int:
        return len(self.matches)

    @property
    def found_items(self) -> int:
        return sum(1 for m in self.matches if m.found)

    @property
    def missing_mandatory(self) -> list[ChecklistMatch]:
        return [m for m in self.matches if not m.found and m.item.obligatoire]

    @property
    def completeness(self) -> float:
        mandatory = [m for m in self.matches if m.item.obligatoire]
        if not mandatory:
            return 1.0
        return sum(1 for m in mandatory if m.found) / len(mandatory)


@dataclass
class FullReport:
    """Rapport complet d'analyse DCE."""
    input_path: str
    total_pdfs_found: int
    total_pdfs_processed: int
    phase_reports: list[PhaseReport] = field(default_factory=list)
    unclassified_documents: list[ExtractedDocument] = field(default_factory=list)
    failed_documents: list[ExtractedDocument] = field(default_factory=list)
    classifications: list[ClassificationResult] = field(default_factory=list)
    folder_structure: dict[str, list[str]] = field(default_factory=dict)  # sous-dossier → [fichiers]

    @property
    def overall_completeness(self) -> float:
        """Score global de complétude (pièces obligatoires trouvées / total obligatoires)."""
        total_mandatory = 0
        found_mandatory = 0
        for pr in self.phase_reports:
            for m in pr.matches:
                if m.item.obligatoire:
                    total_mandatory += 1
                    if m.found:
                        found_mandatory += 1
        if total_mandatory == 0:
            return 1.0
        return found_mandatory / total_mandatory

    @property
    def missing_mandatory_items(self) -> list[ChecklistItem]:
        """Liste de toutes les pièces obligatoires manquantes."""
        missing = []
        for pr in self.phase_reports:
            for m in pr.matches:
                if not m.found and m.item.obligatoire:
                    missing.append(m.item)
        return missing
