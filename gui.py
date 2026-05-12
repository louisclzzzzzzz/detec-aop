"""
Interface graphique Streamlit pour le pipeline DCE SMABTP.

Lancement :
    streamlit run gui.py
"""

import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# ─── sys.path + .env ──────────────────────────────────────────────────
_here = Path(__file__).parent.resolve()
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

load_dotenv(_here / ".env")

# ─── Page config ──────────────────────────────────────────────────────
st.set_page_config(
    page_title="Analyse DCE — SMABTP",
    page_icon="🔍",
    layout="wide",
)

st.markdown("""
<style>
/* Étapes visuelles */
.step-box { text-align: center; padding: 0.4em 0; }
.step-box span { font-size: 1.4em; }
.step-box small { display: block; font-size: 0.75em; color: #888; }
/* Boutons de fichiers */
.stButton button { text-align: left; }
</style>
""", unsafe_allow_html=True)

# ─── Constantes ───────────────────────────────────────────────────────
_STEP_LABELS = [
    "Checklist",
    "Documents",
    "Extraction",
    "Classification",
    "Vérification",
    "Rapport",
]
_TAG_RE = re.compile(r"\[/?[^\]]*\]")


# ─── Utilitaires ──────────────────────────────────────────────────────
def _open_file(path: Path | str):
    """Ouvre un fichier avec l'application par défaut du système."""
    p = Path(path)
    if not p.exists():
        st.error(f"Fichier introuvable : {p}")
        return
    
    try:
        if sys.platform == "win32":
            os.startfile(p)
        elif sys.platform == "darwin":
            subprocess.run(["open", str(p)])
        else:
            subprocess.run(["xdg-open", str(p)])
    except Exception as e:
        st.error(f"Impossible d'ouvrir le fichier : {e}")


# ─── Console proxy (redirige Rich vers la queue) ──────────────────────
class _QueueConsole:
    def __init__(self, log_queue: queue.Queue):
        self._q = log_queue

    def print(self, *args, **kwargs):
        msg = _TAG_RE.sub("", " ".join(str(a) for a in args)).strip()
        if msg:
            self._q.put(msg)

    def rule(self, *args, **kwargs):
        title = _TAG_RE.sub("", str(args[0]) if args else "").strip()
        if title:
            self._q.put(f"{'─' * 10} {title} {'─' * 10}")


