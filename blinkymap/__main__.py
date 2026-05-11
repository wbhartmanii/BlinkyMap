"""Allows `python -m blinkymap` and is the pipx entry point."""
from .app import BlinkyMapApp


def main():
    BlinkyMapApp().run()


if __name__ == "__main__":
    main()
