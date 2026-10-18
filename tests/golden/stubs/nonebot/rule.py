import argparse


class ArgumentParser(argparse.ArgumentParser):
    """Like nonebot.rule.ArgumentParser: never calls sys.exit."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise ValueError(message)
