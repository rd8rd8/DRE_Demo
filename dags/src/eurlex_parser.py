from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Optional

from bs4 import BeautifulSoup, Tag

from legal_schema import Annex, Article, HierarchyNode, LegalDocument, Relationship


SPACE_RE = re.compile(r"\s+")

LANG_LABELS = {
    "Artigo": "pt",
    "Article": "en",
    "Artículo": "es",
    "Artikel": "de",
    "Article": "fr",
    "Articolo": "it",
}

TYPE_MAP = {
    "DIR": "directive",
    "REG": "regulation",
    "DEC": "decision",
    "RECO": "recommendation",
}

STRUCTURE_PREFIXES = {
    "book": "book",
    "bk": "book",
    "prt": "part",
    "part": "part",
    "ttl": "title",
    "title": "title",
    "cpt": "chapter",
    "chp": "chapter",
    "sct": "section",
    "sec": "section",
    "subsec": "subsection",
    "sbs": "subsection",
    "anx": "annex",
}


def clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = SPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()
    return value or None


def slug_piece(value: str) -> str:
    value = value.lower().replace("º", "").replace("ª", "")
    value = re.sub(r"[^a-z0-9à-ÿ]+", "-", value, flags=re.IGNORECASE)
    return value.strip("-") or "unknown"


def get_meta_by_property(soup: BeautifulSoup, property_: str) -> list[Tag]:
    return soup.find_all("meta", attrs={"property": property_})


def first_meta_content(soup: BeautifulSoup, property_: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"property": property_})
    return clean_text(tag.get("content")) if tag and tag.get("content") else None


def first_meta_resource(soup: BeautifulSoup, property_: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"property": property_})
    return tag.get("resource") if tag else None


def detect_content_language(soup: BeautifulSoup) -> str:
    heading = soup.select_one("p.oj-ti-art")
    if heading:
        text = clean_text(heading.get_text(" ", strip=True)) or ""
        for label, lang in LANG_LABELS.items():
            if text.startswith(label):
                return lang
    # Fall back to HTML lang only if article labels are unavailable.
    return (soup.html.get("lang") if soup.html else None) or "und"


def current_title(soup: BeautifulSoup, language: str) -> Optional[str]:
    for tag in get_meta_by_property(soup, "eli:title"):
        if tag.get("lang") == language:
            return clean_text(tag.get("content"))
    tag = soup.find("meta", attrs={"property": "eli:title"})
    return clean_text(tag.get("content")) if tag else None


def legal_resource_eli(soup: BeautifulSoup) -> Optional[str]:
    tag = soup.find("meta", attrs={"typeof": "eli:LegalResource"})
    return tag.get("about") if tag else None


def celex_id(soup: BeautifulSoup) -> Optional[str]:
    return first_meta_content(soup, "eli:id_local")


def document_number_from_eli(eli: Optional[str]) -> Optional[str]:
    if not eli:
        return None
    match = re.search(r"/(?:dir|reg|dec|reco)/([0-9]{4})/([^/]+)/", eli)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return None


def doc_type_from_resource(resource: Optional[str]) -> Optional[str]:
    if not resource:
        return None
    code = resource.rstrip("/").split("/")[-1]
    return TYPE_MAP.get(code, code.lower())


def normalize_article_number(label: str) -> str:
    value = clean_text(label) or label
    value = re.sub(r"^(Artigo|Article|Artículo|Artikel|Articolo)\s+", "", value, flags=re.IGNORECASE)
    # EUR-Lex renders ordinal markers in forms such as "1. o" / "1.º".
    value = re.sub(r"\s*\.\s*[oºª]\s*$", "", value, flags=re.IGNORECASE)
    value = value.strip(" .ºª")
    return value


def direct_child(el: Tag, selector: str) -> Optional[Tag]:
    for child in el.find_all(recursive=False):
        if child.select_one(selector) is child:
            return child
    return None


