"""docx -> Markdown sin Word ni dependencias (zipfile + xml). Uso: python tools/docx_to_md.py <dir_docx> <dir_md>"""
import pathlib
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


DECIMAL = set()  # numId con numeración decimal (se rellena por documento)


def para(p):
    t = "".join(x.text or "" if x.tag == W + "t" else "\n" if x.tag == W + "br" else "" for x in p.iter())
    st = p.find(f"{W}pPr/{W}pStyle")
    m = re.match(r"(?i)heading(\d)", st.get(W + "val") if st is not None else "")
    if m and t.strip():
        return "#" * int(m.group(1)) + " " + t.strip()
    num = p.find(f"{W}pPr/{W}numPr/{W}numId")
    if num is not None and t.strip():
        return ("1. " if num.get(W + "val") in DECIMAL else "- ") + t.strip()
    return t.strip()


def convert(src, dst):
    z = zipfile.ZipFile(src)
    DECIMAL.clear()
    if "word/numbering.xml" in z.namelist():
        nx = ET.fromstring(z.read("word/numbering.xml"))
        dec = {a.get(W + "abstractNumId") for a in nx.iter(W + "abstractNum") if a.find(f"{W}lvl/{W}numFmt") is not None and a.find(f"{W}lvl/{W}numFmt").get(W + "val") == "decimal"}
        DECIMAL.update(n.get(W + "numId") for n in nx.iter(W + "num") if n.find(W + "abstractNumId").get(W + "val") in dec)
    body = ET.fromstring(z.read("word/document.xml")).find(W + "body")
    out = []
    for el in body:
        if el.tag == W + "p":
            out.append(para(el))
        elif el.tag == W + "tbl":
            rows = [
                [" ".join(para(p) for p in c.iter(W + "p")).replace("|", "\\|") for c in r.findall(W + "tc")]
                for r in el.findall(W + "tr")
            ]
            if rows:
                out.append("| " + " | ".join(rows[0]) + " |\n|" + "---|" * len(rows[0]))
                out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    for n in z.namelist():
        if n.startswith("word/media/"):
            (dst.parent / "img").mkdir(exist_ok=True)
            name = f"{dst.stem}_{pathlib.Path(n).name}"
            (dst.parent / "img" / name).write_bytes(z.read(n))
            out.append(f"![{name}](img/{name})")  # shortcut: van al final, no en su posición
    dst.write_text(re.sub(r"\n{3,}", "\n\n", "\n\n".join(out)) + "\n", encoding="utf-8")


if __name__ == "__main__":
    a, b = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    b.mkdir(parents=True, exist_ok=True)
    for f in sorted(a.glob("*.docx")):
        convert(f, b / (f.stem + ".md"))
        print(f.name)
