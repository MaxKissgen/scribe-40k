"""Command line: ``scribe extract | list | show | serve | export``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .llm.base import ProviderError
from .llm.config import apply_overrides, load_config
from .llm.registry import build_ocr_provider, build_reasoning_provider
from .pipeline.run import extract as run_extract
from .store import CharacterStore

app = typer.Typer(
    add_completion=False,
    help="Extract, edit and export Dark Heresy character sheets.",
    no_args_is_help=True,
)
console = Console()

SEVERITY_STYLE = {"error": "bold red", "warning": "yellow", "info": "dim"}


@app.command()
def extract(
    pdf: Annotated[Path, typer.Argument(help="The scanned character sheet PDF.")],
    name: Annotated[str | None, typer.Option(help="Name for the stored character.")] = None,
    ocr_provider: Annotated[str | None, typer.Option("--ocr-provider")] = None,
    ocr_model: Annotated[str | None, typer.Option("--ocr-model")] = None,
    reasoning_provider: Annotated[str | None, typer.Option("--reasoning-provider")] = None,
    reasoning_model: Annotated[str | None, typer.Option("--reasoning-model")] = None,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Ignore cached model replies.")
    ] = False,
    config_file: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Read a sheet PDF into a character document plus an extraction report."""
    if not pdf.exists():
        console.print(f"[bold red]No such file:[/] {pdf}")
        raise typer.Exit(1)

    config = apply_overrides(
        load_config(config_file),
        ocr_provider=ocr_provider,
        ocr_model=ocr_model,
        reasoning_provider=reasoning_provider,
        reasoning_model=reasoning_model,
        cache=False if no_cache else None,
    )

    try:
        ocr = build_ocr_provider(config.ocr, cache=config.cache)
        reasoning = build_reasoning_provider(config.reasoning, cache=config.cache)
    except ProviderError as exc:
        console.print(f"[bold red]{exc}[/]")
        raise typer.Exit(1) from exc

    console.print(
        f"[dim]OCR:[/] {ocr.name}/{ocr.model}   "
        f"[dim]reasoning:[/] {reasoning.name}/{reasoning.model}"
    )

    store = CharacterStore()
    character_id = store.new_id(name or pdf.stem)
    store.store_source(character_id, pdf)

    outcome = run_extract(
        pdf,
        ocr,
        reasoning,
        image_dir=store.pages_dir(character_id),
        progress=lambda message: console.print(f"[dim]{message}[/]"),
    )

    if name:
        outcome.character["bio"]["characterName"] = (
            outcome.character["bio"].get("characterName") or name
        )

    store.save(character_id, outcome.character)
    store.save_report(character_id, outcome.report)

    console.print()
    console.print(f"Saved as [bold]{character_id}[/] in {store.directory(character_id)}")
    _print_flags(outcome.report)


@app.command("list")
def list_characters() -> None:
    """List stored characters."""
    summaries = CharacterStore().list_characters()
    if not summaries:
        console.print("[dim]No characters yet. Run 'scribe extract <pdf>'.[/]")
        return

    table = Table(box=None, pad_edge=False)
    table.add_column("id", style="bold")
    table.add_column("name")
    table.add_column("career")
    table.add_column("review", justify="right")
    table.add_column("updated", style="dim")

    for summary in summaries:
        pending = f"[yellow]{summary.reviewCount}[/]" if summary.reviewCount else "[green]0[/]"
        table.add_row(
            summary.id,
            summary.name,
            summary.career or "[dim]--[/]",
            pending,
            summary.updatedAt[:19].replace("T", " "),
        )

    console.print(table)


@app.command()
def show(
    character_id: Annotated[str, typer.Argument(help="Character id, from 'scribe list'.")],
) -> None:
    """Show what still needs review for one character."""
    store = CharacterStore()
    if not store.exists(character_id):
        console.print(f"[bold red]No character '{character_id}'.[/] Try 'scribe list'.")
        raise typer.Exit(1)

    document = store.load(character_id)
    bio = document.get("bio") or {}
    console.print(f"[bold]{bio.get('characterName') or character_id}[/]")
    for label, key in (("Player", "playerName"), ("Career", "career"), ("Rank", "rank")):
        if bio.get(key):
            console.print(f"  {label}: {bio[key]}")

    report = store.load_report(character_id)
    if report is None:
        console.print("\n[dim]No extraction report: this character was not imported.[/]")
        return

    console.print()
    _print_flags(report)


