from pathlib import Path
import hashlib
import json
import asyncio
import sys
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from concurrent.futures import ThreadPoolExecutor


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36"
    )
}


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def calculate_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def download(url: str) -> requests.Response:

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

def download_html(
    html_url: str,
    output_dir: Path
):

    print(
        "Downloading and rendering HTML "
        "with Playwright..."
    )

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page()

        page.goto(
            html_url,
            wait_until="domcontentloaded",
            timeout=60_000
        )

        page.wait_for_function(
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

        html = page.content()

        browser.close()

    html_bytes = html.encode(
        "utf-8"
    )

    output_path = (
        output_dir /
        "original.html"
    )

    output_path.write_text(
        html,
        encoding="utf-8"
    )

    print(
        f"HTML saved to: {output_path}"
    )

    return {
        "url": html_url,
        "extraction_method": "playwright",
        "size_bytes": len(html_bytes),
        "sha256": calculate_sha256(
            html_bytes
        )
    }


def run_download_html(
    html_url: str,
    output_dir: Path
):
    """
    Runs Playwright in a separate thread.

    On Windows, Playwright requires a ProactorEventLoop
    because it launches the browser as a subprocess.
    """

    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    with ThreadPoolExecutor(
        max_workers=1
    ) as executor:

        future = executor.submit(
            download_html,
            html_url,
            output_dir
        )

        return future.result()



# ---------------------------------------------------------
# HTML INSPECTION
# ---------------------------------------------------------

def inspect_html(
    output_dir: Path
):

    html_file = (
        output_dir /
        "original.html"
    )

    html = html_file.read_text(
        encoding="utf-8",
        errors="replace"
    )

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    if soup.title:

        print(
            "HTML page title:",
            soup.title.get_text(
                " ",
                strip=True
            )
        )

    body = soup.find("body")

    if body:

        text = body.get_text(
            "\n",
            strip=True
        )

        print(
            "\n--- HTML CONTENT SAMPLE ---\n"
        )

        print(
            text[:1500]
        )

        print(
            "\n---------------------------\n"
        )


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main_dre(
    HTML_URL,
    OUTPUT_DIR
):

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    # HTML
    html_metadata = (
        run_download_html(
            HTML_URL,
            OUTPUT_DIR
        )
    )

    

    metadata = {
        "html": html_metadata
    }

    metadata_path = (
        OUTPUT_DIR /
        "metadata.json"
    )

    metadata_path.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    inspect_html(
        OUTPUT_DIR
    )

    print(
        "Download completed."
    )

    return metadata