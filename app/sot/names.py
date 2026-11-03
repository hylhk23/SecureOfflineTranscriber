"""Names from your documents (version 2.1.2): read an agenda, attendee list or email and find the people,
organisations and special terms in it, to use as Whisper hotwords.

Reads .docx (Word), .xlsx (Excel), .pdf, .txt, .csv, .md, .eml (saved e-mail) and .html, or pasted text.
Everything runs on this PC with simple rules (no AI model): attendee-list columns, e-mail addresses,
runs of capitalised words, CamelCase and acronyms, and short Chinese names. The user reviews the result."""
import csv
import email
import email.policy
import html
import io
import os
import re
import zipfile
from xml.etree import ElementTree as ET

SUPPORTED = (".docx", ".xlsx", ".xlsm", ".pdf", ".txt", ".csv", ".md", ".eml", ".html", ".htm")
FILE_FILTER = "Documents (*.docx *.xlsx *.xlsm *.pdf *.txt *.csv *.md *.eml *.html *.htm);;All files (*)"

# words that are capitalised for other reasons (sentence starts, headings, calendar, e-mail and meeting words)
COMMON = set("""
a about above after again against all also am an and any are as at be because been before being below between
both but by can could did do does doing down during each few for from further had has have having he her here
hers him his how i if in into is it its just me more most my no nor not now of off on once only or other our
ours out over own same she should so some such than that the their them then there these they this those
through to too under until up very was we were what when where which while who whom why will with would you
your yours yes ok okay please thanks thank regards best kind dear hi hello hey good morning afternoon evening
welcome cheers sincerely warm yours re fw fwd sent from to cc bcc subject date time location venue address
room floor building street road avenue suite phone tel mobile fax email e-mail website www attachment
attachments note notes agenda meeting meetings minutes call calls session sessions panel keynote break
lunch dinner breakfast coffee reception networking registration introduction introductions opening closing
remarks presentation presentations discussion discussions q&a qa questions answers update updates item items
action actions next steps follow follow-up summary background overview objective objectives purpose topic
topics attendees attendee participants participant speakers speaker host hosts guests guest team teams
monday tuesday wednesday thursday friday saturday sunday january february march april may june july august
september october november december today tomorrow yesterday week weeks month months year years day days
am pm noon midnight local hours hour minute minutes
mr mrs ms miss dr prof sir madam mme mlle m monsieur madame senhor senhora sr sra dra don dona
director manager president vice chair chairman chairwoman chairperson ceo cfo coo cto cmo officer chief head
executive senior junior lead leader coordinator assistant adviser advisor specialist analyst consultant
associate partner founder co-founder owner secretary minister deputy mayor councillor councilor commissioner
ambassador consul representative delegate professor student intern staff member members board
please see attached confirm confirmed tbc tbd n/a none other others total yes no
name names organisation organization company title position role job contact contacts details list schedule programme program
le la les un une des du de et pour avec sur dans par au aux en est sont nous vous ils elles bonjour merci
o os as um uma dos das do da em no na para com por obrigado obrigada bom dia boa tarde
the
""".split())
TITLES = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "madam", "mme", "mlle", "m", "monsieur", "madame",
          "senhor", "senhora", "sr", "sra", "dra", "dear", "hi", "hello"}
PARTICLES = {"da", "de", "do", "dos", "das", "del", "della", "di", "du", "van", "von", "der", "den", "la", "le",
             "y", "e", "bin", "al", "el", "ter", "st", "st."}
CONNECTORS = {"of", "and", "&", "for"}
ORG_WORDS = {"inc", "inc.", "ltd", "ltd.", "llc", "llp", "plc", "gmbh", "ag", "sa", "s.a.", "sas", "bv", "nv", "corp",
             "corp.", "corporation", "company", "co", "co.", "group", "holdings", "bank", "university", "college",
             "institute", "embassy", "consulate", "council", "ministry", "agency", "association", "foundation",
             "authority", "department", "government", "city", "municipality", "chamber", "summit", "partners",
             "capital", "ventures", "labs", "technologies", "technology", "systems", "solutions", "consulting",
             "services", "service", "industries", "international", "network", "society", "centre", "center",
             "hospital", "fund", "trust", "commission", "office", "municipality", "câmara", "camara", "board",
             "chamber", "secretariat", "bureau", "court", "school", "academy", "museum", "factory", "hub"}
COMMON_ACRONYMS = {"CEO", "CFO", "COO", "CTO", "CMO", "VP", "HR", "IT", "AI", "PDF", "FYI", "ASAP", "AM", "PM", "OK",
                   "TBC", "TBD", "N/A", "QA", "RSVP", "CC", "BCC", "FW", "FWD", "RE", "USA", "UK", "EU", "US", "PS"}
