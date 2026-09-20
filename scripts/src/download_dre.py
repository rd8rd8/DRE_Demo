from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import asyncio
import hashlib
import json
import sys

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def calculate_sha256(content: bytes) -> str:
    """Calculate a SHA-256 hash for the downloaded content."""

    return hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------
# HTML - PLAYWRIGHT
# ---------------------------------------------------------

def download_html(html_url: str, output_dir: Path) -> dict:
    """Render a DRE page and save its complete HTML."""

    print("Downloading and rendering HTML with Playwright...")

    with sync_playwright() as playwright:

        browser = playwright.chromium.launch(
            headless=True
        )

        try:
            page = browser.new_page()

            page.goto(
                html_url,
                wait_until="domcontentloaded",
                timeout=60_000,
            )

            # -------------------------------------------------
            # WAIT FOR THE ACTUAL LEGAL CONTENT
            # -------------------------------------------------

            page.wait_for_function(
                """
                () => {

                    const selectors = [
                        "[id$='InjectHTMLWrapper']",
                        ".texto.int-links",
                        "#ConteudoDiploma",
                        "[data-block='Legislacao_Conteudos.Conteudo_Det_Diploma']"
                    ];

                    for (const selector of selectors) {

                        const elements =
                            document.querySelectorAll(selector);

                        for (const element of elements) {

                            const text =
                                (element.innerText || "").trim();

                            const paragraphs =
                                element.querySelectorAll("p");

                            if (
                                paragraphs.length >= 3 &&
                                /Artigo\\s+\\d+/i.test(text)
                            ) {
                                return true;
                            }
                        }
                    }

                    return false;
                }
                """,
                timeout=60_000,
            )

            # Only now is the page considered ready
            html = page.content()

        finally:
            browser.close()

    html_bytes = html.encode("utf-8")

    output_path = (
        output_dir
        / "original.html"
    )

    output_path.write_text(
        html,
        encoding="utf-8",
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
        ),
    }


def run_download_html(html_url: str, output_dir: Path) -> dict:
    """Run synchronous Playwright safely from a VS Code/Jupyter cell."""

    def worker() -> dict:
        # On Windows, Chromium is a subprocess. The Proactor event loop is
        # required because the Selector event loop cannot create subprocesses.
        if sys.platform == "win32":
            previous_policy = asyncio.get_event_loop_policy()

            try:
                asyncio.set_event_loop_policy(
                    asyncio.WindowsProactorEventLoopPolicy()
                )
                return download_html(html_url, output_dir)
            finally:
                asyncio.set_event_loop_policy(previous_policy)

        return download_html(html_url, output_dir)

    # A notebook already has a running event loop, so Playwright runs in one
    # separate worker thread. The POC itself remains synchronous.
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(worker).result()


# ---------------------------------------------------------
# HTML INSPECTION
# ---------------------------------------------------------

def inspect_html(output_dir: Path) -> None:
    """Print the page title and a short sample of the downloaded text."""

    html = (output_dir / "original.html").read_text(
        encoding="utf-8",
        errors="replace",
    )
    soup = BeautifulSoup(html, "html.parser")

    if soup.title:
        print("HTML page title:", soup.title.get_text(" ", strip=True))

    if soup.body:
        text = soup.body.get_text("\n", strip=True)
        print("\n--- HTML CONTENT SAMPLE ---\n")
        print(text[:1500])
        print("\n---------------------------\n")


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main_dre(HTML_URL: str, OUTPUT_DIR: Path) -> dict:
    """Download one DRE page and write its HTML and metadata."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    html_metadata = run_download_html(HTML_URL, OUTPUT_DIR)
    metadata = {"html": html_metadata}

    metadata_path = OUTPUT_DIR / "metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    inspect_html(OUTPUT_DIR)
    print("Download completed.")

    return metadata
