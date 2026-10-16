#!/usr/bin/env python3
"""Find, verify, and render the five Shanghai chemistry textbooks from local originals.

No network access, automatic Git writes, OCR, or model calls. File page numbers
are one-based. Original bytes are always checked before rendering or installation.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

def read_json(name: str) -> Any:
    return json.loads((HERE / name).read_text(encoding="utf-8"))

def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()

def inside(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Resource path escapes the selected repository root")
    return target

def originals(root: Path) -> dict[str, dict[str, Any]]:
    return {b["id"]: b for b in read_json("manifest.json")["books"]}

def checked_path(root: Path, book: dict[str, Any]) -> Path:
    path = inside(root, book["path"])
    if not path.is_file():
        raise FileNotFoundError(f"Original not installed: {path}")
    if path.stat().st_size != book["bytes"] or sha256(path) != book["sha256"]:
        raise ValueError(f"Original checksum mismatch: {path}")
    return path

def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))

def verify(root: Path) -> int:
    rows = []
    for book in originals(root).values():
        try:
            checked_path(root, book)
            rows.append({"id": book["id"], "status": "verified", "path": book["path"],
                         "sha256": book["sha256"], "expected_pdf_pages": book["pdf_pages"]})
        except (OSError, ValueError) as error:
            rows.append({"id": book["id"], "status": "missing_or_mismatched", "error": str(error)})
    success = all(row["status"] == "verified" for row in rows)
    emit({"all_five_verified": success, "local_files": rows,
          "scope": "Local byte verification; this does not assert that GitHub contains the PDFs."})
    return 0 if success else 2

def search(query: str) -> None:
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        raise ValueError("Search query must not be empty")
    sections = read_json("sections.json")
    clips = read_json("excerpts.json")
    def matching(row: dict[str, Any]) -> bool:
        searchable = " ".join(str(row.get(key, "")) for key in
                              ("id", "title", "keywords", "book_id", "question")).casefold()
        return all(term in searchable for term in terms)
    emit({"query": query, "sections": [s for s in sections if matching(s)],
          "excerpts": [s for s in clips if matching(s)],
          "scope": "Table of contents and verified excerpt metadata, not full-text OCR."})

def install(root: Path, source_dir: Path) -> None:
    """Validate every input before copying; never replace a different destination."""
    plans = []
    for book in originals(root).values():
        options = [source_dir / book["uploaded_name"], source_dir / Path(book["path"]).name,
                   source_dir / book["path"]]
        candidates = [p for p in options if p.is_file()]
        source = next((p for p in candidates if p.stat().st_size == book["bytes"]
                       and sha256(p) == book["sha256"]), None)
        if source is None:
            raise FileNotFoundError(f"No byte-identical source found for {book['id']}")
        target = inside(root, book["path"])
        if target.exists() and sha256(target) != book["sha256"]:
            raise FileExistsError(f"Different existing original will not be overwritten: {target}")
        plans.append((source.resolve(), target, book))
    for source, target, book in plans:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source != target and not target.exists():
            temp = target.with_suffix(".pdf.importing")
            if temp.exists():
                raise FileExistsError(f"Temporary path already exists: {temp}")
            try:
                shutil.copyfile(source, temp)
                if sha256(temp) != book["sha256"]:
                    raise ValueError("Copied bytes failed verification")
                temp.replace(target)
            finally:
                temp.unlink(missing_ok=True)
    # Catalog update is additive and leaves unrelated rows in place.
    catalog = root / "sh-chem-db/catalog.csv"
    fields = ["id", "title", "source_type", "publisher", "source_filename", "path", "sha256", "pdf_pages", "collected_date"]
    rows: list[dict[str, Any]] = []
    if catalog.exists():
        with catalog.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            existing_fields = reader.fieldnames or []
            fields = existing_fields + [f for f in fields if f not in existing_fields]
            rows = list(reader)
    for _, _, b in plans:
        row = dict(id="textbook-20261016-" + b["id"], title=b["title"], source_type="user_upload",
                   publisher=b["publisher"], source_filename=b["uploaded_name"], path=b["path"],
                   sha256=b["sha256"], pdf_pages=b["pdf_pages"], collected_date="2026-10-16")
        if not any(r.get("path") == row["path"] and r.get("sha256") == row["sha256"] for r in rows):
            rows.append(row)
    catalog.parent.mkdir(parents=True, exist_ok=True)
    temp_catalog = catalog.with_suffix(".csv.importing")
    if temp_catalog.exists():
        raise FileExistsError(f"Temporary path already exists: {temp_catalog}")
    with temp_catalog.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temp_catalog.replace(catalog)
    emit({"installed": [b["id"] for _, _, b in plans], "catalog": str(catalog),
          "github_upload_performed": False})

def render(root: Path, book_id: str, pdf_page: int, output: Path, dpi: int,
           bbox: list[float] | None = None) -> None:
    if not 72 <= dpi <= 600:
        raise ValueError("dpi must be between 72 and 600")
    try:
        import fitz
    except ImportError as error:
        raise RuntimeError("Rendering requires PyMuPDF: python -m pip install pymupdf") from error
    books = originals(root)
    if book_id not in books:
        raise ValueError(f"Unknown book id: {book_id}")
    source = checked_path(root, books[book_id])
    if output.resolve() == source.resolve() or output.exists():
        raise FileExistsError(f"Output must be a new file: {output}")
    if output.suffix.lower() != ".png":
        raise ValueError("Rendered output must have the .png extension")
    with fitz.open(source) as doc:
        if len(doc) != books[book_id]["pdf_pages"]:
            raise ValueError("PDF page count mismatch")
        if not 1 <= pdf_page <= len(doc):
            raise ValueError("PDF page is outside this book")
        page = doc[pdf_page - 1]
        clip = None
        if bbox is not None:
            x0, y0, x1, y1 = bbox
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError("Invalid normalized crop coordinates")
            clip = fitz.Rect(x0 * page.rect.width, y0 * page.rect.height,
                             x1 * page.rect.width, y1 * page.rect.height)
        output.parent.mkdir(parents=True, exist_ok=True)
        page.get_pixmap(dpi=dpi, clip=clip, alpha=False).save(output)
    emit({"output": str(output), "book_id": book_id, "pdf_page": pdf_page,
          "bbox_normalized": bbox, "source_sha256": books[book_id]["sha256"]})

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=HERE.parents[2], help="Repository root")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify", help="Verify all five local original PDFs")
    s = sub.add_parser("search", help="Search section/excerpt metadata")
    s.add_argument("query")
    i = sub.add_parser("install", help="Copy all five verified local originals and update catalog")
    i.add_argument("--source-dir", type=Path, required=True)
    p = sub.add_parser("page", help="Render a full textbook page")
    p.add_argument("--book", required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--printed", type=int)
    group.add_argument("--pdf-page", type=int)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--dpi", type=int, default=240)
    c = sub.add_parser("crop", help="Render one visually checked excerpt")
    c.add_argument("--id", required=True)
    c.add_argument("--output", type=Path, required=True)
    c.add_argument("--dpi", type=int, default=240)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.command == "verify":
            return verify(root)
        if args.command == "search":
            search(args.query)
        elif args.command == "install":
            install(root, args.source_dir.resolve())
        elif args.command == "page":
            books = originals(root)
            if args.book not in books:
                raise ValueError("Unknown book id")
            if args.printed is not None and args.printed < 1:
                raise ValueError("Printed page must be positive")
            page = args.pdf_page if args.pdf_page is not None else args.printed + books[args.book]["printed_page_offset"]
            render(root, args.book, page, args.output, args.dpi)
        elif args.command == "crop":
            excerpt = next((x for x in read_json("excerpts.json") if x["id"] == args.id), None)
            if excerpt is None:
                raise ValueError(f"Unknown excerpt: {args.id}")
            b = originals(root)[excerpt["book_id"]]
            if excerpt["source_sha256"] != b["sha256"]:
                raise ValueError("Excerpt/source version mismatch")
            render(root, excerpt["book_id"], excerpt["pdf_page"], args.output, args.dpi,
                   excerpt["bbox_normalized"])
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