# ─── Session state init ───────────────────────────────────────────────
def _init():
    for k, v in {
        "phase": "input",          # "input" | "running" | "done"
        "error": None,
        "logs": [],
        "current_step": 0,
        "report": None,
        "md_path": None,
        "json_path": None,
        "_log_q": None,
        "_step_q": None,
        "_tmp_zip": None,
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init()


# ─── Pipeline runner (thread secondaire) ─────────────────────────────
def _run_pipeline(
    input_path: str,
    use_llm: bool,
    api_key: str | None,
    output_dir: str,
    log_q: queue.Queue,
    step_q: queue.Queue,
):
    qcon = _QueueConsole(log_q)

    def step(n: int):
        step_q.put(n)

    try:
        if api_key:
            os.environ["MISTRAL_API_KEY"] = api_key

        # Patcher les consoles Rich des modules du pipeline
        import pipeline.extractor as _ext
        import pipeline.classifier as _cls
        import pipeline.reporter as _rep
        import utils.file_utils as _fu

        for mod in (_ext, _cls, _rep, _fu):
            if hasattr(mod, "console"):
                mod.console = qcon

        from utils.file_utils import resolve_input, cleanup_temp, build_folder_structure
        from pipeline.extractor import extract_all
        from pipeline.classifier import classify_all
        from pipeline.checker import load_checklist, check_completeness
        from pipeline.reporter import save_reports

        # 1 ── Checklist
        step(1)
        checklist = load_checklist(None)
        log_q.put(f"  ✅ {len(checklist)} pièces chargées depuis la checklist")

        # 2 ── Listing documents
        step(2)
        docs, root_dir, temp_dir = resolve_input(input_path)
        folder_structure = build_folder_structure(docs, root_dir)
        log_q.put(f"  ✅ {len(docs)} document(s) dans {len(folder_structure)} dossier(s)")

        if not docs:
            log_q.put("  ⚠️ Aucun document trouvé.")
            step_q.put(("done", None))
            cleanup_temp(temp_dir)
            return

        # 3 ── Extraction
        step(3)
        documents = extract_all(docs, root_dir=root_dir)
        valid = sum(1 for d in documents if d.is_valid)
        log_q.put(f"  ✅ {valid}/{len(documents)} documents extraits avec succès")

        # 4 ── Classification
        step(4)
        classifications = classify_all(documents, checklist, use_llm=use_llm)
        classified = sum(1 for c in classifications if c.is_classified)
        log_q.put(f"  ✅ {classified}/{len(classifications)} documents classifiés")

        # 5 ── Vérification checklist
        step(5)
        report = check_completeness(
            classifications=classifications,
            checklist=checklist,
            input_path=input_path,
            total_pdfs=len(docs),
            folder_structure=folder_structure,
        )
        pct = int(report.overall_completeness * 100)
        log_q.put(f"  ✅ Complétude globale : {pct}%")

        # 6 ── Rapport
        step(6)
        out = Path(output_dir)
        json_path, md_path = save_reports(report, out)
        log_q.put(f"  📄 Rapport sauvegardé : {md_path.name}")

        cleanup_temp(temp_dir)
        step_q.put(("done", {
            "report": report,
            "md_path": str(md_path),
            "json_path": str(json_path),
        }))

    except Exception as exc:
        import traceback
        step_q.put(("error", f"{exc}\n\n{traceback.format_exc()}"))


# ─── Affichage des étapes visuelles ──────────────────────────────────
def _render_steps(current: int, done: bool, error: bool):
    cols = st.columns(len(_STEP_LABELS))
    for i, (col, label) in enumerate(zip(cols, _STEP_LABELS)):
        n = i + 1
        if error:
            icon = "✅" if n < current else ("❌" if n == current else "⬜")
        elif done:
            icon = "✅"
        elif n < current:
            icon = "✅"
        elif n == current:
            icon = "🔄"
        else:
            icon = "⬜"
        col.markdown(
            f'<div class="step-box"><span>{icon}</span><small>{n}. {label}</small></div>',
            unsafe_allow_html=True,
        )


# ─── Affichage du rapport final ───────────────────────────────────────
def _render_report(report, md_path: str, json_path: str):
    pct = report.overall_completeness
    pct_int = int(pct * 100)
    n_missing = len(report.missing_mandatory_items)

    # Bannière statut
    if pct >= 1.0:
        st.success(f"🟢  Dossier **COMPLET** — toutes les pièces obligatoires sont présentes.")
    elif pct >= 0.7:
        st.warning(f"🟡  Dossier **INCOMPLET** — {n_missing} pièce(s) obligatoire(s) manquante(s).")
    else:
        st.error(f"🔴  Dossier **TRÈS INCOMPLET** — {n_missing} pièce(s) obligatoire(s) manquante(s).")

    # Métriques
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Complétude globale", f"{pct_int} %")
    c2.metric("Documents analysés", report.total_pdfs_processed)
    c3.metric("Pièces manquantes", n_missing)
    c4.metric("Non classifiés", len(report.unclassified_documents))

    st.markdown("---")

    # ── Détail par phase ──────────────────────────────────────────────
    st.markdown("### Détail par phase")

    tab_labels = [
        f"{pr.phase_label}  ({pr.found_items}/{pr.total_items})"
        for pr in report.phase_reports
    ]
    tabs = st.tabs(tab_labels)

    for tab, pr in zip(tabs, report.phase_reports):
        with tab:
            pct_phase = int(pr.completeness * 100)
            st.caption(
                f"Complétude de la phase : **{pct_phase} %** "
                f"— {pr.found_items}/{pr.total_items} pièces trouvées"
            )

            # En-tête du tableau
            hcols = st.columns([1, 5, 1, 5, 1.2, 2])
            for col, h in zip(hcols, ["", "Pièce", "Oblig.", "Fichier", "Confiance", "Méthode"]):
                col.markdown(f"**{h}**")
            st.divider()

            for i, m in enumerate(pr.matches):
                status_icon = m.status_icon + (" ⚠️" if m.duplicates_warning else "")
                oblig = "✓" if m.item.obligatoire else ""

                if m.found and m.matched_document:
                    n = len(m.matched_documents)
                    if m.item.search_type == "set" and n > 1:
                        loc = f"{n} fichiers"
                        abs_path = None
                    else:
                        loc = m.matched_document.document.location
                        abs_path = m.matched_document.document.path
                    conf = f"{m.matched_document.confidence:.0%}"
                    meth = m.matched_document.method.value
                else:
                    loc, conf, meth, abs_path = "—", "—", "—", None

                row = st.columns([1, 5, 1, 5, 1.2, 2])
                row[0].write(status_icon)
                row[1].write(m.item.label)
                row[2].write(oblig)
                
                # Fichier (cliquable si trouvé et unique)
                if abs_path:
                    if row[3].button(f"📄 {loc}", key=f"btn_{pr.phase}_{i}_{m.item.id}", help="Ouvrir le document", use_container_width=True):
                        _open_file(abs_path)
                else:
                    row[3].code(loc, language=None)
                    
                row[4].write(conf)
                row[5].write(meth)

                # Détail ensemble de fichiers
                if (
                    m.found
                    and m.item.search_type == "set"
                    and len(m.matched_documents) > 1
                ):
                    with st.expander(f"📂 Voir les {len(m.matched_documents)} fichiers"):
                        for j, cls in enumerate(sorted(m.matched_documents, key=lambda c: c.document.location)):
                            c1, c2 = st.columns([4, 1])
                            c1.markdown(
                                f"- `{cls.document.location}` "
                                f"— {cls.confidence:.0%} ({cls.method.value})"
                            )
                            if c2.button("Ouvrir", key=f"open_set_{m.item.id}_{j}"):
                                _open_file(cls.document.path)

    # ── Pièces manquantes ─────────────────────────────────────────────
    if report.missing_mandatory_items:
        st.markdown("---")
        st.markdown("### ⚠️ Pièces obligatoires manquantes")
        for item in report.missing_mandatory_items:
            st.markdown(f"- **{item.label}** &nbsp;*(phase : {item.phase.value})*")

    # ── Documents non classifiés ──────────────────────────────────────
    if report.unclassified_documents:
        with st.expander(f"📎 {len(report.unclassified_documents)} document(s) non classifié(s)"):
            for i, doc in enumerate(report.unclassified_documents):
                c1, c2 = st.columns([4, 1])
                c1.markdown(
                    f"- `{doc.location}` — {doc.num_pages} page(s), "
                    f"méthode : {doc.extraction_method.value}"
                )
                if c2.button("Ouvrir", key=f"open_unclassified_{i}"):
                    _open_file(doc.path)

    # ── Documents en erreur ───────────────────────────────────────────
    if report.failed_documents:
        with st.expander(f"❌ {len(report.failed_documents)} document(s) en erreur d'extraction"):
            for i, doc in enumerate(report.failed_documents):
                c1, c2 = st.columns([4, 1])
                c1.markdown(f"- `{doc.location}` — `{doc.extraction_error}`")
                if c2.button("Ouvrir", key=f"open_failed_{i}"):
                    _open_file(doc.path)

    # ── Téléchargements ───────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Télécharger le rapport")
    dl1, dl2 = st.columns(2)

    try:
        md_content = Path(md_path).read_text(encoding="utf-8")
        dl1.download_button(
            "📄 Rapport Markdown (.md)",
            md_content,
            file_name=Path(md_path).name,
            mime="text/markdown",
            use_container_width=True,
        )
    except Exception:
        pass

    try:
        json_content = Path(json_path).read_text(encoding="utf-8")
        dl2.download_button(
            "📊 Rapport JSON (.json)",
            json_content,
            file_name=Path(json_path).name,
            mime="application/json",
            use_container_width=True,
        )
    except Exception:
        pass

    # ── Rapport Markdown brut (dépliable) ─────────────────────────────
    with st.expander("📋 Rapport Markdown complet"):
        try:
            st.markdown(md_content)
        except Exception:
            pass


# ─── Page principale ──────────────────────────────────────────────────
def main():
    st.title("🔍 Analyse DCE — Police de Chantier SMABTP")
    st.caption(
        "Vérification automatique de la complétude d'un "
        "Dossier de Consultation des Entreprises (18 pièces checklist)"
    )

    # ── Sidebar ───────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("## Options d'analyse")

        use_llm = st.toggle(
            "Classification LLM Mistral",
            value=True,
            help="Désactiver pour une analyse rapide par mots-clés uniquement (pas d'appel API).",
        )

        if use_llm:
            if os.environ.get("MISTRAL_API_KEY"):
                st.caption("🔑 Clé API chargée depuis `.env`")
            else:
                st.warning("Clé `MISTRAL_API_KEY` absente du fichier `.env`.")

        st.markdown("---")
        output_dir = st.text_input(
            "Dossier de sortie des rapports",
            value=str(_here / "output"),
        )

        if st.session_state.phase != "input":
            st.markdown("---")
            st.markdown("**Progression**")
            step = st.session_state.current_step
            done = st.session_state.phase == "done"
            err = bool(st.session_state.error)
            for i, label in enumerate(_STEP_LABELS, 1):
                if err:
                    icon = "✅" if i < step else ("❌" if i == step else "⬜")
                elif done:
                    icon = "✅"
                elif i < step:
                    icon = "✅"
                elif i == step:
                    icon = "🔄"
                else:
                    icon = "⬜"
                st.markdown(f"{icon} {i}. {label}")

    # ══════════════════════════════════════════════════════════════════
    # PHASE : saisie
    # ══════════════════════════════════════════════════════════════════
    if st.session_state.phase == "input":
        st.markdown("### Fournir le dossier DCE")

        tab_zip, tab_dir = st.tabs(
            ["📦  Déposer une archive ZIP", "📁  Chemin de dossier local"]
        )

        with tab_zip:
            st.markdown(
                "Glissez-déposez ou sélectionnez l'archive ZIP du DCE. "
                "Les sous-dossiers et ZIPs imbriqués sont gérés automatiquement."
            )
            uploaded = st.file_uploader(
                "Archive ZIP",
                type=["zip"],
                label_visibility="collapsed",
            )
            if uploaded:
                st.success(f"✅ **{uploaded.name}** — {uploaded.size / 1024:.0f} Ko")

        with tab_dir:
            st.markdown("Indiquez le chemin absolu vers le dossier DCE sur votre machine.")
            folder_input = st.text_input(
                "Chemin du dossier",
                placeholder="/Users/vous/DCE/dce1/",
                label_visibility="collapsed",
            )
            if folder_input:
                if Path(folder_input).exists():
                    st.success(f"✅ Dossier trouvé : `{folder_input}`")
                else:
                    st.error("❌ Dossier introuvable.")

        # Résolution de l'entrée active
        input_source = None
        if uploaded:
            input_source = ("zip", uploaded)
        elif folder_input and Path(folder_input).exists():
            input_source = ("folder", folder_input)

        # Validation clé API
        api_missing = use_llm and not os.environ.get("MISTRAL_API_KEY")
        if api_missing:
            st.info("ℹ️ Clé `MISTRAL_API_KEY` introuvable dans `.env` — désactivez le LLM ou ajoutez la clé.")

        st.markdown("---")
        if st.button(
            "🚀  Lancer l'analyse",
            type="primary",
            disabled=(input_source is None or api_missing),
            use_container_width=True,
        ):
            # Préparer le chemin d'entrée
            if input_source[0] == "zip":
                tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
                tmp.write(input_source[1].read())
                tmp.close()
                final_path = tmp.name
                st.session_state._tmp_zip = final_path
            else:
                final_path = input_source[1]
                st.session_state._tmp_zip = None

            # Initialiser les queues et l'état
            log_q: queue.Queue = queue.Queue()
            step_q: queue.Queue = queue.Queue()
            st.session_state._log_q = log_q
            st.session_state._step_q = step_q
            st.session_state.logs = []
            st.session_state.current_step = 0
            st.session_state.phase = "running"
            st.session_state.error = None
            st.session_state.report = None

            threading.Thread(
                target=_run_pipeline,
                args=(final_path, use_llm, None, output_dir, log_q, step_q),
                daemon=True,
            ).start()

            st.rerun()

    # ══════════════════════════════════════════════════════════════════
    # PHASE : en cours (et affichage post-run des logs)
    # ══════════════════════════════════════════════════════════════════
    if st.session_state.phase in ("running", "done"):
        log_q: queue.Queue | None = st.session_state._log_q
        step_q: queue.Queue | None = st.session_state._step_q

        # Drainer les queues si le pipeline tourne encore
        if st.session_state.phase == "running" and log_q and step_q:
            while not step_q.empty():
                item = step_q.get_nowait()
                if isinstance(item, tuple):
                    kind, data = item
                    if kind == "done":
                        st.session_state.phase = "done"
                        if data:
                            st.session_state.report = data["report"]
                            st.session_state.md_path = data["md_path"]
                            st.session_state.json_path = data["json_path"]
                        # Nettoyage ZIP temporaire
                        if st.session_state._tmp_zip:
                            try:
                                os.unlink(st.session_state._tmp_zip)
                            except Exception:
                                pass
                    elif kind == "error":
                        st.session_state.phase = "done"
                        st.session_state.error = data
                else:
                    st.session_state.current_step = item

            while not log_q.empty():
                st.session_state.logs.append(log_q.get_nowait())

        step = st.session_state.current_step
        is_running = st.session_state.phase == "running"
        is_error = bool(st.session_state.error)

        # Barre de progression
        progress = step / len(_STEP_LABELS) if step > 0 else 0
        if not is_running and not is_error:
            progress = 1.0
        progress_label = (
            f"Étape {step}/{len(_STEP_LABELS)} — {_STEP_LABELS[step - 1]}"
            if is_running and 0 < step <= len(_STEP_LABELS)
            else ("Erreur" if is_error else "Analyse terminée")
        )
        st.progress(progress, text=progress_label)

        # Étapes visuelles
        _render_steps(step, not is_running and not is_error, is_error)

        # Journal
        if st.session_state.logs:
            if not is_running and not is_error:
                with st.expander("📋 Voir le journal d'analyse complet"):
                    with st.container(height=320):
                        st.code("\n".join(st.session_state.logs), language=None)
            else:
                st.markdown("**Journal d'analyse**")
                with st.container(height=320):
                    st.code("\n".join(st.session_state.logs), language=None)

        # Auto-refresh pendant le traitement
        if is_running:
            time.sleep(0.35)
            st.rerun()

    # ══════════════════════════════════════════════════════════════════
    # PHASE : rapport final
    # ══════════════════════════════════════════════════════════════════
    if st.session_state.phase == "done":
        st.markdown("---")

        if st.session_state.error:
            st.error("❌ Une erreur est survenue pendant l'analyse.")
            st.code(st.session_state.error)
        elif st.session_state.report:
            st.markdown("## Rapport final")
            _render_report(
                st.session_state.report,
                st.session_state.md_path,
                st.session_state.json_path,
            )

        st.markdown("---")
        if st.button("🔄  Nouvelle analyse", use_container_width=True):
            for k in [
                "phase", "error", "logs", "current_step", "report",
                "md_path", "json_path", "_log_q", "_step_q", "_tmp_zip",
            ]:
                if k in st.session_state:
                    del st.session_state[k]
            st.rerun()


if __name__ == "__main__":
    main()
