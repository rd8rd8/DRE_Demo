from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

from bs4 import BeautifulSoup, Tag

try:
    from dags.src.legal_schema import (
        Annex,
        Article,
        HierarchyNode,
        LegalDocument,
        Relationship,
    )
except ModuleNotFoundError:
    # Allows direct execution when dags/src is the active Python path.
    from legal_schema import (  # type: ignore[no-redef]
        Annex,
        Article,
        HierarchyNode,
        LegalDocument,
        Relationship,
    )


SPACE_RE = re.compile(r"\s+")
ARTICLE_RE = re.compile(r"^Artigo\s+(.+?)\s*$", re.IGNORECASE)

# Order matters when hierarchy is updated.
STRUCTURE_PATTERNS = [
    ("annex", re.compile(r"^(ANEXO|APÊNDICE)(?:\s+.*)?$", re.IGNORECASE), 10),
    ("book", re.compile(r"^LIVRO\s+.+$", re.IGNORECASE), 20),
    ("part", re.compile(r"^PARTE\s+.+$", re.IGNORECASE), 30),
    ("title", re.compile(r"^T[IÍ]TULO\s+.+$", re.IGNORECASE), 40),
    ("chapter", re.compile(r"^CAP[IÍ]TULO\s+.+$", re.IGNORECASE), 50),
    ("section", re.compile(r"^SEC[CÇ][AÃ]O\s+.+$", re.IGNORECASE), 60),
    ("subsection", re.compile(r"^SUBSEC[CÇ][AÃ]O\s+.+$", re.IGNORECASE), 70),
]

LEVEL_BY_TYPE = {name: level for name, _, level in STRUCTURE_PATTERNS}


def clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = SPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()
    return value or None


def slug_piece(value: str) -> str:
    value = value.lower().replace("º", "").replace("ª", "")
    value = re.sub(r"[^a-z0-9à-ÿ]+", "-", value, flags=re.IGNORECASE)
    return value.strip("-") or "unknown"


