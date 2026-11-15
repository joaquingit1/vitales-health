"""Build the ISS acoustic levels table from statements in two NASA papers; every quote is checked against the text layer."""
import csv, re, sys
from pathlib import Path
def _clean(t):
    # drop lines that hold only a number: figure axis ticks interleaved with body text in the PDF text layer
    return "\n".join(l for l in t.split("\n") if not re.fullmatch(r"\s*\d+\s*", l))
docs = {"ICES-2024-354": _clean(Path(sys.argv[1]).read_text(encoding="utf-8")), "INTERNOISE-2021-2219": _clean(Path(sys.argv[2]).read_text(encoding="utf-8"))}
out = Path(sys.argv[3])
norm = lambda s: re.sub(r"\s+", " ", s).strip()
def _found(text, quote, max_gap=40):
    # the quote's words must appear in order; up to max_gap foreign words (figure labels and axis ticks that the
    # PDF text layer interleaves with body text) may sit between two consecutive quote words
    # hyphens are dropped for comparison: the text layer splits words and "NC-47.6" at line ends
    tok = lambda s: [w.strip("().,;:").replace("-", "") for w in norm(s.replace("-\n", "")).split(" ")]
    words, q = tok(text), tok(quote)
    for start in (i for i, w in enumerate(words) if w == q[0]):
        pos, ok = start, True
        for token in q[1:]:
            nxt = next((j for j in range(pos + 1, min(pos + 2 + max_gap, len(words))) if words[j] == token), None)
            if nxt is None:
                ok = False; break
            pos = nxt
        if ok:
            return True
    return False
def page_of(doc, quote):
    pages = docs[doc].split("\f")
    hits = [i + 1 for i, p in enumerate(pages) if _found(p, quote)]
    if hits:
        return str(hits[0])
    if _found(docs[doc].replace("\f", " "), quote):
        return "spans pages"
    raise SystemExit(f"quote not found in {doc}: {quote}")
