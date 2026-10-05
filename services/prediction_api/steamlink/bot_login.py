"""One-time interactive login for the demo bot; outputs a Steam refresh token.

Run on a trusted machine (NOT committed, NOT on CI)::

    python -m steamlink.bot_login --out ~/.config/csa/steam-bot-refresh-token

It asks for the bot's username, password (hidden) and the Steam Guard code
(email or mobile authenticator) once, then writes the refresh token to ``--out``
with 0600 permissions (or prints it if ``--out`` is omitted). Put that value in
the ``STEAM_BOT_REFRESH_TOKEN`` env var on Render. The password is not stored.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys


async def _login(username: str, password: str) -> str:
    from steam.ext import csgo

    token: dict[str, str] = {}

    class _LoginClient(csgo.Client):
        async def on_login(self) -> None:
            token["value"] = self.refresh_token or ""
            await self.close()

    client = _LoginClient()
    await client.login(username, password)  # prompts for the Steam Guard code on stdin
    if not token.get("value"):
        raise SystemExit("login finished without a refresh token")
    return token["value"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="file to write the refresh token to (created with 0600 permissions)")
    args = parser.parse_args(argv)

    username = input("Steam bot username: ").strip()
    password = getpass.getpass("Steam bot password (hidden): ")
    refresh_token = asyncio.run(_login(username, password))

    if args.out:
        path = os.path.expanduser(args.out)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(refresh_token + "\n")
        print(f"Refresh token written to {path}. Copy it into STEAM_BOT_REFRESH_TOKEN on Render, then delete the file.")
    else:
        print("Refresh token (treat like a password; it is shown once):", file=sys.stderr)
        print(refresh_token)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
