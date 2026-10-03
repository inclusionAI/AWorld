"""Thin installed CLI delegates to the AWorld kernel."""


def main():
    from aworld.cli.main import main as kernel_main
    return kernel_main()


if __name__ == "__main__":
    raise SystemExit(main())
