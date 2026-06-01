#!/usr/bin/env python3
"""Convert legacy .extract.md files to the locked .yaml digest format.

Reads from digests/extracts/*.extract.md (the legacy markdown
intermediate) and writes to digests/records/<friendly>.yaml
(the new YAML interchange format per decision 0027).

Run inside the container:
  python extract_to_yaml.py <input.extract.md> [<output.yaml>]
Or convert a whole directory:
  python extract_to_yaml.py <extracts_dir> <records_dir>
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from digester.markdown_format import parse_extraction_markdown
from digester.yaml_format import parsed_dict_to_digest_yaml


def convert_one(src: Path, dst: Path) -> dict:
    parsed = parse_extraction_markdown(src.read_text())
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(parsed_dict_to_digest_yaml(parsed))
    return {
        "nodes": len(parsed["nodes"]),
        "domain_claims": len(parsed["domain_claims"]),
        "infrastructure_claims": len(parsed["infrastructure_claims"]),
    }


def _strip_extract_md(name: str) -> str:
    return name[: -len(".extract.md")] if name.endswith(".extract.md") else name


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    src = Path(sys.argv[1])
    if src.is_dir():
        if len(sys.argv) < 3:
            print(
                "Need <records_dir> as second arg for directory mode", file=sys.stderr
            )
            sys.exit(2)
        out_dir = Path(sys.argv[2])
        files = sorted(src.glob("*.extract.md"))
        if not files:
            print(f"No .extract.md files in {src}", file=sys.stderr)
            sys.exit(1)
        print(f"Converting {len(files)} files into {out_dir}")
        for f in files:
            stem = _strip_extract_md(f.name)
            dst = out_dir / f"{stem}.yaml"
            counts = convert_one(f, dst)
            print(
                f"  {stem}.yaml  "
                f"nodes={counts['nodes']:>3}  "
                f"domain={counts['domain_claims']:>4}  "
                f"infra={counts['infrastructure_claims']:>4}"
            )
        return

    if len(sys.argv) >= 3:
        dst = Path(sys.argv[2])
    else:
        stem = _strip_extract_md(src.name)
        dst = src.with_name(f"{stem}.yaml")
    counts = convert_one(src, dst)
    print(f"wrote {dst} ({dst.stat().st_size} bytes) {counts}")


if __name__ == "__main__":
    main()
