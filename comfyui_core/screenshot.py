"""Optional screenshot of a workflow graph in the ComfyUI web UI (needs playwright)."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def workflow_screenshot(backend_url: str, workflow_file: Path, timeout_ms: int = 30000) -> bytes | None:
    """Load ``workflow_file`` into the ComfyUI page at ``backend_url`` and screenshot it.

    Returns ``None`` when playwright or its browser is missing, or anything fails;
    the workflow list is complete without it.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.debug("playwright is not installed; skipping the workflow screenshot")
        return None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 3000, "height": 2000})
                await page.goto(backend_url, timeout=timeout_ms)
                await page.wait_for_load_state("networkidle", timeout=timeout_ms)
                drop = await page.query_selector("#comfy-file-input")
                if drop:
                    await drop.set_input_files(str(workflow_file))
                    await page.wait_for_load_state("networkidle", timeout=timeout_ms)
                return await page.screenshot(type="jpeg", full_page=True, quality=70)
            finally:
                await browser.close()
    except Exception as e:
        logger.warning("Workflow screenshot failed: %s", e)
        return None
