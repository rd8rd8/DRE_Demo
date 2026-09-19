"""Streamlit frontend for the legislation extraction POC.

Run from the project root with:
    streamlit run frontend.py
"""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import json
import traceback

import streamlit as st

from dags.src.engine import main, resolve_dre


st.set_page_config(
    page_title="Legislation Extractor",
    page_icon="⚖️",
    layout="wide",
)

st.title("⚖️ Legislation Extractor")
st.write(
    "Introduz uma ligação do Diário da República para descarregar o HTML "
    "e convertê-lo num JSON estruturado."
)


with st.form("legislation_form"):
    url = st.text_input(
        "Ligação da legislação",
        placeholder=(
            "https://diariodarepublica.pt/dr/detalhe/"
            "decreto-lei/125-2025-962603401"
        ),
    )
    submitted = st.form_submit_button(
        "Extrair e organizar",
        type="primary",
        use_container_width=True,
    )


if submitted:
    st.session_state.pop("extraction_result", None)
    st.session_state.pop("extraction_error", None)

    clean_url = url.strip()

    if not clean_url:
        st.session_state["extraction_error"] = {
            "message": "Introduz uma ligação antes de iniciar.",
            "details": "",
        }
    else:
        log_buffer = StringIO()

        try:
            with st.spinner("A descarregar e a processar a legislação..."):
                with redirect_stdout(log_buffer):
                    result = main(clean_url)

                paths = resolve_dre(clean_url)
                html_path = paths["OUTPUT_DIR"] / "original.html"
                json_path = paths["PARSED_OUTPUT_PATH"]

                html = html_path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
                document = result["document"]
                json_text = json.dumps(
                    document,
                    ensure_ascii=False,
                    indent=2,
                )

                st.session_state["extraction_result"] = {
                    "url": clean_url,
                    "html": html,
                    "html_name": f"{paths['OUTPUT_DIR'].name}.html",
                    "json": document,
                    "json_text": json_text,
                    "json_name": Path(json_path).name,
                    "log": log_buffer.getvalue(),
                }

        except Exception as error:
            st.session_state["extraction_error"] = {
                "message": str(error),
                "details": traceback.format_exc(),
            }


if "extraction_error" in st.session_state:
    error = st.session_state["extraction_error"]
    st.error(f"Não foi possível processar a legislação: {error['message']}")

    if error["details"]:
        with st.expander("Detalhes técnicos"):
            st.code(error["details"], language="text")


if "extraction_result" in st.session_state:
    extraction = st.session_state["extraction_result"]
    document = extraction["json"]

    st.success("Download e parsing concluídos com sucesso.")

    title = document.get("document", {}).get("title") or "Sem título"
    st.subheader(title)

    

    html_tab, json_tab = st.tabs(
        ["HTML extraído", "JSON organizado"]
    )

    with html_tab:
        st.download_button(
            "Descarregar HTML",
            data=extraction["html"],
            file_name=extraction["html_name"],
            mime="text/html",
        )
        st.code(
            extraction["html"],
            language="html",
            line_numbers=True,
            wrap_lines=True,
            height=480,
        )

    with json_tab:
        st.download_button(
            "Descarregar JSON",
            data=extraction["json_text"],
            file_name=extraction["json_name"],
            mime="application/json",
        )
        st.json(extraction["json"], expanded=2)

    with st.expander("Detalhes do processamento"):
        st.code(extraction["log"], language="text")
