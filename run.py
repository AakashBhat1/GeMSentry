import sys


def main():
    # Phase 4: ensure layout + logging before anything else
    import paths
    import logging_setup

    paths.ensure_dirs()
    logger = logging_setup.setup_logging()

    logger.info("=" * 60)
    logger.info("           GeMSentry: Smart RFP Acquisition System")
    logger.info("=" * 60)

    # 1. Dependency Checks
    # Imported purely to prove the dependency is installed before we start.
    try:
        import bs4  # noqa: F401
        import flask  # noqa: F401
        import playwright  # noqa: F401
        import pypdf  # noqa: F401
    except ImportError as e:
        logger.error("Missing Python dependencies: %s", e)
        logger.error("Please run: uv sync   (or: pip install -e .)")
        sys.exit(1)

    # 2. Start the server
    server_cfg = paths.load_server_config()
    try:
        paths.require_safe_bind(server_cfg)
    except RuntimeError as e:
        logger.error("%s", e)
        sys.exit(1)
    host = server_cfg.get("host", "127.0.0.1")
    port = int(server_cfg.get("port", 5000))
    auth_on = bool(server_cfg.get("auth_token", "").strip())
    logger.info("Starting GeMSentry Server on %s:%s (Auth: %s)...", host, port, "ENABLED" if auth_on else "DISABLED")
    try:
        from app import app
        from gemsentry.serve import serve
        serve(app, host, port)
    except Exception as e:
        logger.error("Server failed to start: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
