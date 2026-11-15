"""Transcribe tables of the JSC Toxicology Increment 71 report from its pdftotext -layout text layer."""
import csv, re, sys
from pathlib import Path
txt = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
out = Path(sys.argv[2])

def split(line):
    out = []
    for p in re.split(r"\s{2,}", line.strip()):
        if not p:
            continue
        if p.count("See GC-FID") > 1 and p.replace("See GC-FID", "").strip() == "":
            out.extend(["See GC-FID"] * p.count("See GC-FID"))
        else:
            out.append(p)
    return out

S1_SAMPLES = [
    ("AQ240841", "Boeing-CFT", "Cabin", "Nominal Air Sample, Ascent S/N 2102", "2024-06-06", "11:28", 158),
    ("AQ250061", "Crew-8", "Lab", "Nominal air sample, S/N 2013", "2024-05-08", "14:11", 129),
    ("AQ250062", "Crew-8", "Columbus", "Nominal air sample, S/N 2012", "2024-05-08", "14:12", 129),
    ("AQ250063", "Crew-8", "SM", "Nominal air sample, S/N 2019", "2024-06-17", "14:20", 169),
    ("AQ250064", "Crew-8", "Lab", "Nominal air sample, S/N 2035", "2024-06-17", "14:21", 169),
    ("AQ250065", "Crew-8", "Lab", "Nominal air sample, S/N 2043", "2024-07-22", "19:00", 204),
    ("AQ250066", "Crew-8", "JPM", "Nominal air sample, S/N 2046", "2024-07-22", "19:00", 204),
    ("AQ250068", "Crew-8", "Lab", "Nominal air sample, S/N 2017", "2024-09-11", "17:00", 255),
    ("AQ250069", "Crew-8", "Columbus", "Nominal air sample, S/N 2085", "2024-09-11", "17:00", 255),
    ("AQ250067", "Crew-8", "Cygnus", "NG-21 Ingress, S/N 2071", "2024-08-06", "15:25", 219),
]

def parse_block(start, stop, ncols):
    rows, group = [], None
    for i in range(start, stop):
        line = txt[i]
        if not line.strip():
            continue
        if re.match(r"^\S", line) and ("Volatiles" in line):
            group = line.strip(); continue
        if group is None:
            continue
        parts = split(line)
        if parts[0] in ("Total T-Value",) or len(parts) == ncols + 1:
            rows.append((i + 1, group, parts[0], parts[1:]))
        else:
            raise SystemExit(f"line {i+1}: expected {ncols+1} fields, got {len(parts)}: {parts}")
    return rows

def find(prefix, after=0):
    for i in range(after, len(txt)):
        if txt[i].strip().startswith(prefix):
            return i
    raise SystemExit("not found: " + prefix)

def qualify(printed):
    p = printed.replace(",", "").replace(" ", "")
    if re.fullmatch(r"\d+(\.\d+)?", p):
        return p, "", ""
    m = re.fullmatch(r"<(\d+(\.\d+)?)", p)
    if m:
        return "", m.group(1), "below_reporting_limit"
    return "", "", {"SeeGC-FID": "reported_by_gc_fid", "notfound": "not_found", "NR": "not_reported", "ND": "below_reporting_limit"}[p]

# Table S-1 concentrations
s1 = find("TABLE S-1")
s1_end = find("GMT 158", s1)
rows = parse_block(s1, s1_end, 10)
with open(out / "iss_air_inc71_mgsc_concentrations.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["sample_id", "return_mission", "location", "description", "sample_date", "sample_time", "gmt_day_of_year",
                "method_group", "compound", "printed", "value", "reporting_limit", "qualifier", "source_line"])
    for line_no, group, compound, values in rows:
        for sample, printed in zip(S1_SAMPLES, values):
            v, rl, q = qualify(printed)
            w.writerow([*sample, group, compound, printed, v, rl, q, line_no])

# Table S-2A and S-2B T-values
S2A_COLS = [("AQ250061", "180-day"), ("AQ250062", "180-day"), ("AQ250063", "180-day"), ("AQ250064", "180-day"),
            ("AQ250065", "180-day"), ("AQ250066", "180-day"), ("AQ250068", "180-day"), ("AQ250069", "180-day"),
            ("AQ240841", "7-day"), ("AQ240841", "180-day"), ("AQ250067", "7-day"), ("AQ250067", "180-day")]
