from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

HTML_URL = (
    "https://diariodarepublica.pt/dr/detalhe/"
    "decreto-lei/125-2025-962603401"
)

PDF_URL = (
    "https://files.diariodarepublica.pt/"
    "1s/2025/12/23400/0000400068.pdf"
)

OUTPUT_DIR = Path("data/raw/PT-DL-125-2025")

HEADERS = {
    "User-Agent": (
        "RegulatoryDataPoC/0.1 "
        "(educational regulatory data ingestion project)"
    )
}


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def calculate_sha256(content: bytes) -> str:
    """
    Calculate SHA256 hash of content.

    Useful for detecting changes between different
    versions of the same document.
    """
    return hashlib.sha256(content).hexdigest()


def download(url: str) -> requests.Response:
    """
    Generic HTTP download function.

    Used for resources that do not require JavaScript,
    such as the PDF.
    """
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    return response


# ---------------------------------------------------------
# HTML - PLAYWRIGHT
# ---------------------------------------------------------

def download_html():
    """
    Download the rendered HTML using Playwright.

    The Diário da República website is built with
    React / OutSystems, so a normal requests.get()
    only returns the initial application shell.

    Playwright launches a browser and executes the
    JavaScript before retrieving the HTML.
    """

    print("Downloading and rendering HTML with Playwright...")

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page()

        page.goto(
            HTML_URL,
            wait_until="domcontentloaded",
            timeout=60_000
        )

        # Wait until actual document content is rendered.
        page.get_by_text(
            "Decreto-Lei n.º 125/2025",
            exact=False
        ).first.wait_for(
            timeout=60_000
        )

        print("Page successfully rendered.")

        # HTML after JavaScript execution
        html = page.content()

        browser.close()

    # Convert string to bytes so we can calculate
    # the SHA256 consistently.
    html_bytes = html.encode("utf-8")

    output_file = OUTPUT_DIR / "original.html"

    output_file.write_text(
        html,
        encoding="utf-8"
    )

    print(f"HTML saved to: {output_file}")

    return {
        "url": HTML_URL,
        "extraction_method": "playwright",
        "size_bytes": len(html_bytes),
        "sha256": calculate_sha256(html_bytes)
    }


# ---------------------------------------------------------
# PDF - REQUESTS
# ---------------------------------------------------------

def download_pdf():
    """
    Download the official PDF.

    The PDF is a static resource, so browser automation
    is unnecessary. A normal HTTP request is preferable.
    """

    print("Downloading PDF...")

    response = download(PDF_URL)

    pdf = response.content

    # Basic validation:
    # PDF files should start with the PDF signature.
    if not pdf.startswith(b"%PDF"):
        raise ValueError(
            "Downloaded content does not appear to be a PDF."
        )

    output_file = OUTPUT_DIR / "original.pdf"

    output_file.write_bytes(pdf)

    print(f"PDF saved to: {output_file}")

    return {
        "url": PDF_URL,
        "extraction_method": "requests",
        "content_type": response.headers.get("Content-Type"),
        "size_bytes": len(pdf),
        "sha256": calculate_sha256(pdf)
    }


# ---------------------------------------------------------
# HTML INSPECTION
# ---------------------------------------------------------

def inspect_html():
    """
    Small test to verify that the rendered HTML
    actually contains the document content.
    """

    html_file = OUTPUT_DIR / "original.html"

    html = html_file.read_text(
        encoding="utf-8",
        errors="replace"
    )

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # Print page title
    if soup.title:
        print(
            "HTML page title:",
            soup.title.get_text(
                " ",
                strip=True
            )
        )

    # Print a sample of the rendered text
    body = soup.find("body")

    if body:

        text = body.get_text(
            "\n",
            strip=True
        )

        print("\n--- HTML CONTENT SAMPLE ---\n")

        print(text[:1500])

        print("\n---------------------------\n")


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    # Dynamic HTML
    html_metadata = download_html()

    # Static PDF
    pdf_metadata = download_pdf()

    metadata = {

        "document_id": "PT-DL-125-2025",

        "source": "Diário da República",

        "jurisdiction": "PT",

        "document_type": "Decreto-Lei",

        "document_number": "125/2025",

        "retrieved_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "html": html_metadata,

        "pdf": pdf_metadata
    }

    metadata_file = (
        OUTPUT_DIR /
        "metadata.json"
    )

    metadata_file.write_text(
        json.dumps(
            metadata,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    inspect_html()

    print(
        f"Metadata saved to: "
        f"{metadata_file}"
    )


if __name__ == "__main__":
    main()