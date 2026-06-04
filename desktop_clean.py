"""Desktop entry point for Konggu."""

from __future__ import annotations

from app.gui.main_window import KongguApp


def main() -> None:
    app = KongguApp()
    app.mainloop()


if __name__ == "__main__":
    main()
