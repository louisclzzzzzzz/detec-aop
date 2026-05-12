"""
Utilitaires de gestion de fichiers : extraction ZIP et listing des documents.
"""

import zipfile
import tempfile
import shutil
from pathlib import Path

from rich.console import Console

console = Console()

SUPPORTED_EXTENSIONS = {".pdf", ".PDF", ".docx", ".doc", ".xlsx", ".xls"}


def extract_zip(zip_path: Path, dest: Path | None = None) -> Path:
    """
    Extrait une archive ZIP dans un dossier de destination.

    Args:
        zip_path: Chemin vers l'archive ZIP.
        dest: Dossier de destination. Si None, crée un dossier temporaire.

    Returns:
        Chemin du dossier contenant les fichiers extraits.
    """
    if not zip_path.exists():
        raise FileNotFoundError(f"Archive introuvable : {zip_path}")

    if not zipfile.is_zipfile(zip_path):
        raise ValueError(f"Le fichier n'est pas une archive ZIP valide : {zip_path}")

    if dest is None:
        dest = Path(tempfile.mkdtemp(prefix="dce_"))

    console.print(f"  📦 Extraction de [bold]{zip_path.name}[/bold] → {dest}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest)

    return dest


def _extract_nested_zips(folder_path: Path, depth: int = 0) -> None:
    """
    Extrait récursivement les archives ZIP imbriquées dans un dossier.
    Chaque ZIP est extrait dans un sous-dossier portant son nom (sans extension).
    Limite à 3 niveaux de profondeur pour éviter les boucles.
    """
    if depth > 3:
        return

    for zip_path in list(folder_path.rglob("*.zip")):
        dest = zip_path.parent / zip_path.stem
        if dest.exists():
            continue
        try:
            if not zipfile.is_zipfile(zip_path):
                continue
            dest.mkdir(parents=True, exist_ok=True)
            console.print(f"  📦 ZIP imbriqué : [bold]{zip_path.name}[/bold]")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(dest)
            _extract_nested_zips(dest, depth + 1)
        except Exception as e:
            console.print(f"  ⚠️  ZIP ignoré ({zip_path.name}) : {e}", style="yellow")


def list_documents(folder_path: Path) -> list[Path]:
    """
    Liste récursivement tous les documents supportés d'un dossier
    (PDF, DOCX, DOC, XLSX, XLS).

    Args:
        folder_path: Chemin du dossier à parcourir.

    Returns:
        Liste de chemins triés par nom.
    """
    if not folder_path.exists():
        raise FileNotFoundError(f"Dossier introuvable : {folder_path}")

    found: list[Path] = []
    seen: set[Path] = set()

    for ext in SUPPORTED_EXTENSIONS:
        pattern = f"*{ext}"
        for p in folder_path.rglob(pattern):
            # Ignorer les fichiers temporaires Office (~$...) et les fichiers cachés
            if p.name.startswith("~$") or p.name.startswith("."):
                continue
            if p not in seen:
                found.append(p)
                seen.add(p)

    return sorted(found, key=lambda p: (p.parent, p.name.lower()))


def resolve_input(input_path: str) -> tuple[list[Path], Path, Path | None]:
    """
    Point d'entrée : accepte un dossier ou un ZIP et retourne la liste des documents.

    Args:
        input_path: Chemin vers un dossier ou une archive ZIP.

    Returns:
        Tuple (liste de documents, dossier racine, dossier temporaire à nettoyer ou None).
    """
    path = Path(input_path).resolve()
    temp_dir = None

    if not path.exists():
        raise FileNotFoundError(f"Chemin introuvable : {path}")

    if path.is_file() and path.suffix.lower() == ".zip":
        temp_dir = extract_zip(path)
        root_dir = temp_dir
        _extract_nested_zips(root_dir)
        docs = list_documents(root_dir)
    elif path.is_dir():
        root_dir = path
        _extract_nested_zips(root_dir)
        docs = list_documents(root_dir)
    else:
        raise ValueError(
            f"Entrée non supportée : {path}\n"
            "Fournissez un dossier ou une archive ZIP."
        )

    return docs, root_dir, temp_dir


def get_subdirectory(pdf_path: Path, root_dir: Path) -> str:
    """
    Calcule le chemin relatif du sous-dossier d'un PDF par rapport à la racine.

    Args:
        pdf_path: Chemin absolu du fichier PDF.
        root_dir: Chemin absolu du dossier racine.

    Returns:
        Chemin relatif du sous-dossier (ex: "lot3/plans"), ou "" si à la racine.
    """
    try:
        relative = pdf_path.parent.relative_to(root_dir)
        return str(relative) if str(relative) != "." else ""
    except ValueError:
        return ""


def build_folder_structure(docs: list[Path], root_dir: Path) -> dict[str, list[str]]:
    """
    Construit un dictionnaire de l'arborescence des sous-dossiers.

    Returns:
        Dict {sous-dossier: [noms de fichiers]}.
    """
    structure: dict[str, list[str]] = {}
    for doc in docs:
        subdir = get_subdirectory(doc, root_dir)
        folder_key = subdir if subdir else "."
        if folder_key not in structure:
            structure[folder_key] = []
        structure[folder_key].append(doc.name)

    return dict(sorted(structure.items()))


def cleanup_temp(temp_dir: Path | None) -> None:
    """Supprime le dossier temporaire s'il existe."""
    if temp_dir and temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
