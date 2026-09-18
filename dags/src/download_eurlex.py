from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re

from playwright.sync_api import sync_playwright

# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def calculate_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def clean_lines(text: str) -> list[str]:
    """
    Convert body text into a cleaner list of lines.
    """

    return [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------
# EXTRACTION HELPERS
# ---------------------------------------------------------

def extract_title(lines: list[str]) -> str | None:
    """
    Find the Portuguese NIS2 title.
    """

    for line in lines:

        if "Diretiva (UE) 2022/2555" in line:
            return line

    return None


def extract_publication_date(text: str) -> str | None:
    """
    Extract publication date.

    EUR-Lex displays:
    27/12/2022
    """

    match = re.search(
        r"\b27/12/2022\b",
        text
    )

    if match:
        return match.group()

    return None


def extract_eli(text: str) -> str | None:
    """
    Extract European Legislation Identifier.
    """

    match = re.search(
        r"https?://data\.europa\.eu/eli/"
        r"dir/2022/2555/oj",
        text
    )

    if match:
        return match.group()

    return None


# ---------------------------------------------------------
# EUR-LEX DOWNLOAD
# ---------------------------------------------------------

def download_eurlex():

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    print("Opening EUR-Lex...")

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page()

        page.goto(
            URL,
            wait_until="domcontentloaded",
            timeout=60_000
        )

        # Wait until the actual NIS2 document is visible
        page.get_by_text(
            "Diretiva (UE) 2022/2555",
            exact=False
        ).first.wait_for(
            timeout=60_000
        )

        print("EUR-Lex document loaded.")

        # -------------------------------------------------
        # RAW HTML
        # -------------------------------------------------

        html = page.content()

        html_bytes = html.encode(
            "utf-8"
        )

        html_file = (
            OUTPUT_DIR /
            "original.html"
        )

        html_file.write_text(
            html,
            encoding="utf-8"
        )

        # -------------------------------------------------
        # RAW VISIBLE TEXT
        # -------------------------------------------------

        text = page.locator(
            "body"
        ).inner_text()

        text_file = (
            OUTPUT_DIR /
            "original.txt"
        )

        text_file.write_text(
            text,
            encoding="utf-8"
        )

        browser.close()

    print(
        f"HTML saved to: {html_file}"
    )

    print(
        f"Text saved to: {text_file}"
    )

    return html, text


# ---------------------------------------------------------
# PARSE DOCUMENT
# ---------------------------------------------------------

def parse_document(
    html: str,
    text: str
):

    lines = clean_lines(text)

    title = extract_title(lines)

    publication_date = (
        extract_publication_date(text)
    )

    eli = extract_eli(text)

    document = {

        "document_id": "EU-DIR-2022-2555",

        "celex": CELEX,

        "eli": eli,

        "source": "EUR-Lex",

        "jurisdiction": "EU",

        "document_type": "Directive",

        "document_number": "2022/2555",

        "language": "pt",

        "title": title,

        "publication_date":
            publication_date,

        "source_url": URL,

        "retrieved_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "html_sha256":
            calculate_sha256(
                html.encode("utf-8")
            )
    }

    return document


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main_eurlex(URL,OUTPUT_DIR,CELEX):

    html, text = download_eurlex()

    document = parse_document(
        html,
        text
    )

    metadata_file = (
        OUTPUT_DIR /
        "metadata.json"
    )

    metadata_file.write_text(
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print("\n--- EXTRACTED DOCUMENT ---\n")

    print(
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2
        )
    )

    print(
        f"\nMetadata saved to: "
        f"{metadata_file}"
    )


