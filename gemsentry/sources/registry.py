"""Portal source registry: config loading, engine dispatch and parallel fan-out."""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Dict, Iterable, List, Optional, Type

import paths
from gemsentry.constants import logger
from gemsentry.sources.base import BaseAdapter, UnsupportedAdapter
from gemsentry.sources.bhel import BHELAdapter
from gemsentry.sources.gepnic import GePNICAdapter
from gemsentry.sources.isro import ISROAdapter

# engine name -> adapter class. Adding a portal family means adding one entry.
ENGINES: Dict[str, Type[BaseAdapter]] = {
    "gepnic": GePNICAdapter,
    "isro": ISROAdapter,
    "bhel": BHELAdapter,
}

# GeM is not fanned out here: it has its own pipeline (cookie/CSRF handshake,
# paginated JSON API, PDF download and scoring) driven from gemsentry.pipeline.
NATIVE_ENGINES = frozenset({"gem"})

DEFAULT_MAX_WORKERS = 4
DEFAULT_SOURCE_TIMEOUT = 90


class SourceRegistry:
    """Loads ``config/sources.json`` and runs the adapters that exist.

    Sources whose engine has no implementation are kept visible (the dashboard
    lists them) but are never dispatched, so the run does not spawn a dozen
    workers that can only return an empty list.
    """

    def __init__(self, sources_path: str = paths.SOURCES_PATH):
        self.sources_path = sources_path
        self.sources: List[Dict[str, Any]] = []
        self.adapters: Dict[str, BaseAdapter] = {}
        self._lock = threading.Lock()
        self.reload_sources()

    # -- config ----------------------------------------------------------

    def reload_sources(self) -> None:
        if not os.path.exists(self.sources_path):
            logger.warning("Sources config not found at %s", self.sources_path)
            self.sources, self.adapters = [], {}
            return
        try:
            with open(self.sources_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.error("Failed to load sources from %s: %s", self.sources_path, exc)
            return

        self.sources = data.get("sources", [])
        self._build_adapters()
        logger.info(
            "Loaded %d source(s): %d runnable, %d native, %d awaiting an adapter",
            len(self.sources), len(self.runnable_adapters()),
            len(self._by_engine(NATIVE_ENGINES)), len(self.unsupported_sources()),
        )

    def _build_adapters(self) -> None:
        adapters: Dict[str, BaseAdapter] = {}
        for source in self.sources:
            source_id = source.get("id")
            if not source_id:
                logger.warning("Skipping source with no id: %r", source.get("name"))
                continue
            engine = (source.get("engine") or "").lower()
            adapter_cls = ENGINES.get(engine, UnsupportedAdapter)
            try:
                adapters[source_id] = adapter_cls(source)
            except Exception as exc:
                logger.error("Could not build adapter for '%s': %s", source_id, exc)
        self.adapters = adapters

    def _save_sources(self) -> None:
        tmp_path = f"{self.sources_path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "sources": self.sources}, handle, indent=2)
            os.replace(tmp_path, self.sources_path)
            logger.info("Saved updated sources configuration")
        except OSError as exc:
            logger.error("Failed to save sources configuration: %s", exc)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # -- queries ---------------------------------------------------------

    def get_all_sources(self) -> List[Dict[str, Any]]:
        """Every configured source, annotated with its runtime capability."""
        annotated = []
        for source in self.sources:
            adapter = self.adapters.get(source.get("id"))
            engine = (source.get("engine") or "").lower()
            annotated.append({
                **source,
                "supported": bool(adapter and getattr(adapter, "implemented", False)),
                "native": engine in NATIVE_ENGINES,
            })
        return annotated

    def get_active_sources(self) -> List[Dict[str, Any]]:
        return [s for s in self.sources if s.get("enabled", True)]

    def _by_engine(self, engines: Iterable[str]) -> List[Dict[str, Any]]:
        wanted = set(engines)
        return [s for s in self.sources if (s.get("engine") or "").lower() in wanted]

    def unsupported_sources(self) -> List[Dict[str, Any]]:
        """Enabled sources whose engine has no adapter and isn't native."""
        return [
            s for s in self.get_active_sources()
            if (s.get("engine") or "").lower() not in ENGINES
            and (s.get("engine") or "").lower() not in NATIVE_ENGINES
        ]

    def runnable_adapters(self) -> List[BaseAdapter]:
        """Enabled sources that have a real adapter to run."""
        enabled = {s.get("id") for s in self.get_active_sources()}
        return [
            adapter for source_id, adapter in self.adapters.items()
            if source_id in enabled and getattr(adapter, "implemented", False)
        ]

    def toggle_source(self, source_id: str, enabled: bool) -> bool:
        with self._lock:
            for source in self.sources:
                if source.get("id") == source_id:
                    source["enabled"] = bool(enabled)
                    self._save_sources()
                    self._build_adapters()
                    return True
        return False

    # -- fan-out ---------------------------------------------------------

    def fetch_from_all_active(
        self,
        keywords: List[str],
        max_workers: int = DEFAULT_MAX_WORKERS,
        max_pages: int = 5,
        timeout: Optional[float] = DEFAULT_SOURCE_TIMEOUT,
    ) -> List[Dict[str, Any]]:
        """Query every runnable portal in parallel and merge the results.

        Results are de-duplicated on ``tender_id`` -- the same tender is often
        published on both a parent portal (CPPP) and the buying organisation's
        own site.
        """
        adapters = self.runnable_adapters()
        skipped = self.unsupported_sources()
        if skipped:
            logger.info(
                "Skipping %d source(s) with no adapter yet: %s",
                len(skipped), ", ".join(sorted(s.get("id", "?") for s in skipped)),
            )
        if not adapters:
            logger.info("No runnable portal adapters enabled; multi-source fetch skipped.")
            return []

        logger.info("Querying %d portal(s) in parallel: %s",
                    len(adapters), ", ".join(a.source_id for a in adapters))

        merged: Dict[str, Dict[str, Any]] = {}
        pool = ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(adapters))))
        try:
            futures = {
                pool.submit(self._run_adapter, adapter, keywords, max_pages): adapter
                for adapter in adapters
            }
            # One shared deadline for the whole fan-out: a single unresponsive
            # portal must not hold up the scrape run.
            done, pending = wait(futures, timeout=timeout)
            for future in pending:
                logger.error("[%s] timed out after %ss", futures[future].source_id, timeout)
                future.cancel()
            for future in done:
                try:
                    for tender in future.result():
                        merged.setdefault(tender.get("tender_id"), tender)
                except Exception as exc:
                    logger.error("[%s] worker failed: %s", futures[future].source_id, exc)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        logger.info("Multi-source aggregation complete: %d unique tender(s).", len(merged))
        return list(merged.values())

    @staticmethod
    def _run_adapter(adapter: BaseAdapter, keywords: List[str], max_pages: int) -> List[Dict[str, Any]]:
        tenders = adapter.fetch_active_tenders(keywords, max_pages=max_pages)
        logger.info("[%s] returned %d tender(s)", adapter.source_id, len(tenders))
        return tenders