@app.command()
def export(
    character_id: Annotated[str, typer.Argument(help="Character id, from 'scribe list'.")],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Where to write the PDF.")
    ] = None,
    page_size: Annotated[
        str, typer.Option("--page-size", help="native (213x276mm), a4, or letter.")
    ] = "native",
    server: Annotated[
        str | None, typer.Option("--server", help="Reuse a running server instead of starting one.")
    ] = None,
) -> None:
    """Render a character to PDF, exactly as the editor shows it."""
    from .export.pdf import ExportError, ExportOptions, export_character

    store = CharacterStore()
    if not store.exists(character_id):
        console.print(f"[bold red]No character '{character_id}'.[/] Try 'scribe list'.")
        raise typer.Exit(1)

    report = store.load_report(character_id)
    if report and report.review_count:
        # A warning, not a block: the user may well want a printout of a sheet they have
        # not finished checking.
        console.print(
            f"[yellow]Note:[/] {report.review_count} field(s) still need review. "
            f"Run 'scribe show {character_id}' to see them."
        )

    destination = output or Path(f"{character_id}.pdf")
    console.print(f"[dim]Rendering {character_id} at {page_size} size...[/]")

    try:
        written = export_character(
            character_id,
            destination,
            options=ExportOptions(page_size=page_size),
            base_url=server,
        )
    except ExportError as exc:
        console.print(f"[bold red]{exc}[/]")
        raise typer.Exit(1) from exc

    console.print(f"Wrote [bold]{written}[/] ({written.stat().st_size // 1024} KB)")


@app.command()
def serve(
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
    host: Annotated[str, typer.Option(help="Address to bind.")] = "127.0.0.1",
) -> None:
    """Run the editor."""
    import uvicorn

    from .paths import FRONTEND_DIST

    if not FRONTEND_DIST.exists():
        console.print(
            "[yellow]The frontend has not been built.[/] The API will run, but the editor "
            "will not load. Build it with:\n"
            "    cd frontend && npm install && npm run build"
        )

    console.print(f"Editor at [bold]http://{host}:{port}[/]")
    uvicorn.run("scribe40k.api:app", host=host, port=port, log_level="info")


def _print_flags(report) -> None:
    counts = report.severity_counts()
    total = report.review_count

    if total == 0:
        console.print("[green]Nothing needs review.[/]")
    else:
        parts = [f"[{SEVERITY_STYLE[s]}]{n} {s}[/]" for s, n in counts.items() if n]
        console.print(f"[bold]{total} field(s) need review[/] ({', '.join(parts)})")

        table = Table(box=None, pad_edge=False, show_header=False)
        table.add_column("", width=2)
        table.add_column("field", style="cyan", no_wrap=True)
        table.add_column("problem")

        for flag in report.open_flags[:25]:
            marker = {"error": "!", "warning": "?", "info": "-"}[flag.severity]
            table.add_row(
                f"[{SEVERITY_STYLE[flag.severity]}]{marker}[/]",
                flag.pointer or "(whole sheet)",
                flag.message,
            )
        console.print(table)

        if total > 25:
            console.print(f"[dim]...and {total - 25} more.[/]")

    unresolved = [u for u in report.unmapped if u.status == "unresolved"]
    if unresolved:
        console.print()
        console.print(f"[bold]{len(unresolved)} fragment(s) found on the sheet but not assigned[/]")
        for item in unresolved[:10]:
            where = item.source.location or f"page {item.source.pdfPage}"
            console.print(f"  [dim]{where}:[/] {item.text[:80]}")
        if len(unresolved) > 10:
            console.print(f"[dim]  ...and {len(unresolved) - 10} more.[/]")

    failed = [s for s in report.sections if s.status != "ok"]
    if failed:
        console.print()
        console.print("[bold red]Sections that did not complete[/]")
        for section in failed:
            console.print(f"  {section.name}: {section.status} -- {section.error}")


if __name__ == "__main__":
    app()