CJK = r"㐀-䶿一-鿿豈-﫿"
CJK_ORG = ("公司", "集團", "集团", "銀行", "银行", "大學", "大学", "政府", "協會", "协会", "基金", "中心", "學院", "学院",
           "商會", "商会", "總署", "总署", "局", "署", "部", "會", "会")
HEADER_NAME = re.compile(r"^(full\s*)?name|^attendee|^participant|^guest|^speaker|^contact|^姓名|^名字|^與會者|^出席", re.I)
HEADER_FIRST = re.compile(r"^(first|given)\s*name|^prénom|^nome$", re.I)
HEADER_LAST = re.compile(r"^(last|family|sur)\s*name|^surname|^nom$|^apelido|^sobrenome", re.I)
HEADER_SKIP = re.compile(r"^(title|position|role|job|function|phone|tel|mobile|e-?mail|address|country|city|"
                         r"職位|职位|職銜|职衔|頭銜|头衔|電話|电话)", re.I)
HEADER_ORG = re.compile(r"^(company|organi[sz]ation|employer|firm|institution|affiliation|agency|公司|機構|机构|單位|单位)", re.I)
WORD = r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ž][A-Za-zÀ-ÖØ-öø-ÿĀ-ž'’\-]*"
EMAIL = re.compile(r"([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
DISPLAY = re.compile(r'"([^"]{2,60})"\s*<[^<>@\s]+@[^<>\s]+>|([^"<>\n,;:]{2,60}?)\s*<[^<>@\s]+@[^<>\s]+>')


class DocumentError(Exception):
    pass


# ====================================================================== reading
def read_document(path):
    """Returns (text, rows): running text, and table rows (lists of cell strings) from tables and sheets."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".docx":
            return _read_docx(path)
        if ext in (".xlsx", ".xlsm"):
            return "", _read_xlsx(path)
        if ext == ".pdf":
            return _read_pdf(path), []
        if ext == ".csv":
            raw = _read_text(path)
            return "", [r for r in csv.reader(io.StringIO(raw))]
        if ext == ".eml":
            return _read_eml(path)
        if ext in (".html", ".htm"):
            return _strip_html(_read_text(path)), []
        if ext == ".xls":
            raise DocumentError("Old Excel files (.xls) cannot be read. In Excel, save it as .xlsx or .csv.")
        if ext == ".msg":
            raise DocumentError("Outlook .msg files cannot be read. In Outlook use File > Save As > Text Only, "
                                "or copy the e-mail and use Paste text.")
        if ext == ".doc":
            raise DocumentError("Old Word files (.doc) cannot be read. In Word, save it as .docx.")
        return _read_text(path), []
    except DocumentError:
        raise
    except Exception as ex:
        raise DocumentError(f"{os.path.basename(path)} could not be read: {ex}")


def _read_text(path):
    data = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp1252", "big5", "gb18030"):
        try:
            text = data.decode(enc)
            if enc == "utf-16" and not data[:2] in (b"\xff\xfe", b"\xfe\xff"):
                continue
            return text
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _read_docx(path):
    from docx import Document
    doc = Document(path)
    text = "\n".join(p.text for p in doc.paragraphs)
    rows = []
    for t in doc.tables:
        for r in t.rows:
            cells = []
            for c in r.cells:
                v = c.text.strip()
                if not cells or cells[-1] != v:  # merged cells repeat their text
                    cells.append(v)
            rows.append(cells)
    for sec in doc.sections:  # headers often carry the event or company name
        for p in sec.header.paragraphs:
            text += "\n" + p.text
    return text, rows


def _read_xlsx(path):
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows = []
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        sheets = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        for sh in sheets:
            root = ET.fromstring(z.read(sh))
            for row in root.iter(f"{{{ns['m']}}}row"):
                cells = []
                for c in row.findall("m:c", ns):
                    t = c.get("t")
                    v = c.find("m:v", ns)
                    if t == "s" and v is not None:
                        cells.append(shared[int(v.text)] if v.text and int(v.text) < len(shared) else "")
                    elif t == "inlineStr":
                        cells.append("".join(x.text or "" for x in c.iter(f"{{{ns['m']}}}t")))
                    elif t == "str" and v is not None:
                        cells.append(v.text or "")
                    else:
                        cells.append("")  # numbers and dates are not names
                if any(cells):
                    rows.append(cells)
    return rows


def _read_pdf(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise DocumentError("PDF reading is not available in this installation.")
    r = PdfReader(path)
    if r.is_encrypted:
        try:
            r.decrypt("")
        except Exception:
            raise DocumentError("This PDF is password-protected.")
    text = "\n".join((p.extract_text() or "") for p in r.pages[:200])
    if not text.strip():
        raise DocumentError("This PDF has no text layer (it is a scanned image).")
    return text


def _read_eml(path):
    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)
    head = []
    for h in ("From", "To", "Cc", "Subject"):
        v = msg.get(h)
        if v:
            head.append(f"{h}: {v}")
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        text = body.get_content()
        if body.get_content_type() == "text/html":
            text = _strip_html(text)
    return "\n".join(head) + "\n\n" + text, []


def _strip_html(s):
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(s)


# ====================================================================== finding names
def extract(text, rows=None):
    """Returns candidates: [{"text", "kind" (Person/Name/Organisation/Term), "count", "checked", "why"}].
    Person = from an attendee list or e-mail; Name = capitalised words (a person or a place)."""
    found = {}

    def add(name, kind, checked, why):
        name = re.sub(r"\s+", " ", name).strip(" ,;:.-–—()[]\"'’")
        if len(name) < 2 or len(name) > 60 or not re.search(r"[A-Za-zÀ-ÿ]|[" + CJK + "]", name):
            return
        key = name.lower()
        old = found.get(key)
        rank = {"Person": 0, "Name": 1, "Organisation": 2, "Term": 3}
        if old is None:
            found[key] = {"text": name, "kind": kind, "checked": checked, "why": why}
        else:
            old["checked"] = old["checked"] or checked
            if rank[kind] < rank[old["kind"]] and checked:
                old["kind"], old["why"] = kind, why

    rows = rows or []
    used = _from_rows(rows, add)
    free_cells = [str(c).strip() for i, r in enumerate(rows) if i not in used for c in r if c and str(c).strip()]
    all_cells = [str(c).strip() for r in rows for c in r if c and str(c).strip()]
    flat = text + "\n" + "\n".join(all_cells)
    _from_emails(flat, add)
    _from_capitals(text + "\n" + "\n".join(free_cells), add)
    _from_cjk(text, free_cells, add)
    # drop single words already inside a longer name, and count how often each appears
    multi = [c["text"].lower() for c in found.values() if " " in c["text"]]
    out = []
    for c in found.values():
        if " " not in c["text"] and not c["checked"] and any(re.search(rf"\b{re.escape(c['text'].lower())}\b", m)
                                                              for m in multi):
            continue
        c["count"] = max(1, len(re.findall(re.escape(c["text"]), flat, re.I)))
        out.append(c)
    kinds = {"Person": 0, "Name": 1, "Organisation": 2, "Term": 3}
    out.sort(key=lambda c: (not c["checked"], kinds[c["kind"]], -c["count"], c["text"].lower()))
    return out


def _header(row):
    cols = {"name": None, "first": None, "last": None, "org": None}
    hits = 0
    for j, h in enumerate(str(c or "").strip() for c in row):
        if not h or len(h) > 30:
            continue
        for key, rx in (("first", HEADER_FIRST), ("last", HEADER_LAST), ("org", HEADER_ORG), ("name", HEADER_NAME)):
            if rx.search(h) and cols[key] is None:
                cols[key] = j
                hits += 1
                break
    return (cols, hits) if hits else (None, 0)


def _from_rows(rows, add):
    """Attendee lists: a header row with Name / First name / Last name / Company columns, then one row each.
    Returns the numbers of the rows used this way (headers included), so they are not scanned again."""
    cols = None
    used = set()
    for i, r in enumerate(rows):
        h, hits = _header(r)
        if h and (cols is None or hits >= 2):  # a (new) header row
            cols = h
            used.add(i)
            continue
        if not cols:
            continue
        used.add(i)
        cell = lambda k: str(r[cols[k]]).strip() if cols[k] is not None and cols[k] < len(r) and r[cols[k]] else ""
        person = cell("name") or " ".join(x for x in (cell("first"), cell("last")) if x)
        person = re.sub(r"^(?:%s)\.?\s+" % "|".join(TITLES), "", person, flags=re.I)
        if person and len(person.split()) <= 5 and not re.search(r"\d|@", person):
            add(person, "Person", True, "attendee list")
        org = cell("org")
        if org and not org.isdigit():
            add(org, "Organisation", True, "attendee list")
    return used


def _from_emails(text, add):
    for m in DISPLAY.finditer(text):  # "Marta Quinteiro" <marta.quinteiro@...>, Quinteiro, Marta <...>
        d = (m.group(1) or m.group(2) or "").strip().strip("'")
        if "," in d and len(d.split(",")) == 2:
            last, first = [x.strip() for x in d.split(",")]
            d = f"{first} {last}"
        d = re.sub(r"^(to|from|cc|bcc)\s*:\s*", "", d, flags=re.I)
        if re.fullmatch(rf"{WORD}(\s+{WORD}){{0,3}}", d) and d.lower() not in COMMON:
            add(d, "Person", len(d.split()) >= 2, "e-mail sender or recipient")
    for m in EMAIL.finditer(text):  # marta.quinteiro@... -> Marta Quinteiro (to be checked: spelling may differ)
        parts = re.split(r"[._]", m.group(1))
        if len(parts) == 2 and all(p.isalpha() and len(p) >= 2 for p in parts):
            add(" ".join(p.capitalize() for p in parts), "Person", False, "e-mail address")


def _from_capitals(text, add):
    for line in text.splitlines():
        tokens = re.findall(rf"{WORD}|[.!?:;,()\[\]\"“”]|&", line)
        run = []
        sentence_start = True

        def flush():
            nonlocal run
            _take_run(run, add)
            run = []

        for tok in tokens:
            if tok in ".!?:;":
                flush()
                sentence_start = True
                continue
            if not re.match(WORD, tok) and tok != "&":
                flush()
                continue
            low = tok.lower()
            cap = tok[:1].isupper()
            if cap or (run and (low in PARTICLES or low in CONNECTORS or tok == "&")):
                run.append((tok, sentence_start and not run))
            else:
                flush()
            sentence_start = False
        flush()


def _take_run(run, add):
    """A run of capitalised words. 'of', 'and', 'for', '&' stay inside only after an organisation word
    ('Embassy of Canada', 'Bank of Montreal'); otherwise they separate two names ('George Rocha and Sandra Lopes')."""
    if not run:
        return
    parts, cur = [], []
    for i, (w, at_start) in enumerate(run):
        low = w.lower()
        if (low in CONNECTORS or w == "&") and not (cur and cur[-1][0].lower().rstrip(".,") in ORG_WORDS):
            if cur:
                parts.append(cur)
            cur = []
            continue
        cur.append((w, at_start))
    if cur:
        parts.append(cur)
    for p in parts:
        _take_part(p, add)


def _take_part(run, add):
    words = [w for w, _ in run]
    first_at_start = run[0][1]
    # trim connectors and particles at the ends, and common words / titles at the start and end
    while words and (words[0].lower() in COMMON or words[0].lower() in CONNECTORS or words[0].lower() in PARTICLES
                     or words[0].rstrip(".").lower() in TITLES):
        words.pop(0)
        first_at_start = False
    while words and (words[-1].lower() in COMMON or words[-1].lower() in CONNECTORS or
                     words[-1].lower() in PARTICLES):
        words.pop()
    if not words:
        return
    if len(words) == 1:
        w = words[0]
        if re.match(r"^[A-Z][a-z]+[A-Z]\w*$", w) or re.match(r"^[A-Z]{2,6}s?$", w):  # CamelCase, ACRONYM
            if w.rstrip("s") not in COMMON_ACRONYMS:
                add(w, "Term", re.match(r"^[A-Z][a-z]+[A-Z]", w) is not None, "special spelling")
        elif not first_at_start and len(w) >= 3 and w.lower() not in COMMON and not w.isupper():
            add(w, "Name", False, "single capitalised word")
        return
    if len(words) > 6 or all(w.isupper() for w in words):  # too long, or a heading in capitals
        return
    lows = [w.lower().rstrip(".,") for w in words]
    if any(w in ORG_WORDS for w in lows) or "&" in words:
        add(" ".join(words), "Organisation", True, "name of an organisation")
    elif len(words) <= 4:
        add(" ".join(words), "Name", True, "capitalised name")
    else:
        add(" ".join(words), "Term", False, "capitalised words")


def _from_cjk(text, cells, add):
    lines = [l.strip() for l in text.splitlines()]
    for item in cells + lines:
        for piece in re.split(r"[\s,，、;；/|()（）:：]+", item):
            if not re.fullmatch(f"[{CJK}]+", piece or ""):
                continue
            if any(piece.endswith(s) for s in CJK_ORG) and 3 <= len(piece) <= 14:
                add(piece, "Organisation", True, "Chinese organisation")
            elif 2 <= len(piece) <= 4 and piece in cells:
                add(piece, "Person", True, "Chinese name in a table")
            elif 2 <= len(piece) <= 3 and item == piece:
                add(piece, "Person", False, "short Chinese line")


def word_count(names):
    return sum(len(n.split()) if not re.search(f"[{CJK}]", n) else 1 for n in names)


def merge_hotwords(existing, new, first=True):
    """Add names to a hotword list without duplicates (case-insensitive); new ones first by default."""
    seen = set()
    out = []
    for n in (list(new) + list(existing)) if first else (list(existing) + list(new)):
        k = n.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(n.strip())
    return out
