"""
Génération de rapports d'analyse DCE en JSON et Markdown.
"""

import json
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from collections import Counter

from models.schemas import FullReport, PhaseReport, ClassificationMethod
from config import OUTPUT_DIR

console = Console()


def _classification_stats(report: FullReport) -> dict:
    """Retourne le décompte des méthodes de classification utilisées."""
    counts = Counter(
        c.method.value
        for c in report.classifications
    )
    total = len(report.classifications)
    return {"counts": dict(counts), "total": total}


def _build_json_report(report: FullReport) -> dict:
    """Construit la structure JSON du rapport."""
    return {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "input_path": report.input_path,
            "total_pdfs_found": report.total_pdfs_found,
            "total_pdfs_processed": report.total_pdfs_processed,
            "overall_completeness": round(report.overall_completeness, 3),
        },
        "phases": [
            {
                "phase": pr.phase.value,
                "label": pr.phase_label,
                "completeness": round(pr.completeness, 3),
                "items": [
                    {
                        "id": m.item.id,
                        "label": m.item.label,
                        "obligatoire": m.item.obligatoire,
                        "found": m.found,
                        "search_type": m.item.search_type,
                        "matched_file": (
                            m.matched_document.document.filename
                            if m.matched_document else None
                        ),
                        "matched_location": (
                            m.matched_document.document.location
                            if m.matched_document else None
                        ),
                        "matched_files_count": len(m.matched_documents) if m.matched_documents else None,
                        "matched_files": (
                            [d.document.location for d in m.matched_documents]
                            if m.item.search_type == "set" and m.matched_documents else None
                        ),
                        "confidence": (
                            m.matched_document.confidence
                            if m.matched_document else None
                        ),
                        "method": (
                            m.matched_document.method.value
                            if m.matched_document else None
                        ),
                        "duplicates_warning": m.duplicates_warning or None,
                    }
                    for m in pr.matches
                ],
            }
            for pr in report.phase_reports
        ],
        "unclassified": [
            {
                "filename": doc.filename,
                "location": doc.location,
                "pages": doc.num_pages,
            }
            for doc in report.unclassified_documents
        ],
        "failed": [
            {"filename": doc.filename, "location": doc.location, "extraction_error": doc.extraction_error}
            for doc in report.failed_documents
        ],
        "folder_structure": report.folder_structure,
        "classification_stats": _classification_stats(report),
    }