def find_legislation_json_ld(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "Legislation":
                return item
    return {}


def meta_content(soup: BeautifulSoup, *, property_: str | None = None, name: str | None = None) -> Optional[str]:
    attrs: dict[str, str] = {}
    if property_:
        attrs["property"] = property_
    if name:
        attrs["name"] = name
    tag = soup.find("meta", attrs=attrs)
    return clean_text(tag.get("content")) if tag else None


def canonical_url(soup: BeautifulSoup) -> Optional[str]:
    tag = soup.find("link", rel="canonical")
    return tag.get("href") if tag else None


def parse_title_number(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    match = re.search(r"n\.?\s*[.ºo]*\s*([0-9]+(?:-[A-Z])?/\d{4})", title, re.IGNORECASE)
    if match:
        return match.group(1)
    # More permissive fallback for other Portuguese legal-act titles.
    match = re.search(r"\b([0-9]+(?:-[A-Z])?/\d{4})\b", title, re.IGNORECASE)
    return match.group(1) if match else None


def relationship_targets(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    targets: list[str] = []
    for item in values:
        if isinstance(item, str):
            targets.append(item)
        elif isinstance(item, dict) and item.get("@id"):
            targets.append(item["@id"])
    return targets


def direct_content_root(soup: BeautifulSoup) -> Tag:
    # Current DRE rendered pages place the legal text here.
    wrappers = soup.select("[id$='InjectHTMLWrapper']")
    wrapper_candidates = []
    for wrapper in wrappers:
        inner = wrapper.find("div", recursive=False)
        if inner:
            score = len(inner.find_all("p", recursive=False))
            wrapper_candidates.append((score, inner))
    if wrapper_candidates:
        score, best_inner = max(wrapper_candidates, key=lambda item: item[0])
        if score > 0:
            return best_inner

    # Fallbacks make the adapter less dependent on generated OutSystems IDs.
    candidates = [
        soup.select_one(".texto.int-links"),
        soup.select_one("#ConteudoDiploma"),
        soup.select_one("[data-block='Legislacao_Conteudos.Conteudo_Det_Diploma']"),
    ]
    for candidate in candidates:
        if candidate:
            # Find the descendant containing the largest number of legal paragraphs.
            divs = [candidate, *candidate.find_all("div")]
            best = max(divs, key=lambda d: len(d.find_all("p", recursive=False)), default=candidate)
            return best

    raise ValueError("Could not locate the rendered DRE legal-content container.")


def classify_structure(text: str) -> tuple[str, int] | None:
    for type_, pattern, level in STRUCTURE_PATTERNS:
        if pattern.match(text):
            return type_, level
    return None


def get_following_heading_title(elements: list[Tag], index: int) -> Optional[str]:
    if index + 1 >= len(elements):
        return None
    nxt = elements[index + 1]
    classes = set(nxt.get("class") or [])
    if nxt.name == "p" and (
        "paragraph-bold-center-14px" in classes
        or "paragraph-bold-center" in classes
    ):
        return clean_text(nxt.get_text(" ", strip=True))
    return None


def looks_like_reference_note(text: str) -> bool:
    normalized = text.strip().lower()
    return (
        normalized.startswith("(a que se refere")
        or normalized.startswith("[a que se refere")
        or normalized.startswith("(a que se referem")
        or normalized.startswith("[a que se referem")
    )


def get_annex_details(elements: list[Tag], index: int) -> tuple[Optional[str], Optional[str], set[int]]:
    """Return (title, reference_note, consumed_indices) for a DRE annex heading."""
    title: Optional[str] = None
    reference_note: Optional[str] = None
    consumed: set[int] = set()

    for j in range(index + 1, min(index + 5, len(elements))):
        el = elements[j]
        text = clean_text(el.get_text(" ", strip=True)) or ""
        if not text:
            consumed.add(j)
            continue

        classes = set(el.get("class") or [])
        is_center = el.name == "p" and "paragraph-center" in classes
        if is_center and (ARTICLE_RE.match(text) or classify_structure(text)):
            break

        if looks_like_reference_note(text):
            reference_note = text
            consumed.add(j)
            continue

        # First non-reference textual line after ANEXO is treated as its human title.
        title = text
        consumed.add(j)
        break

    return title, reference_note, consumed


def update_hierarchy(
    hierarchy: list[HierarchyNode],
    node: HierarchyNode,
) -> list[HierarchyNode]:
    new_level = LEVEL_BY_TYPE[node.type]
    kept = [h for h in hierarchy if LEVEL_BY_TYPE.get(h.type, 999) < new_level]
    kept.append(node)
    return kept


def extract_table(table: Tag) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [clean_text(cell.get_text(" ", strip=True)) or "" for cell in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    return rows


def extract_tables_from_container(container: Tag) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    if container.name == "table":
        rows = extract_table(container)
        if rows:
            tables.append(rows)
        return tables
    for table in container.find_all("table"):
        rows = extract_table(table)
        if rows:
            tables.append(rows)
    return tables


def parse_dre_html(html: str) -> LegalDocument:
    soup = BeautifulSoup(html, "html.parser")
    ld = find_legislation_json_ld(soup)

    title = meta_content(soup, property_="og:title")
    if not title and soup.title:
        title = clean_text(soup.title.get_text(" ", strip=True).removesuffix("| DR"))

    description = meta_content(soup, property_="og:description") or meta_content(soup, name="description")
    source_url = canonical_url(soup) or ld.get("@id")
    language = ld.get("inLanguage") or "pt-PT"
    doc_type = ld.get("legislationType")
    document_number = parse_title_number(title)

    responsible = ld.get("legislationResponsible") or {}
    responsible_bodies: list[dict[str, str]] = []
    if isinstance(responsible, dict):
        if responsible.get("name") or responsible.get("@id"):
            responsible_bodies.append({
                "name": responsible.get("name"),
                "id": responsible.get("@id"),
            })

    document_id = (
        f"PT-{slug_piece(doc_type or 'legal-act').upper()}-{document_number.replace('/', '-') }"
        if document_number
        else f"PT-{slug_piece(title or 'legal-act').upper()}"
    )

    relation_map = {
        "legislationTransposes": "transposes",
        "legislationChanges": "changes",
        "legislationConsolidates": "consolidates",
        "citation": "cites",
    }
    relationships: list[Relationship] = []
    for source_key, normalized_type in relation_map.items():
        for target in relationship_targets(ld.get(source_key)):
            relationships.append(Relationship(type=normalized_type, target=target))

    root = direct_content_root(soup)
    elements = [el for el in root.children if isinstance(el, Tag)]

    preamble: list[str] = []
    articles: list[Article] = []
    annexes: list[Annex] = []
    hierarchy: list[HierarchyNode] = []
    current_article: dict[str, Any] | None = None
    article_started = False
    skip_title_index: set[int] = set()

    # Track annex text that is outside articles (e.g. annex tables).
    current_annex: dict[str, Any] | None = None

    def flush_article() -> None:
        nonlocal current_article
        if not current_article:
            return
        paragraphs = [p for p in current_article["paragraphs"] if p]
        current_article["paragraphs"] = paragraphs
        current_article["text"] = "\n".join(paragraphs).strip()
        articles.append(Article(**current_article))
        current_article = None

    def flush_annex() -> None:
        nonlocal current_annex
        if not current_annex:
            return
        current_annex["text"] = "\n".join(current_annex.pop("text_parts", [])).strip()
        annexes.append(Annex(**current_annex))
        current_annex = None

    for i, el in enumerate(elements):
        if i in skip_title_index:
            continue

        text = clean_text(el.get_text(" ", strip=True)) or ""
        if not text and el.name != "table":
            continue

        classes = set(el.get("class") or [])
        is_center_marker = el.name == "p" and "paragraph-center" in classes

        # A quoted article (e.g. «Artigo 16.º) is amendment content, not a new top-level article.
        article_match = ARTICLE_RE.match(text) if is_center_marker else None
        if article_match:
            flush_article()
            article_started = True

            number_raw = clean_text(article_match.group(1)) or article_match.group(1)
            number = number_raw.replace(".º", "").replace("º", "").replace(".ª", "").replace("ª", "").strip()
            article_title = get_following_heading_title(elements, i)
            if article_title:
                skip_title_index.add(i + 1)

            path = deepcopy(hierarchy)
            path_slug = "-".join(slug_piece(h.label) for h in path) or "root"
            article_id = f"{document_id}:{path_slug}:article-{slug_piece(number)}"

            current_article = {
                "id": article_id,
                "number": number,
                "label": text,
                "title": article_title,
                "text": "",
                "paragraphs": [],
                "hierarchy": path,
                "source_anchor": None,
            }
            continue

        structure = classify_structure(text) if is_center_marker else None
        if structure:
            flush_article()
            type_, _ = structure
            structure_title = get_following_heading_title(elements, i)
            if structure_title:
                skip_title_index.add(i + 1)

            reference_note = None
            if type_ == "annex":
                annex_title, reference_note, consumed = get_annex_details(elements, i)
                if annex_title is not None or reference_note is not None:
                    structure_title = annex_title
                    skip_title_index.update(consumed)

            node = HierarchyNode(type=type_, label=text, title=structure_title)
            hierarchy = update_hierarchy(hierarchy, node)

            if type_ == "annex":
                flush_annex()
                current_annex = {
                    "id": f"{document_id}:annex-{slug_piece(text)}",
                    "label": text,
                    "title": structure_title,
                    "reference_note": reference_note,
                    "text_parts": [],
                    "tables": [],
                }
            continue

        if current_article:
            # Preserve tables as readable text inside articles.
            tables = extract_tables_from_container(el)
            if tables:
                for rows in tables:
                    table_text = "\n".join(" | ".join(row) for row in rows)
                    if table_text:
                        current_article["paragraphs"].append(table_text)
            else:
                current_article["paragraphs"].append(text)
            continue

        if current_annex:
            tables = extract_tables_from_container(el)
            if tables:
                for rows in tables:
                    current_annex["tables"].append(rows)
                    current_annex["text_parts"].append("\n".join(" | ".join(row) for row in rows))
            else:
                current_annex["text_parts"].append(text)
            continue

        # Before the first article/annex, preserve the legislative preamble.
        if not article_started:
            # Skip the document title/date headings already represented as metadata.
            if "paragraph-title-bold-center-18px" not in classes and "paragraph-bold-center" not in classes:
                preamble.append(text)

    flush_article()
    flush_annex()

    return LegalDocument(
        schema_version="1.0",
        source={
            "name": "Diário da República",
            "adapter": "dre",
            "url": source_url,
            "jurisdiction": "PT",
        },
        document={
            "document_id": document_id,
            "title": title,
            "document_type": doc_type,
            "document_number": document_number,
            "language": language,
            "jurisdiction": "PT",
            "document_date": ld.get("datePublished"),
            "publication_date": ld.get("datePublished"),
            "entry_into_force_date": None,
            "status": None,
            "summary": description,
            "identifiers": {
                "eli": ld.get("@id"),
                "celex": None,
            },
            "responsible_bodies": responsible_bodies,
        },
        preamble=preamble,
        recitals=[],
        articles=articles,
        annexes=annexes,
        relationships=relationships,
    )


def parse_file(input_path: Path, output_path: Optional[Path] = None) -> dict[str, Any]:
    document = parse_dre_html(input_path.read_text(encoding="utf-8", errors="replace"))
    data = document.to_dict()
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main_dre_parser(
    input_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Parse a downloaded DRE HTML file and save the structured JSON."""

    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.is_file():
        raise FileNotFoundError(
            f"DRE HTML file not found: {input_path}"
        )

    data = parse_file(input_path, output_path)

    print("\nDRE document parsed successfully.\n")
    print(json.dumps({
        "document_id": data["document"]["document_id"],
        "title": data["document"]["title"],
        "articles": len(data["articles"]),
        "annexes": len(data["annexes"]),
        "relationships": len(data["relationships"]),
        "output": str(output_path),
    }, ensure_ascii=False, indent=2))

    return data
