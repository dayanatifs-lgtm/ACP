"""Command line runner for ACP imports."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from .client import IfsAcpClient, IfsApiError
from .config import Settings


@dataclass
class Result:
    path: Path
    success: bool
    message: str


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Import .acp packages into IFS.")
    parser.add_argument("--dry-run", action="store_true", help="List packages without authentication or API calls.")
    parser.add_argument("--folder", type=Path, help="Override IFS_ACP_FOLDER for this run.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    configure_logging(args.verbose)

    try:
        settings = Settings.from_environment(require_auth=not args.dry_run)
    except ValueError as exc:
        logging.error("Configuration error: %s", exc)
        return 2
    folder = args.folder or settings.acp_folder
    # The original requirement used .acp examples, while the captured IFS upload
    # is a .zip file. IFS ACP exports are commonly delivered as ZIP archives.
    packages = sorted(
        path for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in {".acp", ".zip"}
    )
    if not packages:
        logging.warning("No .acp or .zip files found in %s", folder)
        return 0

    client = IfsAcpClient(settings)
    if not args.dry_run:
        try:
            client.authenticate()
        except (IfsApiError, Exception) as exc:
            logging.error("Authentication failed: %s", exc)
            return 2

    results: list[Result] = []
    for package in packages:
        try:
            success, message = client.import_acp(package, dry_run=args.dry_run)
            results.append(Result(package, success, message))
        except Exception as exc:
            logging.exception("Import failed for %s", package.name)
            results.append(Result(package, False, str(exc)))

    print("\nACP Import Summary\n------------------")
    for result in results:
        state = "SUCCESS" if result.success else "FAILED"
        print(f"{result.path.name} - {state} ({result.message})")
    successful = sum(result.success for result in results)
    print(f"\nTotal: {len(results)}\nSuccessful: {successful}\nFailed: {len(results) - successful}")
    return 0 if successful == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
