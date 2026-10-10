from .cli import main

if __name__ == "__main__":  # guard: worker processes re-import this module on Windows
    raise SystemExit(main())
