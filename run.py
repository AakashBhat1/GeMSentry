import os
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
    try:
        import playwright
        import bs4
        import pypdf
        import flask
    except ImportError as e:
        logger.error("Missing Python dependencies: %s", e)
        logger.error("Please run: pip install -r requirements.txt")
        sys.exit(1)

    # 2. Start the server
    server_cfg = paths.load_server_config()
    host = server_cfg.get("host", "0.0.0.0")
    port = int(server_cfg.get("port", 5000))
    auth_on = bool(server_cfg.get("auth_token", "").strip())
    logger.info("Starting GeMSentry Server on %s:%s (Auth: %s)...", host, port, "ENABLED" if auth_on else "DISABLED")
    try:
        from app import app
        app.run(host=host, port=port, debug=False)
    except Exception as e:
        logger.error("Server failed to start: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
