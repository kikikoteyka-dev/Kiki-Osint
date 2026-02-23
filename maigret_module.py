import asyncio
import logging
import json
import os

def _get_db_data():
    import maigret
    db_path = os.path.join(os.path.dirname(maigret.__file__), "resources", "data.json")
    with open(db_path, encoding="utf-8") as f:
        return json.load(f)

async def run_maigret(username: str, limit: int = 100, progress_callback=None) -> list:
    """Запускает Maigret как библиотеку без subprocess и прогресс-бара"""
    try:
        from maigret.checking import maigret as maigret_search
        from maigret.sites import MaigretDatabase
        from maigret.result import MaigretCheckStatus

        db = MaigretDatabase()
        db.load_from_json(_get_db_data())
        sites = db.ranked_sites_dict(top=limit)

        logger = logging.getLogger("maigret_silent")
        logger.setLevel(logging.CRITICAL)

        results = await maigret_search(
            username=username,
            site_dict=sites,
            logger=logger,
            query_notify=None,
            timeout=10,
            no_progressbar=True,
            max_connections=20,
        )

        found = []
        for site_name, info in results.items():
            status_obj = info.get("status")
            if status_obj and hasattr(status_obj, "status"):
                if status_obj.status == MaigretCheckStatus.CLAIMED:
                    url = info.get("url_user") or info.get("url_main") or ""
                    entry = {"site": site_name, "url": url}
                    found.append(entry)
                    if progress_callback:
                        progress_callback(entry)

        return found

    except Exception as e:
        return [{"error": str(e)}]

def run_maigret_sync(username: str, limit: int = 100) -> list:
    return asyncio.run(run_maigret(username, limit))
