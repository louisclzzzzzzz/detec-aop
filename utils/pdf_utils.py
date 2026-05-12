"""
Utilitaires PDF : détection natif vs scanné.
"""

import fitz  # PyMuPDF

from config import SCANNED_TEXT_THRESHOLD


def extract_native_text(pdf_path: str) -> tuple[str, int]:
    """
    Extrait le texte d'un PDF via PyMuPDF (texte natif uniquement).

    Returns:
        Tuple (texte complet, nombre de pages).
    """
    doc = fitz.open(pdf_path)
    pages_text = []
    for page in doc:
        pages_text.append(page.get_text())
    num_pages = len(doc)
    doc.close()
    return "\n".join(pages_text), num_pages


def is_scanned_pdf(pdf_path: str) -> tuple[bool, str, int]:
    """
    Détermine si un PDF est scanné (image) ou natif (texte extractible).

    Heuristique : si le ratio moyen de caractères par page est inférieur
    au seuil, le PDF est considéré comme scanné.

    Returns:
        Tuple (is_scanned, texte_extrait, nb_pages).
    """
    text, num_pages = extract_native_text(pdf_path)

    if num_pages == 0:
        return True, "", 0

    avg_chars_per_page = len(text.strip()) / num_pages

    is_scanned = avg_chars_per_page < SCANNED_TEXT_THRESHOLD

    return is_scanned, text, num_pages
