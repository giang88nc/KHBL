"""QR engine CLI: analyze, benchmark against D, or explicitly build approved context."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from apps.pos.cccd import normalize_cccd_group, analyze_text, split_raw, _ngay
from apps.pos.cccd_context import build_model, load_model


def workbook_rows(path):
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(n.itertext()) for n in
                      ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", ns)]
        for row in ET.fromstring(archive.read("xl/worksheets/sheet1.xml")).findall(".//s:sheetData/s:row", ns):
            values = {}
            for cell in row.findall("s:c", ns):
                column = cell.get("r", "").rstrip("0123456789")
                if column not in ("B", "C", "D"):
                    continue
                v = cell.find("s:v", ns)
                text = v.text if v is not None else ""
                if cell.get("t") == "s":
                    text = shared[int(text)]
                elif cell.get("t") == "inlineStr":
                    text = "".join(cell.find("s:is", ns).itertext())
                if text:
                    values[column] = text
            if "B" in values or "C" in values:
                yield int(row.get("r")), values


def approved_records(rows):
    records = []
    for row, values in rows:
        gold = values.get("D", "").strip().split("|")
        if (len(gold) != 7 or not gold[0].isascii() or not gold[0].isdigit()
                or len(gold[0]) != 12 or not _ngay(gold[3]) or not _ngay(gold[6])
                or (gold[1] and (not gold[1].isascii() or not gold[1].isdigit() or len(gold[1]) != 9))
                or _ngay(gold[6]) < _ngay(gold[3])
                or gold[4] not in ("Nam", "Nữ") or not gold[2]):
            raise ValueError(f"Invalid gold QR in D{row}; model was not changed")
        for column in ("B", "C"):
            if column not in values:
                continue
            parts, _, errors = split_raw(values[column])
            if errors or any(parts[i] != gold[i] for i in (0, 1, 3, 6)):
                raise ValueError(f"Identity/date mismatch at {column}{row}; model was not changed")
            records.append((row, parts, gold))
    return records


def xlsx_rows(path):
    for row, values in workbook_rows(path):
        yield {"source_row": row, **normalize_cccd_group([values[c] for c in ("B", "C") if c in values])}


def benchmark(rows, model, use_approved, held_out=False):
    scores = {mode: {"total": 0, "parsed": 0, "exact_qr": 0,
                     "fields": {key: 0 for key in ("ho_ten", "gioi_tinh", "dia_chi")},
                     "mismatch_rows": []} for mode in ("B", "C", "B+C")}
    records = approved_records(rows) if held_out else []
    for row, values in rows:
        if held_out:
            model = build_model([r for r in records if r[0] != row], "held-out")
        gold = values["D"].strip()
        target = gold.split("|")
        for mode in scores:
            scans = [values[c] for c in mode.split("+") if c in values]
            if not scans:
                continue
            result = normalize_cccd_group(scans, context_model=model, use_approved=use_approved,
                                          learned_rules=None if use_approved else [])
            score = scores[mode]
            score["total"] += 1
            score["parsed"] += bool(result["data"])
            score["exact_qr"] += result.get("normalized_qr") == gold
            if result.get("normalized_qr") != gold:
                score["mismatch_rows"].append(row)
            if result["data"]:
                for key, expected in (("ho_ten", target[2]), ("dia_chi", target[5]),
                                      ("gioi_tinh", {"Nam": "1", "Nữ": "0"}[target[4]])):
                    score["fields"][key] += result["data"][key] == expected
    return scores


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlsx", help="Mẫu B/C và đáp án D trên worksheet đầu")
    parser.add_argument("--summary", action="store_true", help="Chỉ in số lượng, không in dữ liệu cá nhân")
    parser.add_argument("--benchmark", action="store_true", help="So sánh chính xác toàn QR/từng trường với D; không ghi")
    parser.add_argument("--leave-one-out", action="store_true", help="Thêm đánh giá bỏ cả thẻ đang thử khỏi ngữ cảnh")
    parser.add_argument("--build-context", type=Path, help="Ghi model cục bộ từ cột D đã được người dùng duyệt")
    parser.add_argument("--approve-column-d", action="store_true", help="Xác nhận người dùng đã duyệt D là đáp án")
    args = parser.parse_args()
    if args.build_context:
        if not args.xlsx or not args.approve_column_d:
            parser.error("--build-context requires --xlsx and explicit --approve-column-d")
        records = approved_records(list(workbook_rows(args.xlsx)))
        model = build_model(records, hashlib.sha256(Path(args.xlsx).read_bytes()).hexdigest())
        args.build_context.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.build_context.with_suffix(".tmp")
        temporary.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(args.build_context)
        print(json.dumps({"gold_rows": model["gold_rows"], "approved_scans": len(model["approved"]),
                          "lexicon_words": len(model["lexicon"]), "path": str(args.build_context)}, ensure_ascii=False))
        return
    if args.benchmark:
        if not args.xlsx:
            parser.error("--benchmark requires --xlsx")
        rows = list(workbook_rows(args.xlsx))
        approved_records(rows)  # Validate the complete gold set before measuring.
        model = load_model()
        report = {"decoder_without_example_lookup": benchmark(rows, model, False),
                  "with_approved_examples": benchmark(rows, model, True)}
        if args.leave_one_out:
            report["leave_one_card_out"] = benchmark(rows, model, False, held_out=True)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if args.xlsx:
        results = list(xlsx_rows(args.xlsx))
    else:
        data = json.load(sys.stdin)
        if isinstance(data.get("text"), str):
            print(json.dumps(analyze_text(data["text"], data.get("scope", "auto")), ensure_ascii=False, indent=2))
            return
        results = [normalize_cccd_group(data.get("scans"), data.get("trusted_context"))]
    if args.summary:
        output = {"rows": len(results), "parsed": sum(r["data"] is not None for r in results),
                  "statuses": {s: sum(r["status"] == s for r in results)
                               for s in ("AUTO_ACCEPT", "ACCEPT_WITH_WARNING", "MANUAL_REVIEW")}}
    else:
        output = results
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