I, N = "ICES-2024-354", "INTERNOISE-2021-2219"
rows = [
 # doc, segment, location, kind, date, date_basis, metric, nc, sil_db, level_dba, quote
 (I, "US", "U.S. Lab", "work area", "2024-01-03", "Figure 2 caption", "spatial average", "", "53", "60", "the U.S. Lab average speech interference level, SIL(4),11 was approximately 53 dB, and the average sound level was approximately 60 dBA"),
 (I, "US", "COF", "work area", "2023-07-06", "text", "spatial average of 5 locations", "50.8", "49.2", "56.8", "decrease from NC-51.1 to NC-50.8, with the SIL increasing from 45.9 dB to 49.2 dB, and the sound level also increasing from 54.1 dBA to 56.8 dBA"),
 (I, "US", "JEM", "work area", "2022-07-01", "text", "spatial average of 7 locations", "53.5", "47.6", "55.6", "increases from NC-48.1 to NC-53.5, because of a peak in the 63 Hz octave band and slight increases from a SIL of 47.5 dB to 47.6 dB, and a sound level from 54.1 dBA to 55.6 dBA"),
 (I, "US", "JLP", "stowage", "", "not stated", "not stated", "46.8", "42.3", "49.7", "The JLP NC, SIL and sound levels are NC-46.8, 42.3 dB, and 49.7 dBA, respectively"),
 (I, "US", "PMM", "stowage", "", "not stated", "not stated", "41.8", "37.9", "46.2", "The PMM NC, SIL and sound levels are NC-41.8, 37.9 dB, and 46.2 dBA, respectively"),
 (I, "US", "Cupola", "work area", "2024-04-08", "text", "not stated", "44.2", "39.7", "49.2", "The Cupola, measured on April 8, 2024, is shown in Fig. 3 to meet the NC-50 requirement with NC, SIL and sound levels of NC-44.2, 39.7 dB, and 49.2 dBA, respectively"),
 (I, "US", "Node 2", "work area", "2023-10-03", "Figure 4 caption", "spatial average of locations 2 to 5", "53.6", "47.4", "57.5", "are NC-53.6, SIL of 47.4 dB, and sound level of 57.5 dBA"),
 (I, "US", "Node 2 crew quarters, starboard (fans high)", "sleep station", "2023-10-03", "Figure 5 caption", "interior", "47.6", "", "51.6", "Overhead (Zenith), and Deck (Nadir) CQs (on high speed setting) are NC-47.6, NC-48.2, NC-52.8, and NC-42.5, respectively, with corresponding sound levels of 51.6 dBA, 51.5 dBA, 53.5 dBA, and 48.5 dBA, respectively"),
 (I, "US", "Node 2 crew quarters, port (fans high)", "sleep station", "2023-10-03", "Figure 5 caption", "interior", "48.2", "", "51.5", "Overhead (Zenith), and Deck (Nadir) CQs (on high speed setting) are NC-47.6, NC-48.2, NC-52.8, and NC-42.5, respectively, with corresponding sound levels of 51.6 dBA, 51.5 dBA, 53.5 dBA, and 48.5 dBA, respectively"),
 (I, "US", "Node 2 crew quarters, overhead/zenith (fans high)", "sleep station", "2023-10-03", "Figure 5 caption", "interior", "52.8", "", "53.5", "Overhead (Zenith), and Deck (Nadir) CQs (on high speed setting) are NC-47.6, NC-48.2, NC-52.8, and NC-42.5, respectively, with corresponding sound levels of 51.6 dBA, 51.5 dBA, 53.5 dBA, and 48.5 dBA, respectively"),
 (I, "US", "Node 2 crew quarters, deck/nadir (fans high)", "sleep station", "2023-10-03", "Figure 5 caption", "interior", "42.5", "", "48.5", "Overhead (Zenith), and Deck (Nadir) CQs (on high speed setting) are NC-47.6, NC-48.2, NC-52.8, and NC-42.5, respectively, with corresponding sound levels of 51.6 dBA, 51.5 dBA, 53.5 dBA, and 48.5 dBA, respectively"),
 (I, "US", "Crew Alternate Sleep Accommodation (CASA) in COF", "sleep station", "2022-04-06", "Figure 5 caption", "interior", "36.6", "", "43.8", "CASA is very quiet (quietest spot on ISS) at NC-36.6 with a sound level of 43.8 dBA"),
 (I, "US", "Node 3", "work area", "2024-04-08", "text", "spatial average", "50.5", "47.1", "55.9", "a decrease in noise from NC-54.6 to NC-50.5, with the SIL decreasing from 49.7 dB to 47.1 dB, and the sound level also decreasing from 58.9 dBA to 55.9 dBA"),
 (I, "RS", "Service Module", "work area", "2022-07-01", "text", "spatial average", "57.9", "55.5", "63.0", "noise levels have remained steady at NC-57.9 and with SIL from 55.7 dB to 55.5 dB, and sound level from 62.9 dBA to 63.0 dBA"),
 (I, "RS", "FGB", "work area", "2024-04-08", "text", "average", "58.5", "52.7", "61.8", "Average acoustic levels from measurements in the FGB on April 8, 2024 are NC-58.5, SIL of 52.7 dB, and sound level of 61.8 dBA"),
 (I, "RS", "MRM1", "work area", "2023-10-03", "text", "average", "60.3", "58.3", "65.2", "the MRM1 on October 3, 2023 are NC-60.3, SIL of 58.3 dB, and sound level of 65.2 dBA"),
 (I, "RS", "MRM2", "work area", "2023-10-03", "text", "average", "57.3", "53.6", "61.9", "Average acoustic levels in the MRM2, measured on October 3, 2023, are NC-57.3, SIL of 53.6 dB, and sound level of 61.9 dBA"),
 (I, "RS", "Node Module (NM)", "work area", "2023-10-03", "text", "not stated", "46", "42.2", "50.5", "The Russian NM acoustic levels were measured on October 3, 2023 to be NC-46, with SIL of 42.2 dB and sound level of 50.5 dBA"),
 (I, "RS", "MLM", "work area", "2024-01-03", "text", "spatial average", "62.5", "58.6", "67.4", "in the MLM are NC-62.5 with SIL of 58.6 and sound level of 67.4 dBA"),
 (I, "RS", "SM starboard kayuta", "sleep station", "2023-10-03", "Figure 8 caption", "interior", "", "", "55.7", "Sound levels of the SM starboard and port kayutas are 55.7 dBA and 59.2 dBA, respectively"),
 (I, "RS", "SM port kayuta", "sleep station", "2023-10-03", "Figure 8 caption", "interior", "", "", "59.2", "Sound levels of the SM starboard and port kayutas are 55.7 dBA and 59.2 dBA, respectively"),
 (I, "RS", "MLM kayuta (fan high)", "sleep station", "2024-01-03", "Figure 8 caption", "interior; order high then low as stated", "", "", "58.2", "The MLM kayuta levels are shown for two fan speeds, high and low, with resulting sound levels of 58.2 dBA and 59 dBA"),
 (I, "RS", "MLM kayuta (fan low)", "sleep station", "2024-01-03", "Figure 8 caption", "interior; order high then low as stated", "", "", "59", "The MLM kayuta levels are shown for two fan speeds, high and low, with resulting sound levels of 58.2 dBA and 59 dBA"),
 (N, "", "one crewmember, crew-worn dosimeter, work period", "crew-worn dosimeter", "2020-04", "Figure 3 source line (Increment 63)", "LEQ16", "", "", "73.0", "LEQ16 during the daytime was measured at 73.0 dBA"),
 (N, "", "one crewmember, crew-worn dosimeter, sleep period", "crew-worn dosimeter", "2020-04", "Figure 3 source line (Increment 63)", "LEQ8", "", "", "59.7", "During the nighttime period, LEQ8 was 59.7 dBA"),
 (N, "US", "crewmembers working mainly in the US segment, Increments 51 to 56", "crew-worn dosimeter", "", "increment range", "LEQ16 average", "", "", "70.8", "70.8 dBA in Increments 51-56 vs. 68.9 dBA in Increments 57-64"),
 (N, "US", "crewmembers working mainly in the US segment, Increments 57 to 64", "crew-worn dosimeter", "", "increment range", "LEQ16 average", "", "", "68.9", "70.8 dBA in Increments 51-56 vs. 68.9 dBA in Increments 57-64"),
 (N, "RS", "crewmembers working mainly in the RS segment, Increments 51 to 56", "crew-worn dosimeter", "", "increment range", "LEQ16 average", "", "", "69.5", "69.5 dBA in Increments 51-56 vs. 68.7 dBA in Increments 57-64"),
 (N, "RS", "crewmembers working mainly in the RS segment, Increments 57 to 64", "crew-worn dosimeter", "", "increment range", "LEQ16 average", "", "", "68.7", "69.5 dBA in Increments 51-56 vs. 68.7 dBA in Increments 57-64"),
 (N, "US", "crewmembers sleeping in the US segment, Increments 51 to 56", "crew-worn dosimeter", "", "increment range", "LEQ8 average", "", "", "52.5", "52.5 dBA in Increments 51-56 vs. 53.9 dBA in Increments 57-64"),
 (N, "US", "crewmembers sleeping in the US segment, Increments 57 to 64", "crew-worn dosimeter", "", "increment range", "LEQ8 average", "", "", "53.9", "52.5 dBA in Increments 51-56 vs. 53.9 dBA in Increments 57-64"),
 (N, "RS", "crewmembers sleeping in the RS segment, Increments 51 to 56", "crew-worn dosimeter", "", "increment range", "LEQ8 average", "", "", "54.6", "54.6 dBA in Increments 51-56 vs. 55.5 dBA in Increments 57-64"),
 (N, "RS", "crewmembers sleeping in the RS segment, Increments 57 to 64", "crew-worn dosimeter", "", "increment range", "LEQ8 average", "", "", "55.5", "54.6 dBA in Increments 51-56 vs. 55.5 dBA in Increments 57-64"),
]
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["document", "segment", "location", "location_kind", "measured", "date_basis", "metric", "nc_rating", "sil_db", "sound_level_dba", "quote", "pdf_page"])
    for r in rows:
        w.writerow([*r, page_of(r[0], r[-1])])
print(len(rows), "rows ok")
