from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote, urljoin

import asyncio
import sys
import re
import requests

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from concurrent.futures import ThreadPoolExecutor

from dags.src.download_dre import main_dre
from dags.src.download_eurlex import main_eurlex


# =========================================================
# DETECT SOURCE
# =========================================================

def detect_source(url: str) -> str:
    """
    Detect whether the URL belongs to DRE or EUR-Lex.
    """

    domain = urlparse(url).netloc.lower()

    if "diariodarepublica.pt" in domain:
        return "dre"

    if "eur-lex.europa.eu" in domain:
        return "eurlex"

    raise ValueError(
        f"Unsupported source: {domain}"
    )


# =========================================================
# DRE - GET RENDERED HTML
# =========================================================

async def _get_dre_html_async(
    url: str
) -> str:
    """
    Open a DRE page with Playwright and return
    the rendered HTML.
    """

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        try:

            page = await browser.new_page()

            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60_000
            )

            await page.wait_for_function(
                """
                () => {
                    const container =
                        document.querySelector(
                            '#reactContainer'
                        );

                    return container &&
                           container.innerText.length > 500;
                }
                """,
                timeout=60_000
            )

            html = await page.content()

            return html

        finally:

            await browser.close()


def get_dre_html(
    url: str
) -> str:
    """
    Synchronous wrapper around the asynchronous
    Playwright function.

    Uses a separate thread so it also works
    inside a VS Code / Jupyter notebook.
    """

    def worker():

        # Windows requires a Proactor event loop
        # because Playwright launches subprocesses.
        if sys.platform.startswith("win"):

            loop = asyncio.ProactorEventLoop()

            asyncio.set_event_loop(
                loop
            )

            try:

                return loop.run_until_complete(
                    _get_dre_html_async(url)
                )

            finally:

                loop.close()

        # Linux / macOS
        return asyncio.run(
            _get_dre_html_async(url)
        )

    with ThreadPoolExecutor(
        max_workers=1
    ) as executor:

        future = executor.submit(
            worker
        )

        return future.result()


# =========================================================
# DRE
# =========================================================

def resolve_dre(
    url: str
):
    """
    Convert a DRE URL into the arguments required
    by main_dre():

        HTML_URL
        PDF_URL
        OUTPUT_DIR
    """

    path = urlparse(url).path
    print(path)
    # Example:
    #
    # /dr/detalhe/decreto-lei/125-2025-962603401

    match = re.search(
        r"/detalhe/([^/]+)/(\d+)-(\d{4})-",
        path
    )

    if not match:

        raise ValueError(
            "Could not understand DRE URL."
        )

    document_type = match.group(1)
    number = match.group(2)
    year = match.group(3)

    # Friendly document codes
    type_map = {
        "decreto-lei": "DL",
        "lei": "LEI",
        "portaria": "PORTARIA",
        "decreto-regulamentar": "DR"
    }

    type_code = type_map.get(
        document_type,
        document_type.upper()
    )

    OUTPUT_DIR = Path(
        f"data/raw/PT-{type_code}-{number}-{year}"
    )

    print(
        "Resolving DRE document..."
    )

    # -----------------------------------------------------
    # Get rendered HTML
    # -----------------------------------------------------

    html = get_dre_html(
        url
    )

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # -----------------------------------------------------
    # Find PDF
    # -----------------------------------------------------

    PDF_URL = None

    for link in soup.find_all(
        "a",
        href=True
    ):

        href = urljoin(
            url,
            link["href"]
        )

        if ".pdf" in href.lower():

            PDF_URL = href

            break

    if not PDF_URL:

        raise ValueError(
            "Could not find DRE PDF."
        )

    return {
        "HTML_URL": url,
        "PDF_URL": PDF_URL,
        "OUTPUT_DIR": OUTPUT_DIR
    }


# =========================================================
# EUR-LEX
# =========================================================

def resolve_eurlex(
    url: str
):
    """
    Convert a EUR-Lex URL into the arguments
    required by main_eurlex():

        URL
        OUTPUT_DIR
        CELEX
    """

    parsed = urlparse(
        url
    )

    query = parse_qs(
        parsed.query
    )

    # -----------------------------------------------------
    # Try CELEX from URL
    # -----------------------------------------------------

    uri = query.get(
        "uri",
        [None]
    )[0]

    CELEX = None

    if uri:

        uri = unquote(
            uri
        )

        if uri.upper().startswith(
            "CELEX:"
        ):

            CELEX = uri.split(
                ":",
                1
            )[1]

    # -----------------------------------------------------
    # If CELEX isn't in URL, read EUR-Lex metadata
    # -----------------------------------------------------

    if not CELEX:

        print(
            "CELEX not found in URL. "
            "Reading EUR-Lex metadata..."
        )

        response = requests.get(
            url,
            timeout=30
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        celex_tag = soup.find(
            "meta",
            attrs={
                "property": "eli:id_local"
            }
        )

        if not celex_tag:

            raise ValueError(
                "Could not identify CELEX."
            )

        CELEX = celex_tag.get(
            "content"
        )

    if not CELEX:

        raise ValueError(
            "CELEX identifier is empty."
        )

    # -----------------------------------------------------
    # Output directory
    # -----------------------------------------------------

    OUTPUT_DIR = Path(
        f"data/raw/EU-{CELEX}"
    )

    return {
        "URL": url,
        "OUTPUT_DIR": OUTPUT_DIR,
        "CELEX": CELEX
    }


# =========================================================
# MAIN ENGINE
# =========================================================

def main(
    url: str
):
    """
    Main entry point.

    The user only provides the legislation URL.
    """

    source = detect_source(
        url
    )

    print(
        f"Source detected: {source}"
    )

    # =====================================================
    # DRE
    # =====================================================

    if source == "dre":

        args = resolve_dre(
            url
        )

        print(
            "\nArguments discovered:"
        )

        print(
            f"HTML_URL: "
            f"{args['HTML_URL']}"
        )

        print(
            f"PDF_URL: "
            f"{args['PDF_URL']}"
        )

        print(
            f"OUTPUT_DIR: "
            f"{args['OUTPUT_DIR']}"
        )

        return main_dre(
            HTML_URL=args["HTML_URL"],
            PDF_URL=args["PDF_URL"],
            OUTPUT_DIR=args["OUTPUT_DIR"]
        )

    # =====================================================
    # EUR-LEX
    # =====================================================

    if source == "eurlex":

        args = resolve_eurlex(
            url
        )

        print(
            "\nArguments discovered:"
        )

        print(
            f"URL: "
            f"{args['URL']}"
        )

        print(
            f"CELEX: "
            f"{args['CELEX']}"
        )

        print(
            f"OUTPUT_DIR: "
            f"{args['OUTPUT_DIR']}"
        )

        return main_eurlex(
            URL=args["URL"],
            OUTPUT_DIR=args["OUTPUT_DIR"],
            CELEX=args["CELEX"]
        )