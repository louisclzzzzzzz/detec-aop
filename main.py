#!/usr/bin/env python3
"""
Pipeline d'analyse DCE — Polices de Chantier SMABTP.

Analyse un dossier ou archive ZIP contenant des PDFs et vérifie la présence
de documents obligatoires depuis la checklist.

Usage :
    python main.py <dossier_ou_zip>
    python main.py <dossier_ou_zip> --output ./rapports/
    python main.py <dossier_ou_zip> --no-llm
    python main.py <dossier_ou_zip> --verbose
"""

import argparse
import sys
import time
from pathlib import Path

from rich.console import Console

from config import OUTPUT_DIR, CHECKLIST_PATH
from utils.file_utils import resolve_input, cleanup_temp
from pipeline.extractor import extract_all
from pipeline.classifier import classify_all
from pipeline.checker import load_checklist, check_completeness
from pipeline.reporter import save_reports, print_summary

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="🔍 Analyse DCE — Vérification des pièces obligatoires (Police de Chantier SMABTP)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python main.py ./dce_dossier/
  python main.py ./dce_archive.zip
  python main.py ./dce/ --output ./rapports/ --no-llm --verbose
        """,
    )
    parser.add_argument(
        "input",
        help="Chemin vers un dossier ou une archive ZIP contenant des PDFs.",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help=f"Dossier de sortie pour les rapports (défaut : {OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--checklist", "-c",
        type=str,
        default=None,
        help=f"Chemin vers le fichier checklist.json (défaut : {CHECKLIST_PATH}).",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Désactiver la classification LLM (mots-clés uniquement).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Affichage détaillé.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start_time = time.time()

    console.print()
    console.rule("[bold blue]🔍 Pipeline d'Analyse DCE — SMABTP[/bold blue]")
    console.print()

    # ─── 1. Charger la checklist ─────────────────────────────────────
    console.print("[bold]1. Chargement de la checklist[/bold]")
    checklist_path = Path(args.checklist) if args.checklist else None
    try:
        checklist = load_checklist(checklist_path)
        console.print(f"   ✅ {len(checklist)} pièces chargées depuis la checklist")
    except Exception as e:
        console.print(f"   ❌ Erreur : {e}", style="red")
        return 1

    # ─── 2. Résolution de l'entrée et listing des documents ─────────
    console.print()
    console.print("[bold]2. Détection des documents[/bold]")
    temp_dir = None
    try:
        from utils.file_utils import build_folder_structure
        docs, root_dir, temp_dir = resolve_input(args.input)
        folder_structure = build_folder_structure(docs, root_dir)
        console.print(f"   ✅ {len(docs)} document(s) trouvé(s) dans {len(folder_structure)} dossier(s)")
    except Exception as e:
        console.print(f"   ❌ Erreur : {e}", style="red")
        return 1

    if not docs:
        console.print("   ⚠️  Aucun document trouvé dans l'entrée fournie.", style="yellow")
        cleanup_temp(temp_dir)
        return 0

    if args.verbose:
        for doc in docs:
            console.print(f"      • {doc.name}")

    # ─── 3. Extraction du texte ──────────────────────────────────────
    console.print()
    console.print("[bold]3. Extraction du texte[/bold]")
    documents = extract_all(docs, root_dir=root_dir)

    valid = sum(1 for d in documents if d.is_valid)
    console.print(f"   ✅ {valid}/{len(documents)} documents extraits avec succès")

    # ─── 4. Classification ───────────────────────────────────────────
    console.print()
    use_llm = not args.no_llm
    mode = "hybride (mots-clés + LLM)" if use_llm else "mots-clés uniquement"
    console.print(f"[bold]4. Classification ({mode})[/bold]")
    classifications = classify_all(documents, checklist, use_llm=use_llm)

    classified = sum(1 for c in classifications if c.is_classified)
    console.print(f"   ✅ {classified}/{len(classifications)} documents classifiés")

    # ─── 5. Vérification de la checklist ─────────────────────────────
    console.print()
    console.print("[bold]5. Vérification de la checklist[/bold]")
    report = check_completeness(
        classifications=classifications,
        checklist=checklist,
        input_path=args.input,
        total_pdfs=len(docs),
        folder_structure=folder_structure,
    )

    # ─── 6. Génération des rapports ──────────────────────────────────
    console.print()
    console.print("[bold]6. Génération des rapports[/bold]")
    output_dir = Path(args.output) if args.output else None
    json_path, md_path = save_reports(report, output_dir)
    console.print(f"   📄 JSON : {json_path}")
    console.print(f"   📄 Markdown : {md_path}")

    # ─── 7. Résumé console ───────────────────────────────────────────
    print_summary(report)

    elapsed = time.time() - start_time
    console.print(f"  ⏱️  Terminé en {elapsed:.1f}s")
    console.print()

    # Nettoyage
    cleanup_temp(temp_dir)

    # Code de sortie : 0 si complet, 1 si pièces manquantes
    return 0 if report.overall_completeness == 1.0 else 2


if __name__ == "__main__":
    sys.exit(main())
