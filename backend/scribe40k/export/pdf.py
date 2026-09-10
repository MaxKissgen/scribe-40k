"""PDF export: render the editor's own print route through headless Chromium.

The alternative would have been to stamp values onto the original template PDF at fixed
coordinates. This approach was chosen instead because it cannot drift: the exporter loads
``/print/<id>`` from the running server, which is the same React components and the same
stylesheet the editor uses, in print mode. If the sheet looks right on screen it prints
right, and there is no coordinate map to maintain in parallel with the layout.

The cost is that Chromium has to be installed. It is an optional dependency, and the error
message says exactly how to get it.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .. import constants as K

#: Page sizes the export offers. The native size is the template's own, which is neither
#: A4 nor Letter -- printing to A4 shrinks the sheet slightly rather than cropping it.
PAGE_SIZES: dict[str, tuple[str, str]] = {
    "native": (f"{K.PAGE_WIDTH_MM}mm", f"{K.PAGE_HEIGHT_MM}mm"),
    "a4": ("210mm", "297mm"),
    "letter": ("216mm", "279mm"),
}

DEFAULT_TIMEOUT_MS = 30_000


class ExportError(RuntimeError):
    """Export could not run. The message says what to do about it."""


@dataclass
class ExportOptions:
    page_size: str = "native"
    #: Wait for webfonts and images before capturing. Without this the first export after
    #: a cold start can come out in a fallback face.
    settle_ms: int = 400
    timeout_ms: int = DEFAULT_TIMEOUT_MS


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ExportError(
            "PDF export needs Playwright. Install it with:\n"
            '    pip install -e ".[export]"\n'
            "    python -m playwright install chromium"
        ) from exc
    return sync_playwright


def render_url_to_pdf(
    url: str,
    destination: Path,
    options: ExportOptions | None = None,
) -> Path:
    """Print one URL to PDF."""
    options = options or ExportOptions()

    if options.page_size not in PAGE_SIZES:
        raise ExportError(
            f"unknown page size '{options.page_size}'. Choose one of: {', '.join(PAGE_SIZES)}"
        )

    width, height = PAGE_SIZES[options.page_size]
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    sync_playwright = _require_playwright()

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:
            raise ExportError(
                "Could not start Chromium. Install the browser with:\n"
                "    python -m playwright install chromium"
            ) from exc

        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=options.timeout_ms)

            # Webfonts in particular: laying out before they resolve changes line breaks.
            page.wait_for_timeout(options.settle_ms)
            with contextlib.suppress(Exception):
                page.evaluate("document.fonts && document.fonts.ready")

            page.pdf(
                path=str(destination),
                width=width,
                height=height,
                print_background=True,
                # The @page rule in print.css already declares the template's size, so let
                # the stylesheet win where it disagrees with the arguments above.
                prefer_css_page_size=options.page_size == "native",
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
        finally:
            browser.close()

    return destination


# --------------------------------------------------------------------------------------
# Running the app just long enough to print it
# --------------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def temporary_server(port: int | None = None, startup_timeout: float = 20.0):
    """Run the API on a background thread for the duration of an export.

    The CLI needs a server to point Chromium at, and asking the user to start one first
    would make ``scribe export`` fail for a reason that has nothing to do with them.
    """
    import uvicorn

    from ..api import app

    port = port or _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + startup_timeout
    while not server.started:
        if time.monotonic() > deadline:
            server.should_exit = True
            raise ExportError("the local server did not start in time")
        if not thread.is_alive():
            raise ExportError("the local server stopped before it finished starting")
        time.sleep(0.05)

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def export_character(
    character_id: str,
    destination: Path,
    *,
    options: ExportOptions | None = None,
    base_url: str | None = None,
) -> Path:
    """Export one character to PDF, and remember what the pages looked like.

    Point ``base_url`` at an already-running server to reuse it; otherwise one is started
    for the duration of the call.

    The remembering is the important half for anything that comes back. A PDF this program
    wrote can be re-imported from its text layer for nothing -- but the path people
    actually take is to print it, mark it up and scan it, and a printer does not print
    text layers. What comes back is a photograph of a page the blank template does not
    describe, especially where a long gear list has spilled onto a page of its own. So the
    layout is recorded here, at the one moment we know exactly what was on the paper.
    """
    if base_url:
        rendered = render_url_to_pdf(
            f"{base_url.rstrip('/')}/print/{character_id}", destination, options
        )
    else:
        with temporary_server() as url:
            rendered = render_url_to_pdf(f"{url}/print/{character_id}", destination, options)

    _remember_layout(character_id, rendered)
    return rendered


def _remember_layout(character_id: str, pdf_path: Path) -> None:
    """Record this printing. Never fatal: an export that worked is still an export."""
    try:
        from .. import api
        from ..pipeline.print_layout import record

        api.store.save_print_layout(character_id, record(pdf_path, source_name=pdf_path.name))
    except Exception:  # noqa: BLE001 - a diagnostic aid must not break the export
        pass