s2a = find("TABLE S-2A"); s2a_end = find("Total T-Value", s2a) + 1
rows2 = parse_block(s2a, s2a_end, 12)
with open(out / "iss_air_inc71_mgsc_t_values.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["sample_id", "smac_basis", "method_group", "compound", "printed", "value", "qualifier", "source_line"])
    for line_no, group, compound, values in rows2:
        if len(values) != 12:
            raise SystemExit(f"S-2A line {line_no}: {values}")
        for (sample, basis), printed in zip(S2A_COLS, values):
            v, _, q = qualify(printed)
            w.writerow([sample, basis, group, compound, printed, v, q, line_no])

# Table 2 ANITA-2 monthly averages (fixed column positions taken from the header line)
t2 = find("Table 2: Monthly"); t2_end = find("Note: Blank", t2)
header = next(i for i in range(t2, t2_end) if "March" in txt[i])
months = ["2024-03", "2024-04", "2024-05", "2024-06", "2024-07", "2024-08", "2024-09"]
hdr = txt[header]
starts = [hdr.index(m) for m in ["March", "April", "May", "June", "July", "August", "September"]]
with open(out / "iss_air_inc71_anita2_monthly.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["compound", "month", "printed", "value", "qualifier", "source_line"])
    for i in range(header + 1, t2_end):
        line = txt[i]
        if not line.strip() or line.strip() in ("Average", "Increment", "2024") or "2024" in line or line.strip().startswith("Compound"):
            continue
        name = split(line)[0]
        tokens = [(m.start(), m.group()) for m in re.finditer(r"\d+(\.\d+)?", line[len(line) - len(line.lstrip()) + len(name):])]
        offset = len(line) - len(line.lstrip()) + len(name)
        tokens = [(p + offset, t) for p, t in tokens]
        cells = {}
        for pos, tok in tokens:
            end = pos + len(tok)
            centers = starts + [len(hdr.rstrip()) + 4]
            idx = min(range(8), key=lambda k: abs(end - (centers[k] + (8 if k < 7 else 0))))
            if idx in cells:
                raise SystemExit(f"line {i+1}: two values for one column: {line}")
            cells[idx] = tok
        for k, month in enumerate(months + ["increment_average"]):
            printed = cells.get(k, "")
            qual = "" if printed else ("not_detected" if month != "increment_average" else "no_average_printed")
            w.writerow([name, month, printed, printed, qual, i + 1])
print("ok")

# Table S-3A potable water (three samples)
W_SAMPLES = [("WQ240196", "SpX-30", "WPA xPWD Ambient", "2024-04-02"),
             ("WQ240197", "SpX-30", "WPA PWD Hot", "2024-04-24"),
             ("WQ240449", "Crew-8", "WPA xPWD Hot", "2024-09-10")]
s3a = find("TABLE S-3A"); s3a_end = find("Comments: NR= Not Reported", s3a)
start = find("Physical Characteristics", s3a)
with open(out / "iss_potable_water_inc71.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["sample_id", "return_mission", "location", "sample_date", "method_group", "analyte", "units",
                "max_contaminant_level", "mcl_source", "printed", "value", "reporting_limit", "qualifier", "source_line"])
    group = None
    for i in range(start, s3a_end):
        line = txt[i]
        if not line.strip():
            continue
        parts = split(line)
        if len(parts) == 1 and not line.startswith("  ") and "consumption)" not in line:
            group = parts[0]; continue
        if "41000 (tl I max" in line or "water/tl I at pt of" in line:
            continue
        if len(parts) < 6:
            raise SystemExit(f"S-3A line {i+1}: {parts}")
        name, units, values = parts[0], parts[1], parts[-3:]
        if not line.startswith(" "):
            group = "Summary"
        middle = parts[3:-3]
        if name == "Total I":
            mcl, src = "6/0.2", "41000 (tl I max in product water/tl I at pt of consumption)"
        elif len(middle) == 2:
            mcl, src = middle
        elif len(middle) == 0:
            mcl, src = "", ""
        else:
            raise SystemExit(f"S-3A line {i+1}: unexpected middle {middle}")
        for sample, printed in zip(W_SAMPLES, values):
            p = printed.replace(" ", "")
            if p == "SeeVolatiles":
                v, rl, q = "", "", "reported_under_volatiles"
            elif p == "5,83":
                v, rl, q = "", "", "printed_with_comma_decimal_not_parsed"
            else:
                v, rl, q = qualify(printed)
            w.writerow([*sample, group, name, units, mcl, src, printed, v.replace(",", ""), rl, q, i + 1])
print("ok water")
