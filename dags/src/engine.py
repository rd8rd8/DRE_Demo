from pathlib import Path
from urllib.parse import urlparse

from dags.src.download_dre import main_dre
from dags.src.dre_parser import main_dre_parser


# =========================================================
# DETECT SOURCE
# =========================================================

def detect_source(url: str) -> str:
    """Identify the legislation website from its URL."""

    domain = urlparse(url).netloc.lower()

    if domain == "diariodarepublica.pt" or domain.endswith(
        ".diariodarepublica.pt"
    ):
        return "dre"

    raise ValueError(f"Unsupported source: {domain or url}")


# =========================================================
# DRE
# =========================================================

def resolve_dre(url: str) -> dict:
    """Build the arguments needed by main_dre from a DRE URL."""

    parts = [part for part in urlparse(url).path.split("/") if part]

    try:
        detail_index = parts.index("detalhe")
        document_type = parts[detail_index + 1]
        document_slug = parts[detail_index + 2]
        number, year, _internal_id = document_slug.rsplit("-", 2)
    except (ValueError, IndexError) as error:
        raise ValueError("Could not understand DRE URL.") from error

    if len(year) != 4 or not year.isdigit():
        raise ValueError("Could not identify the year in the DRE URL.")

    type_codes = {
        "decreto-lei": "DL",
        "lei": "LEI",
        "portaria": "PORTARIA",
        "decreto-regulamentar": "DR",
    }
    type_code = type_codes.get(document_type, document_type.upper())

    document_id = f"PT-{type_code}-{number.upper()}-{year}"
    output_dir = Path("data/raw") / document_id
    parsed_output_path = Path("data/processed") / f"{document_id}.json"

    return {
        "HTML_URL": url,
        "OUTPUT_DIR": output_dir,
        "PARSED_OUTPUT_PATH": parsed_output_path,
    }


# =========================================================
# MAIN ENGINE
# =========================================================

def main(url: str):
    #print(python.__version__)
    """Download and parse legislation from the source indicated by the URL."""

    source = detect_source(url)
    print(f"Source detected: {source}")

    if source == "dre":
        args = resolve_dre(url)

        print("Arguments discovered:")
        print(f"HTML_URL: {args['HTML_URL']}")
        print(f"OUTPUT_DIR: {args['OUTPUT_DIR']}")
        print(f"PARSED_OUTPUT_PATH: {args['PARSED_OUTPUT_PATH']}\n")

        download_metadata = main_dre(
            HTML_URL=args["HTML_URL"],
            OUTPUT_DIR=args["OUTPUT_DIR"],
        )

        html_path = args["OUTPUT_DIR"] / "original.html"

        if not html_path.exists():
            raise FileNotFoundError(
                f"The DRE download did not create the expected file: {html_path}"
            )

        print("Starting DRE parser...")

        parsed_document = main_dre_parser(
            input_path=html_path,
            output_path=args["PARSED_OUTPUT_PATH"],
        )

        return {
            "download": download_metadata,
            "document": parsed_document,
        }
