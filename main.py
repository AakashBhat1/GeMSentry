"""Thin alias entrypoint — prefer `python run.py`."""


def main():
    from run import main as run_main
    run_main()


if __name__ == "__main__":
    main()
