"""Read-only verification of bundle files and optionally every archived member."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def verify(root: Path, archives: bool = False):
    manifest = json.loads((root / "BUNDLE_MANIFEST.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        path = root / item["path"]
        content = path.read_bytes()
        if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"File mismatch: {item['path']}")
    members_checked = 0
    if archives:
        index = json.loads((root / "audit/ARCHIVE_INDEX.json").read_text(encoding="utf-8"))
        for entry in index:
            with zipfile.ZipFile(root / entry["archive"]) as z:
                if set(z.namelist()) != {m["path"] for m in entry["members"]}:
                    raise ValueError(f"Archive member list mismatch: {entry['archive']}")
                for member in entry["members"]:
                    content = z.read(member["path"])
                    if len(content) != member["bytes"] or hashlib.sha256(content).hexdigest() != member["sha256"]:
                        raise ValueError(f"Archive member mismatch: {member['path']}")
                    members_checked += 1
    result = {"ok": True, "files_verified": len(manifest["files"]), "archive_members_verified": members_checked}
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archives", action="store_true")
    args = parser.parse_args()
    verify(Path(__file__).resolve().parent.parent, args.archives)