def _build_markdown_report(report: FullReport) -> str:
    """Construit le rapport Markdown."""
    lines = []
    now = datetime.now().strftime("%d/%m/%Y à %H:%M")

    # En-tête
    lines.append("# 📋 Rapport d'Analyse DCE — Police de Chantier SMABTP")
    lines.append("")
    lines.append(f"*Généré le {now}*")
    lines.append("")

    # Résumé exécutif
    pct = report.overall_completeness * 100
    missing_count = len(report.missing_mandatory_items)

    if pct == 100:
        status = "🟢 **COMPLET**"
    elif pct >= 70:
        status = "🟡 **INCOMPLET**"
    else:
        status = "🔴 **TRÈS INCOMPLET**"

    lines.append("## Résumé")
    lines.append("")
    lines.append(f"| Indicateur | Valeur |")
    lines.append(f"|---|---|")
    lines.append(f"| Statut | {status} |")
    lines.append(f"| Complétude globale | **{pct:.0f}%** |")
    lines.append(f"| PDFs analysés | {report.total_pdfs_processed} / {report.total_pdfs_found} |")
    lines.append(f"| Pièces obligatoires manquantes | {missing_count} |")
    lines.append(f"| Documents non classifiés | {len(report.unclassified_documents)} |")
    lines.append(f"| Documents en erreur | {len(report.failed_documents)} |")
    lines.append("")

    # Détail par phase
    for pr in report.phase_reports:
        pct_phase = pr.completeness * 100
        lines.append(f"## {pr.phase_label}")
        lines.append("")
        lines.append(f"Complétude : **{pct_phase:.0f}%** ({pr.found_items}/{pr.total_items} pièces)")
        lines.append("")
        lines.append("| Statut | Pièce | Obligatoire | Fichier(s) | Confiance | Méthode |")
        lines.append("|:---:|---|:---:|---|:---:|---|")

        for m in pr.matches:
            status_icon = m.status_icon
            if m.duplicates_warning:
                status_icon += " ⚠️"
            obligatoire = "Oui" if m.item.obligatoire else "Non"

            if m.found and m.matched_document:
                n = len(m.matched_documents)
                if m.item.search_type == "set" and n > 1:
                    location = f"{n} fichiers ↓"
                else:
                    location = m.matched_document.document.location
                confidence = f"{m.matched_document.confidence:.0%}"
                method = m.matched_document.method.value
            else:
                location = "—"
                confidence = "—"
                method = "—"

            lines.append(
                f"| {status_icon} | {m.item.label} | {obligatoire} | "
                f"`{location}` | {confidence} | {method} |"
            )

        lines.append("")

        # Blocs <details> pour les items "set" avec plusieurs fichiers
        for m in pr.matches:
            if (
                m.found
                and m.item.search_type == "set"
                and len(m.matched_documents) > 1
            ):
                n = len(m.matched_documents)
                lines.append(f"<details>")
                lines.append(f"<summary>📁 {m.item.label} — {n} fichiers trouvés</summary>")
                lines.append("")
                lines.append("| Fichier | Confiance | Méthode |")
                lines.append("|---|:---:|---|")
                for cls in sorted(m.matched_documents, key=lambda c: c.document.location):
                    lines.append(
                        f"| `{cls.document.location}` "
                        f"| {cls.confidence:.0%} "
                        f"| {cls.method.value} |"
                    )
                lines.append("")
                lines.append("</details>")
                lines.append("")

    # Alertes doublons
    all_duplicates = [
        (m, m.duplicates_warning)
        for pr in report.phase_reports
        for m in pr.matches
        if m.duplicates_warning
    ]
    if all_duplicates:
        lines.append("## ⚠️ Doublons Détectés")
        lines.append("")
        lines.append("Plusieurs documents ont matché un item `search_type=\"single\"`. Vérifier lequel est le bon :")
        lines.append("")
        for m, dupes in all_duplicates:
            lines.append(f"**{m.item.label}** — retenu : `{m.matched_document.document.location}` ({m.matched_document.confidence:.0%})")
            for d in dupes:
                lines.append(f"  - aussi matché : `{d['location']}` ({d['confidence']:.0%})")
        lines.append("")

    # Pièces manquantes obligatoires
    if report.missing_mandatory_items:
        lines.append("## ⚠️ Pièces Obligatoires Manquantes")
        lines.append("")
        for item in report.missing_mandatory_items:
            lines.append(f"- **{item.label}** (phase : {item.phase.value})")
        lines.append("")

    # Documents non classifiés
    if report.unclassified_documents:
        lines.append("## 📎 Documents Non Classifiés")
        lines.append("")
        lines.append("Ces documents n'ont pas pu être associés à une pièce de la checklist :")
        lines.append("")
        for doc in report.unclassified_documents:
            lines.append(f"- `{doc.location}` ({doc.num_pages} pages, {doc.extraction_method.value})")
        lines.append("")

    # Arborescence des sous-dossiers
    if report.folder_structure:
        lines.append("## 📂 Arborescence du Dossier")
        lines.append("")
        lines.append(f"{len(report.folder_structure)} sous-dossier(s) détecté(s) :")
        lines.append("")
        lines.append("| Dossier | Nb fichiers |")
        lines.append("|---|:---:|")
        for folder, files in report.folder_structure.items():
            folder_display = f"`{folder}/`" if folder != "." else "`./` *(racine)*"
            lines.append(f"| {folder_display} | {len(files)} |")
        lines.append("")

    # Documents en erreur d'extraction
    if report.failed_documents:
        lines.append("## ❌ Documents en Erreur d'Extraction")
        lines.append("")
        for doc in report.failed_documents:
            lines.append(f"- `{doc.location}` — `{doc.extraction_error}`")
        lines.append("")

    # Stats de classification
    stats = _classification_stats(report)
    total = stats["total"]
    if total > 0:
        lines.append("## 📊 Statistiques de Classification")
        lines.append("")
        lines.append("| Méthode | Nb documents | % |")
        lines.append("|---|:---:|:---:|")
        method_labels = {
            "folder": "🗂️ Dossier",
            "filename": "📁 Nom de fichier",
            "keyword": "🏷️ Mots-clés",
            "llm": "🤖 LLM Mistral",
            "none": "❓ Non classifié",
        }
        for method in ["folder", "filename", "keyword", "llm", "none"]:
            count = stats["counts"].get(method, 0)
            if count:
                pct = count / total * 100
                label = method_labels.get(method, method)
                lines.append(f"| {label} | {count} | {pct:.0f}% |")
        lines.append("")

    lines.append("---")
    lines.append("*Rapport généré automatiquement par le pipeline DCE SMABTP.*")

    return "\n".join(lines)