def direct_eli_title(el: Tag) -> Optional[str]:
    for child in el.find_all("div", recursive=False):
        if "eli-title" in (child.get("class") or []):
            return clean_text(child.get_text(" ", strip=True))
    return None


def direct_structure_label(el: Tag) -> Optional[str]:
    for child in el.find_all("p", recursive=False):
        classes = " ".join(child.get("class") or [])
        if "oj-ti-section" in classes or "oj-ti-part" in classes or "oj-ti-title" in classes:
            return clean_text(child.get_text(" ", strip=True))
    # Fallback: first short direct paragraph written as a structural heading.
    for child in el.find_all("p", recursive=False):
        text = clean_text(child.get_text(" ", strip=True))
        if text and len(text) < 80 and text.upper() == text:
            return text
    return None


def hierarchy_for_article(article: Tag) -> list[HierarchyNode]:
    nodes: list[HierarchyNode] = []
    ancestors = list(article.parents)
    ancestors.reverse()

    for ancestor in ancestors:
        if not isinstance(ancestor, Tag):
            continue
        id_ = ancestor.get("id") or ""
        if "_" not in id_:
            continue
        prefix = id_.split("_", 1)[0].lower()
        type_ = STRUCTURE_PREFIXES.get(prefix)
        if not type_:
            continue
        label = direct_structure_label(ancestor) or id_
        title = direct_eli_title(ancestor)
        nodes.append(HierarchyNode(type=type_, label=label, title=title))

    return nodes


