"""Install a hash-verified official .NET 9 SDK into the project only."""
from pathlib import Path
import hashlib
import json
import urllib.request
import zipfile

from autoslay.paths import ROOT
root = ROOT
metadata = json.load(urllib.request.urlopen(
    "https://dotnetcli.blob.core.windows.net/dotnet/release-metadata/9.0/releases.json", timeout=60))
version = metadata["latest-sdk"]
sdk = next(s for r in metadata["releases"] for s in r.get("sdks", []) if s["version"] == version)
asset = next(f for f in sdk["files"] if f["rid"] == "win-x64" and f["name"].endswith(".zip"))
archive = root / ".runtime" / "cache" / f"dotnet-sdk-{version}.zip"
archive.parent.mkdir(parents=True, exist_ok=True)
print(f"Downloading .NET SDK {version}", flush=True)
urllib.request.urlretrieve(asset["url"], archive)
digest = hashlib.file_digest(archive.open("rb"), "sha512").hexdigest()
if digest.lower() != asset["hash"].lower():
    raise RuntimeError("SDK SHA512 mismatch")
destination = root / ".runtime" / "tools" / "dotnet"
destination.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(archive) as z:
    z.extractall(destination)
(root / "src/csharp/global.json").write_text(json.dumps({"sdk": {"version": version, "rollForward": "latestPatch"}}, indent=2) + "\n")
print(f"Installed verified SDK to {destination}", flush=True)