def save_reports(report: FullReport, output_dir: Path | None = None) -> tuple[Path, Path]:
    """
    Sauvegarde les rapports JSON et Markdown.

    Args:
        report: Rapport complet à sauvegarder.
        output_dir: Dossier de sortie. Utilise OUTPUT_DIR par défaut.

    Returns:
        Tuple (chemin JSON, chemin Markdown).
    """
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON
    json_path = out / f"rapport_dce_{timestamp}.json"
    json_data = _build_json_report(report)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    # Markdown
    md_path = out / f"rapport_dce_{timestamp}.md"
    md_content = _build_markdown_report(report)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    return json_path, md_path


def print_summary(report: FullReport) -> None:
    """Affiche un résumé dans la console avec Rich."""
    pct = report.overall_completeness * 100

    console.print()
    console.rule("[bold]📋 Résumé DCE[/bold]")
    console.print()

    if pct == 100:
        console.print(f"  🟢 Dossier [bold green]COMPLET[/bold green] — {pct:.0f}%")
    elif pct >= 70:
        console.print(f"  🟡 Dossier [bold yellow]INCOMPLET[/bold yellow] — {pct:.0f}%")
    else:
        console.print(f"  🔴 Dossier [bold red]TRÈS INCOMPLET[/bold red] — {pct:.0f}%")

    console.print(
        f"  📄 {report.total_pdfs_processed} PDFs analysés "
        f"({len(report.unclassified_documents)} non classifiés, "
        f"{len(report.failed_documents)} en erreur)"
    )
    console.print()

    # Tableau par phase
    table = Table(title="Vérification Checklist", show_lines=True)
    table.add_column("Phase", style="bold")
    table.add_column("Pièce", min_width=40)
    table.add_column("Oblig.", justify="center")
    table.add_column("Statut", justify="center")
    table.add_column("Fichier")
    table.add_column("Conf.", justify="center")

    for pr in report.phase_reports:
        for i, m in enumerate(pr.matches):
            phase_label = pr.phase_label if i == 0 else ""
            obligatoire = "✓" if m.item.obligatoire else ""

            if m.found and m.matched_document:
                status = "[green]✅[/green]" + (" [yellow]⚠[/yellow]" if m.duplicates_warning else "")
                n = len(m.matched_documents)
                if m.item.search_type == "set" and n > 1:
                    previews = [cls.document.filename for cls in m.matched_documents[:3]]
                    location = f"{n} fichiers\n" + "\n".join(f"[dim]{f}[/dim]" for f in previews)
                    if n > 3:
                        location += f"\n[dim]... et {n - 3} autres[/dim]"
                else:
                    location = m.matched_document.document.location
                confidence = f"{m.matched_document.confidence:.0%}"
            elif not m.item.obligatoire:
                status = "[yellow]⚠️[/yellow]"
                location = "—"
                confidence = "—"
            else:
                status = "[red]❌[/red]"
                location = "—"
                confidence = "—"

            table.add_row(phase_label, m.item.label, obligatoire, status, location, confidence)

    console.print(table)

    # Pièces manquantes
    missing = report.missing_mandatory_items
    if missing:
        console.print()
        console.print(f"  [bold red]⚠️  {len(missing)} pièce(s) obligatoire(s) manquante(s) :[/bold red]")
        for item in missing:
            console.print(f"     • {item.label}")

    # Stats classification
    stats = _classification_stats(report)
    total = stats["total"]
    if total > 0:
        console.print()
        console.rule("[dim]📊 Méthodes de classification[/dim]")
        method_labels = {
            "folder": "🗂️  Dossier",
            "filename": "📁 Filename",
            "keyword": "🏷️  Mots-clés",
            "llm": "🤖 LLM Mistral",
            "none": "❓ Non classifié",
        }
        for method in ["folder", "filename", "keyword", "llm", "none"]:
            count = stats["counts"].get(method, 0)
            if count:
                pct = count / total * 100
                label = method_labels.get(method, method)
                console.print(f"  {label} : {count} doc(s) ({pct:.0f}%)")

    console.print()