def extract_tables(container: Tag) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    for table in container.find_all("table"):
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [clean_text(td.get_text(" ", strip=True)) or "" for td in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def parse_eurlex_html(html: str, preferred_language: Optional[str] = None) -> LegalDocument:
    soup = BeautifulSoup(html, "html.parser")

    language = preferred_language or detect_content_language(soup)
    eli = legal_resource_eli(soup)
    celex = celex_id(soup)
    title = current_title(soup, language)
    type_resource = first_meta_resource(soup, "eli:type_document")
    doc_type = doc_type_from_resource(type_resource)
    document_number = document_number_from_eli(eli)

    passed_by = [tag.get("resource") for tag in get_meta_by_property(soup, "eli:passed_by") if tag.get("resource")]

    status_resource = first_meta_resource(soup, "eli:in_force")
    status = status_resource.rstrip("/").split("#")[-1] if status_resource else None

    relation_properties = [
        "eli:changes",
        "eli:cites",
        "eli:consolidated_by",
        "eli:based_on",
        "eli:cited_by",
        "eli:transposes",
        "eli:transposed_by",
    ]
    relationships: list[Relationship] = []
    for prop in relation_properties:
        relation_type = prop.split(":", 1)[1]
        for tag in get_meta_by_property(soup, prop):
            target = tag.get("resource")
            if target:
                relationships.append(Relationship(type=relation_type, target=target))

    document_id = f"EU-{celex}" if celex else f"EU-{slug_piece(document_number or title or 'legal-act').upper()}"

    # Preamble/citations before the enacting terms.
    preamble: list[str] = []
    pbl = soup.select_one("#pbl_1")
    if pbl:
        for child in pbl.find_all(recursive=False):
            child_id = child.get("id") or ""
            if child_id.startswith("rct_"):
                continue
            text = clean_text(child.get_text(" ", strip=True))
            if text:
                preamble.append(text)

    recitals: list[dict[str, Any]] = []
    for recital in soup.select(".eli-subdivision[id^='rct_']"):
        text = clean_text(recital.get_text(" ", strip=True))
        if text:
            recital_number_match = re.match(r"\((\d+)\)", text)
            recitals.append({
                "number": recital_number_match.group(1) if recital_number_match else None,
                "text": text,
            })

    articles: list[Article] = []
    for article in soup.select("div.eli-subdivision[id^='art_']"):
        # Only parse the article as a logical unit; nested references are not separate articles.
        label_tag = None
        for child in article.find_all("p", recursive=False):
            if "oj-ti-art" in (child.get("class") or []):
                label_tag = child
                break
        if not label_tag:
            continue

        label = clean_text(label_tag.get_text(" ", strip=True)) or article.get("id")
        article_number = normalize_article_number(label)
        article_title = direct_eli_title(article)

        paragraphs: list[str] = []
        for child in article.find_all(recursive=False):
            if child is label_tag:
                continue
            if "eli-title" in (child.get("class") or []):
                continue
            text = clean_text(child.get_text(" ", strip=True))
            if text:
                paragraphs.append(text)

        hierarchy = hierarchy_for_article(article)
        hierarchy_slug = "-".join(slug_piece(h.label) for h in hierarchy) or "root"
        article_id = f"{document_id}:{hierarchy_slug}:article-{slug_piece(article_number)}"

        articles.append(Article(
            id=article_id,
            number=article_number,
            label=label,
            title=article_title,
            text="\n".join(paragraphs),
            paragraphs=paragraphs,
            hierarchy=hierarchy,
            source_anchor=article.get("id"),
        ))

    annexes: list[Annex] = []
    for annex in soup.select("div.eli-container[id^='anx_']"):
        direct_ps = annex.find_all("p", recursive=False)
        label = clean_text(direct_ps[0].get_text(" ", strip=True)) if direct_ps else annex.get("id")
        title_text = clean_text(direct_ps[1].get_text(" ", strip=True)) if len(direct_ps) > 1 else None

        text_parts: list[str] = []
        for child in annex.find_all(recursive=False):
            if child in direct_ps[:2]:
                continue
            text = clean_text(child.get_text(" ", strip=True))
            if text:
                text_parts.append(text)

        annexes.append(Annex(
            id=f"{document_id}:annex-{slug_piece(label or annex.get('id') or 'annex')}",
            label=label or annex.get("id") or "Annex",
            title=title_text,
            reference_note=None,
            text="\n".join(text_parts),
            tables=extract_tables(annex),
        ))

    return LegalDocument(
        schema_version="1.0",
        source={
            "name": "EUR-Lex",
            "adapter": "eurlex",
            "url": eli,
            "jurisdiction": "EU",
        },
        document={
            "document_id": document_id,
            "title": title,
            "document_type": doc_type,
            "document_number": document_number,
            "language": language,
            "jurisdiction": "EU",
            "document_date": first_meta_content(soup, "eli:date_document"),
            "publication_date": first_meta_content(soup, "eli:date_publication"),
            "entry_into_force_date": first_meta_content(soup, "eli:first_date_entry_in_force"),
            "status": status,
            "summary": None,
            "identifiers": {
                "eli": eli,
                "celex": celex,
            },
            "responsible_bodies": [{"id": x, "name": None} for x in passed_by],
        },
        preamble=preamble,
        recitals=recitals,
        articles=articles,
        annexes=annexes,
        relationships=relationships,
    )


def parse_file(input_path: Path, output_path: Optional[Path] = None, language: Optional[str] = None) -> dict[str, Any]:
    document = parse_eurlex_html(input_path.read_text(encoding="utf-8", errors="replace"), preferred_language=language)
    data = document.to_dict()
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

INPUT_PATH = Path(
    "data/raw/EU-DIR-2022-2555/original.html"
)

OUTPUT_PATH = Path(
    "data/processed/EU-DIR-2022-2555.json"
)

PREFERRED_LANGUAGE = "pt"


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main() -> None:

    data = parse_file(
        INPUT_PATH,
        OUTPUT_PATH,
        PREFERRED_LANGUAGE
    )

    print("\nEUR-Lex document parsed successfully.\n")

    print(json.dumps(
        {
            "document_id": data["document"]["document_id"],
            "title": data["document"]["title"],
            "articles": len(data["articles"]),
            "annexes": len(data["annexes"]),
            "recitals": len(data["recitals"]),
            "relationships": len(data["relationships"]),
            "output": str(OUTPUT_PATH)
        },
        ensure_ascii=False,
        indent=2
    ))


if __name__ == "__main__":
    main()
