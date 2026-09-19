from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

import asyncio
import sys
import re
import json
import requests

from bs4 import BeautifulSoup
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError
)
from concurrent.futures import ThreadPoolExecutor

from dags.src.download_dre import main_dre



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

    raise ValueError(
        f"Unsupported source: {domain}"
    )


# =========================================================
# DRE - GET RENDERED HTML
# =========================================================

async def _get_dre_html_async(url: str) -> str:
    """
    Open a DRE page with Playwright and return the rendered HTML.

    Important:
    DRE injects part of its legal metadata after the main page content
    is already visible. Therefore we wait not only for the React content,
    but also for the PDF ELI metadata when available.
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

            # Wait until the legislation text is actually rendered.
            await page.wait_for_function(
                """
                () => {
                    const container =
                        document.querySelector('#reactContainer');

                    return container &&
                           container.innerText.length > 500;
                }
                """,
                timeout=60_000
            )

            # DRE injects the ELI format metadata later.
            # Do not fail here if it is not injected: resolve_dre()
            # has a fallback that constructs the official ELI PDF URL
            # from the document metadata.
            try:
                await page.wait_for_function(
                    """
                    () => {
                        return Array.from(
                            document.querySelectorAll('[about]')
                        ).some(el => {
                            const value =
                                el.getAttribute('about') || '';

                            return (
                                value.includes('data.dre.pt/eli/') &&
                                /\\/pt\\/pdf\\/?$/.test(value)
                            );
                        });
                    }
                    """,
                    timeout=15_000
                )

            except PlaywrightTimeoutError:
                pass

            return await page.content()

        finally:

            await browser.close()


def get_dre_html(url: str) -> str:
    """
    Synchronous wrapper around Playwright.

    Runs in a separate thread so that main(...) can be called
    directly inside VS Code / Jupyter notebooks.
    """

    def worker():

        if sys.platform.startswith("win"):

            loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(loop)

            try:

                return loop.run_until_complete(
                    _get_dre_html_async(url)
                )

            finally:

                loop.close()

        return asyncio.run(
            _get_dre_html_async(url)
        )

    with ThreadPoolExecutor(
        max_workers=1
    ) as executor:

        return executor.submit(
            worker
        ).result()


# =========================================================
# DRE HELPERS
# =========================================================

def _find_dre_pdf_in_eli_metadata(
    soup: BeautifulSoup
) -> str | None:
    """
    Find the official PDF representation in DRE ELI metadata.
    """

    # Preferred structure.
    pdf_container = soup.find(
        id="eli-legal-format-pdf"
    )

    if pdf_container:

        pdf_format = pdf_container.find(
            attrs={
                "typeof": "eli:Format",
                "about": True
            }
        )

        if pdf_format:

            value = pdf_format.get(
                "about"
            )

            if value:
                return value

    # Generic fallback: independent of the surrounding container ID.
    for tag in soup.find_all(
        attrs={
            "about": True
        }
    ):

        value = tag.get(
            "about"
        )

        if (
            isinstance(value, str)
            and "data.dre.pt/eli/" in value
            and re.search(
                r"/pt/pdf/?$",
                value
            )
        ):

            return value

    return None


def _get_dre_publication_date(
    soup: BeautifulSoup
) -> str | None:
    """
    Read datePublished from DRE JSON-LD metadata.
    """

    for script in soup.find_all(
        "script",
        attrs={
            "type": "application/ld+json"
        }
    ):

        raw = script.string or script.get_text(
            strip=True
        )

        if not raw:
            continue

        try:
            data = json.loads(raw)

        except json.JSONDecodeError:
            continue

        documents = (
            data
            if isinstance(data, list)
            else [data]
        )

        for document in documents:

            if not isinstance(
                document,
                dict
            ):
                continue

            date_published = document.get(
                "datePublished"
            )

            if date_published:
                return str(
                    date_published
                )[:10]

    # Secondary fallback for the visible DRE publication date.
    text = soup.get_text(
        " ",
        strip=True
    )

    match = re.search(
        r"Data de Publicação:\s*"
        r"(\d{4}-\d{2}-\d{2})",
        text
    )

    if match:
        return match.group(1)

    return None


def _build_dre_eli_pdf_url(
    document_type: str,
    number: str,
    year: str,
    publication_date: str
) -> str | None:
    """
    Construct the official DRE ELI PDF representation.

    Used only if the ELI PDF metadata was not present
    in the rendered DOM.
    """

    eli_type_map = {
        "decreto-lei": "dec-lei",
        "lei": "lei",
        "portaria": "port",
        "decreto-regulamentar": "dec-reg",
        "decreto": "dec",
        "resolucao-do-conselho-de-ministros": "resolconsmin",
        "resolucao-da-assembleia-da-republica": "resolassembleia"
    }

    eli_type = eli_type_map.get(
        document_type
    )

    if not eli_type:
        return None

    date_match = re.fullmatch(
        r"(\d{4})-(\d{2})-(\d{2})",
        publication_date
    )

    if not date_match:
        return None

    date_year = date_match.group(1)
    month = date_match.group(2)
    day = date_match.group(3)

    # The year in the publication date must agree with
    # the year contained in the document URL.
    if date_year != year:
        return None

    eli_number = number.lower()

    return (
        "https://data.dre.pt/eli/"
        f"{eli_type}/"
        f"{eli_number}/"
        f"{year}/"
        f"{month}/"
        f"{day}/"
        "p/dre/pt/pdf"
    )


# =========================================================
# DRE
# =========================================================

def resolve_dre(url: str):
    """
    Convert a DRE URL into the exact arguments expected by:

        main_dre(
            HTML_URL,
            OUTPUT_DIR
        )

    main_dre itself is not modified.
    """

    parsed = urlparse(
        url
    )

    parts = [
        part
        for part in parsed.path.split("/")
        if part
    ]

    # Expected example:
    #
    # /dr/detalhe/decreto-lei/125-2025-962603401

    try:

        detail_index = parts.index(
            "detalhe"
        )

        document_type = parts[
            detail_index + 1
        ]

        document_slug = parts[
            detail_index + 2
        ]

    except (
        ValueError,
        IndexError
    ):

        raise ValueError(
            "Could not understand DRE URL."
        )

    # Split from the right so document numbers containing
    # a hyphen can also be handled.
    try:

        number, year, _internal_id = (
            document_slug.rsplit(
                "-",
                2
            )
        )

    except ValueError:

        raise ValueError(
            "Could not extract number/year "
            "from DRE URL."
        )

    if (
        not year.isdigit()
        or len(year) != 4
    ):

        raise ValueError(
            "Could not identify the year "
            "in the DRE URL."
        )

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
        f"data/raw/"
        f"PT-{type_code}-"
        f"{number.upper()}-{year}"
    )

    print(
        "Resolving DRE document..."
    )

    # -----------------------------------------------------
    # Render DRE page
    # -----------------------------------------------------

    html = get_dre_html(
        url
    )

    soup = BeautifulSoup(
        html,
        "html.parser"
    )





    return {
        "HTML_URL": url,
        "OUTPUT_DIR": OUTPUT_DIR
    }



# =========================================================
# MAIN ENGINE
# =========================================================

def main(url: str):
    """
    Main entry point.

    Only a legislation URL is required.
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
            f"OUTPUT_DIR: "
            f"{args['OUTPUT_DIR']}"
        )
        print()
        return main_dre(
            HTML_URL=args["HTML_URL"],
            OUTPUT_DIR=args["OUTPUT_DIR"]
        )
 
    
