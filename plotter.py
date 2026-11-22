"""
plotter.py — publication-style plotting core for SciPlot.

Runs in the browser via Pyodide (loaded by index.html), and also runs
unchanged in a normal Python install, so any figure made on the site can
be reproduced from a script:

    python plotter.py DATAFILE SETTINGS.json [figure.pdf]
"""
import io
import csv
import json
import math
import base64

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import AutoMinorLocator

try:                      # seaborn: palettes, themes, distribution plots, CI bands
    import seaborn as sns
except ImportError:       # plotter.py still works without it (matplotlib-only features)
    sns = None

DATA = {"df": None}


def _clean(o):
    """Replace NaN / ±inf with None (JSON null) so the browser can parse results."""
    if isinstance(o, float):
        return o if np.isfinite(o) else None
    if isinstance(o, (np.floating,)):
        return float(o) if np.isfinite(o) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def _dumps(o):
    return json.dumps(_clean(o), allow_nan=False)

# Okabe & Ito (2008) colour-blind-safe palette, black first
PALETTES = {
    "okabe-ito": ["#000000", "#E69F00", "#56B4E9", "#009E73",
                  "#D55E00", "#0072B2", "#CC79A7", "#F0E442"],
    "tab10": [matplotlib.colors.to_hex(c) for c in plt.get_cmap("tab10").colors],
    "viridis": None,   # sampled per number of series
    "greyscale": ["#000000", "#555555", "#888888", "#AAAAAA", "#333333", "#777777"],
}
SNS_PALETTES = {"deep", "muted", "bright", "pastel", "dark", "colorblind", "Set1", "Set2",
                "Set3", "Paired", "tab10", "husl", "hls", "rocket", "mako", "flare", "crest",
                "magma", "icefire", "vlag", "cubehelix"}
SNS_CMAPS = {"rocket", "mako", "flare", "crest", "icefire", "vlag"}
SNS_THEMES = {"ticks", "white", "whitegrid", "darkgrid"}
CATEGORICAL = {"bar", "box", "violin", "strip", "swarm"}
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "<", ">", "h", "p", "*"]
LINESTYLES = {"-": "-", "--": "--", ":": ":", "-.": "-."}
REF_STYLES = {"mean": "-.", "median": ":", "wmean": (0, (6, 2))}

FIG_SIZES = {          # inches (w, h)
    "single": (3.5, 2.6),     # single journal column (~89 mm)
    "onehalf": (5.5, 3.8),    # 1.5 column (~140 mm)
    "double": (7.2, 4.4),     # full page width (~183 mm)
    "screen": (8.0, 5.0),
}


# ----------------------------------------------------------------- loading
def _read_lines(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return [ln.rstrip("\r\n") for ln in f]


def _is_comment(ln):
    return ln.lstrip().startswith(("#", "%", "//"))


def _parse_lines(lines, auto_trim=False):
    if not lines:
        raise ValueError("No data rows found (check the header row setting).")
    sample = "\n".join(lines[len(lines) // 3:][:50])   # sniff the table body, not a metadata block
    try:
        sep = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        sep = None
    if sep is None or sum(ln.count(sep) > 0 for ln in lines) < 0.5 * len(lines):
        sep = r"\s+"  # whitespace-delimited (typical .txt / instrument output)
    if auto_trim and len(lines) > 4:
        cnt = [len(ln.split(sep)) if sep != r"\s+" else len(ln.split()) for ln in lines]
        tail = cnt[len(cnt) // 2:]
        modal = max(set(tail), key=tail.count)
        first = next((i for i, n in enumerate(cnt) if n == modal), 0)
        lines = lines[first:]
    return pd.read_csv(io.StringIO("\n".join(lines)), sep=sep, header=None,
                       engine="python", skipinitialspace=True)


def _is_num(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return True
    try:
        float(str(v).strip())
        return True
    except ValueError:
        return False


def _apply_header(df, header):
    if header == "auto":
        row0 = df.iloc[0].tolist()
        rest_numeric = (df.iloc[1:].apply(pd.to_numeric, errors="coerce").notna().mean() > 0.5)
        header = "yes" if any((not _is_num(v)) and rest_numeric.iloc[i]
                              for i, v in enumerate(row0)) else "no"
    if header == "yes":
        names = df.iloc[0].tolist()
        df = df.iloc[1:].reset_index(drop=True)
    else:
        names = [None] * df.shape[1]
    cols, seen = [], set()
    for i, n in enumerate(names):
        n = str(n).strip() if n is not None and str(n).strip() not in ("", "nan") else f"Column {i + 1}"
        base, k = n, 2
        while n in seen:
            n, k = f"{base} ({k})", k + 1
        seen.add(n)
        cols.append(n)
    df.columns = cols
    return df


def _coerce(df):
    df = df.dropna(how="all").dropna(axis=1, how="all")
    for c in df.columns:
        num = pd.to_numeric(df[c], errors="coerce")
        if num.notna().sum() >= 0.5 * df[c].notna().sum():
            df[c] = num
        else:
            df[c] = df[c].astype(str).str.strip().replace({"nan": np.nan, "": np.nan})
    return df.dropna(how="all").reset_index(drop=True)


def _sheet_frame(xl, sheet, header, headerrow, skipafter):
    raw = xl.parse(sheet, header=None)
    if header == "row":
        raw = raw.iloc[headerrow - 1:]
    if raw.dropna(how="all").empty:
        return None
    df = _apply_header(raw.dropna(how="all").reset_index(drop=True), "yes" if header == "row" else header)
    if skipafter and header == "row":
        df = df.iloc[int(skipafter):]
    return df


def load_file(path, name, header="auto", sheet=None, headerrow=1, skipafter=0, allsheets=False,
              sheetcol="Sheet"):
    """Load csv / tsv / txt / dat / xlsx / xls into the active DataFrame.

    header: "auto" | "yes" (first row) | "no" | "row" (use row `headerrow`, 1-based,
    ignoring everything above it). `skipafter` drops rows straight after the header
    (e.g. a units row). allsheets stacks every sheet that has the same columns as the
    majority, adding a "Sheet" column with the sheet name (numeric names become numbers).
    """
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    headerrow = max(1, int(headerrow or 1))
    sheets = []
    DATA["tables"] = {}
    DATA["load"] = {"path": path, "header": header, "headerrow": headerrow, "skipafter": skipafter,
                    "allsheets": bool(allsheets)}
    DATA["calc"] = "[]"
    DATA["sheetdf"] = {}
    if ext in ("xlsx", "xlsm", "xls", "ods"):
        xl = pd.ExcelFile(path)
        sheets = xl.sheet_names
        for sh in sheets:          # every sheet is also available as a source for extra series
            try:
                f = _sheet_frame(xl, sh, "auto", 1, 0)
                if f is not None:
                    DATA["tables"][sh] = _coerce(f)
            except Exception:
                pass
        sheet = sheet if sheet in sheets else sheets[0]
        raw = xl.parse(sheet, header=None)
        raw_rows = [[i + 1, "  |  ".join(_fmt_cell(v) for v in r if not (isinstance(v, float) and np.isnan(v)))]
                    for i, r in enumerate(raw.head(40).itertuples(index=False))]
        if header == "row":
            raw = raw.iloc[headerrow - 1:]
        if allsheets and len(sheets) > 1:
            frames = {sh: _sheet_frame(xl, sh, header, headerrow, skipafter) for sh in sheets}
            frames = {k: v for k, v in frames.items() if v is not None}
            sigs = [tuple(v.columns) for v in frames.values()]
            common = max(set(sigs), key=sigs.count)
            parts = []
            for sh, v in frames.items():
                if tuple(v.columns) == common:
                    v = v.copy()
                    v.insert(0, (sheetcol or "Sheet").strip() or "Sheet", sh)
                    parts.append(v)
            df = _coerce(pd.concat(parts, ignore_index=True))
            skipped = [sh for sh, v in frames.items() if tuple(v.columns) != common]
            return _finish_load(df, raw_rows, sheets, None, skipped)
    else:
        lines = _read_lines(path)
        raw_rows = [[i + 1, ln[:200]] for i, ln in enumerate(lines[:40])]
        sheet = None
        if header == "row":
            body = [ln for ln in lines[headerrow - 1:] if ln.strip()]
            if body:
                body[0] = body[0].lstrip("#%/ \t")   # header written as a comment line
        else:
            body = [ln for ln in lines if ln.strip() and not _is_comment(ln)]
        raw = _parse_lines(body, auto_trim=header == "auto")
    df = _apply_header(raw.reset_index(drop=True), "yes" if header == "row" else header)
    if skipafter and header == "row":
        df = df.iloc[int(skipafter):]
    df = _coerce(df)
    return _finish_load(df, raw_rows, sheets, sheet, [])


# ------------------------------------------------------------ formulas
def _wrap(x, period):
    x = np.asarray(x, float)
    return (x + period / 2) % period - period / 2


FORMULA_FUNCS = {
    "pi": np.pi, "e": np.e,
    "sin": np.sin, "cos": np.cos, "tan": np.tan, "arcsin": np.arcsin, "arccos": np.arccos,
    "arctan": np.arctan, "arctan2": np.arctan2, "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan,
    "atan2": np.arctan2,
    "sind": lambda x: np.sin(np.radians(x)), "cosd": lambda x: np.cos(np.radians(x)),
    "tand": lambda x: np.tan(np.radians(x)),
    "degrees": np.degrees, "radians": np.radians,
    "sqrt": np.sqrt, "abs": np.abs, "log": np.log, "ln": np.log, "log10": np.log10, "exp": np.exp,
    "round": np.round, "floor": np.floor, "ceil": np.ceil, "sign": np.sign,
    "min": np.minimum, "max": np.maximum, "clip": np.clip, "where": np.where, "mod": np.mod,
    "wrap90": lambda x: _wrap(x, 180),     # angle folded into −90 … +90 (axial data)
    "wrap180": lambda x: _wrap(x, 360),    # angle folded into −180 … +180
    "wrap360": lambda x: np.mod(np.asarray(x, float), 360),
    "mean": lambda x: np.nanmean(x) + 0 * np.asarray(x, float),
    "median": lambda x: np.nanmedian(x) + 0 * np.asarray(x, float),
    "std": lambda x: np.nanstd(x, ddof=1) + 0 * np.asarray(x, float),
    "np": np, "nan": np.nan,
    # whole-column transforms
    "zscore": lambda x: (np.asarray(x, float) - np.nanmean(x)) / np.nanstd(x, ddof=1),
    "norm01": lambda x: (np.asarray(x, float) - np.nanmin(x)) / ((np.nanmax(x) - np.nanmin(x)) or 1),
    "cumsum": lambda x: pd.Series(np.asarray(x, float)).cumsum(skipna=True).values,
    "diff": lambda x: pd.Series(np.asarray(x, float)).diff().values,
    "rank": lambda x: pd.Series(np.asarray(x, float)).rank(pct=True).values * 100,   # percentile rank
    "isnan": lambda x: pd.isna(x),
    # across columns, row by row
    "rowmean": lambda *a: np.nanmean(np.column_stack([np.asarray(v, float) for v in a]), axis=1),
    "rowsum": lambda *a: np.nansum(np.column_stack([np.asarray(v, float) for v in a]), axis=1),
    "rowmax": lambda *a: np.nanmax(np.column_stack([np.asarray(v, float) for v in a]), axis=1),
    "rowmin": lambda *a: np.nanmin(np.column_stack([np.asarray(v, float) for v in a]), axis=1),
    # per group (e.g. per sheet): every row gets its group's value
    "groupmean": lambda x, g: _group(x, g, "mean"),
    "groupmedian": lambda x, g: _group(x, g, "median"),
    "groupstd": lambda x, g: _group(x, g, "std"),
    "groupmin": lambda x, g: _group(x, g, "min"),
    "groupmax": lambda x, g: _group(x, g, "max"),
    "groupn": lambda x, g: _group(x, g, "count"),
}


def _group(x, g, how):
    s = pd.Series(np.asarray(x, float))
    return s.groupby(pd.Series(np.asarray(g)).astype(str).values).transform(how).values


def eval_formula(expr, df):
    """Evaluate a formula over the table. Column names can be written as "Name", 'Name',
    `Name` or (if they are plain words) Name. ^ means power. Functions: FORMULA_FUNCS."""
    import re
    cols = list(df.columns)
    colset = set(cols)

    def quoted(m):
        name = m.group(2)
        return f"`{name}`" if name in colset else m.group(0)

    e = re.sub(r'(["\'])(.+?)\1', quoted, expr)                # "Angle to Caxis" -> `Angle to Caxis`
    e = e.replace("^", "**").replace("×", "*").replace("÷", "/").replace("−", "-")
    e = re.sub(r"`([^`]+)`", lambda m: f"__c[{m.group(1)!r}]", e)
    for nm in re.findall(r"__c\[(.+?)\]", e):
        if eval(nm) not in colset:
            raise ValueError(f"no column called {eval(nm)}")
    ns = dict(FORMULA_FUNCS)
    ns["__c"] = {c: df[c] for c in cols}
    for c in cols:                                              # plain-word column names work bare
        if isinstance(c, str) and c.isidentifier() and c not in ns:
            ns[c] = df[c]
    out = eval(compile(e, "<formula>", "eval"), {"__builtins__": {}}, ns)
    if np.isscalar(out):
        out = np.full(len(df), out)
    return pd.to_numeric(pd.Series(np.asarray(out), index=df.index), errors="coerce")


def add_columns(calc_json):
    """Add calculated columns: [{"name": ..., "expr": ...}] (see eval_formula). Later formulas
    can use earlier ones. Returns load-style JSON for the updated table."""
    df = DATA["base"].copy()
    msgs, stats = [], {}
    DATA["calc"] = calc_json or "[]"
    DATA["sheetdf"] = {}
    for item in json.loads(calc_json or "[]"):
        name, expr = str(item.get("name", "")).strip(), str(item.get("expr", "")).strip()
        if not name or not expr:
            continue
        try:
            df[name] = eval_formula(expr, df)
            v = df[name]
            if pd.api.types.is_numeric_dtype(v) and not pd.api.types.is_bool_dtype(v):
                f = v.astype(float)
                f = f[np.isfinite(f)]
                stats[name] = {"ok": True, "n": int(len(f)), "missing": int(len(v) - len(f)),
                               "mean": float(f.mean()) if len(f) else None,
                               "min": float(f.min()) if len(f) else None,
                               "max": float(f.max()) if len(f) else None,
                               "first": [_fmt_cell(x) for x in v.head(3)]}
            else:
                stats[name] = {"ok": True, "n": int(v.notna().sum()), "missing": int(v.isna().sum()),
                               "levels": int(v.nunique()), "first": [_fmt_cell(x) for x in v.head(3)]}
        except Exception as e:  # report and carry on with the other columns
            msg = str(e).replace("__c[", "[")
            msgs.append(f"{name}: {type(e).__name__ if not msg else msg}")
            stats[name] = {"ok": False, "error": type(e).__name__ if not msg else msg}
    out = json.loads(_finish_load(df, DATA["raw"], DATA["sheets"], DATA["sheet"], DATA["skipped"],
                                  keep_base=True, calc_msgs=msgs))
    out["calc_stats"] = stats
    return _dumps(out)


def _finish_load(df, raw_rows, sheets, sheet, skipped, keep_base=False, calc_msgs=None):
    if not keep_base:
        DATA.update(base=df.copy(), raw=raw_rows, sheets=sheets, sheet=sheet, skipped=skipped)
    DATA["df"] = df
    head = df.head(20000)    # values for tooltips; the page table shows the first 2000
    preview = head.astype(object).where(head.notna(), "").values.tolist()
    return _dumps({
        "columns": list(df.columns),
        "numeric": [bool(pd.api.types.is_numeric_dtype(df[c])) for c in df.columns],
        "nrows": int(len(df)),
        "preview": [[_fmt_cell(v) for v in r] for r in preview],
        "rowlabels": _row_labels(df),
        "raw": raw_rows,
        "sheets": sheets,
        "sheet": sheet,
        "skipped_sheets": skipped,
        "calc_errors": calc_msgs or [],
        "table_order": list(DATA.get("tables", {}).keys()),
        "tables": {k: {"columns": list(v.columns),
                       "numeric": [bool(pd.api.types.is_numeric_dtype(v[c])) for c in v.columns],
                       "nrows": int(len(v))}
                   for k, v in DATA.get("tables", {}).items()},
    })


def _row_labels(df):
    for c in df.columns:
        if not pd.api.types.is_numeric_dtype(df[c]):
            return {"column": c, "values": df[c].astype(str).tolist()}
    return None


def _fmt_cell(v):
    if isinstance(v, (float, np.floating)):
        return f"{v:.6g}"
    return str(v)


# --------------------------------------------------------------- helpers
def _style(c):
    fam = c.get("font", "sans")
    rc = {
        "font.size": c.get("fontsize", 9),
        "axes.labelsize": c.get("fontsize", 9),
        "axes.titlesize": c.get("fontsize", 9) + 1,
        "legend.fontsize": c.get("fontsize", 9) - 1,
        "xtick.labelsize": c.get("fontsize", 9) - 1,
        "ytick.labelsize": c.get("fontsize", 9) - 1,
        "axes.linewidth": 0.8,
        "lines.linewidth": c.get("linewidth", 1.2),
        "lines.markersize": c.get("markersize", 4),
        "errorbar.capsize": c.get("capsize", 2),
        "xtick.major.width": 0.8, "ytick.major.width": 0.8,
        "xtick.minor.width": 0.6, "ytick.minor.width": 0.6,
        "xtick.major.size": 3.5, "ytick.major.size": 3.5,
        "xtick.minor.size": 2.0, "ytick.minor.size": 2.0,
        "legend.frameon": c.get("legendframe", False),
        "pdf.fonttype": 42,          # TrueType in PDF (journal requirement)
        "svg.fonttype": "none",      # keep text editable in SVG
        "axes.unicode_minus": True,
    }
    theme = c.get("theme", "publication")
    if theme in SNS_THEMES and sns is not None:
        rc.update(sns.axes_style(theme))
        rc.update({"axes.linewidth": 0.8 if theme in ("ticks", "white") else 0,
                   "xtick.major.size": 3.5 if theme == "ticks" else 0,
                   "ytick.major.size": 3.5 if theme == "ticks" else 0,
                   "xtick.minor.size": 2.0 if theme == "ticks" else 0,
                   "ytick.minor.size": 2.0 if theme == "ticks" else 0})
    elif c.get("ticks", "in") == "in":
        rc.update({"xtick.direction": "in", "ytick.direction": "in",
                   "xtick.top": not c.get("despine"), "ytick.right": not c.get("despine")})
    else:
        rc.update({"xtick.direction": "out", "ytick.direction": "out"})
    if fam == "serif":
        rc.update({"font.family": "serif", "font.serif": ["STIXGeneral", "DejaVu Serif"],
                   "mathtext.fontset": "stix"})
    elif fam == "cm":
        rc.update({"font.family": "serif", "font.serif": ["cmr10", "DejaVu Serif"],
                   "mathtext.fontset": "cm", "axes.formatter.use_mathtext": True,
                   "axes.unicode_minus": False})
    else:
        rc.update({"font.family": "sans-serif", "font.sans-serif": ["DejaVu Sans"],
                   "mathtext.fontset": "dejavusans"})
    return rc


def _colors(name, n):
    if name in SNS_PALETTES and sns is not None:
        return sns.color_palette(name, n).as_hex()
    if name == "viridis":
        return [matplotlib.colors.to_hex(plt.get_cmap("viridis")(v))
                for v in np.linspace(0.0, 0.85, max(n, 2))]
    pal = PALETTES.get(name) or PALETTES["okabe-ito"]
    return [pal[i % len(pal)] for i in range(n)]


def _cmap(name):
    name = name or "viridis"
    rev = name.endswith("_r")
    base = name[:-2] if rev else name
    if base in SNS_CMAPS and sns is not None:
        cm = sns.color_palette(base, as_cmap=True)
        return cm.reversed() if rev else cm
    return plt.get_cmap(name)


def _num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _nums(text):
    out = []
    for part in str(text or "").replace(";", ",").split(","):
        v = _num(part.strip())
        if v is not None:
            out.append(v)
    return out


def _fit_label(coef, order, r2, xname="x"):
    terms = []
    for p, a in zip(range(order, -1, -1), coef):
        s = f"{a:.3g}"
        terms.append(s if p == 0 else f"{s}{xname}" if p == 1 else f"{s}{xname}$^{p}$")
    eq = " + ".join(terms).replace("+ -", "− ")
    return f"y = {eq}  (R² = {r2:.3f})"


def _fmt_pm(v, e=None):
    """Value ± error, rounded to 2 significant figures of the error."""
    if e is None or not np.isfinite(e) or e <= 0:
        return f"{v:.4g}"
    d = max(0, 1 - int(np.floor(np.log10(e))))
    return f"{v:.{d}f} ± {e:.{d}f}"


def _ok(s):
    """True for columns that exist and aren't 'none'."""
    return bool(s) and s not in ("none", "")


# ------------------------------------------------------- variable mapping
class Mapping:
    """Colour / size / shape mapped from data columns (seaborn-style hue, size, style)."""

    def __init__(self, df, c):
        self.c = c
        self.hue = self.size = self.style = None
        if _ok(c.get("hue")) and c["hue"] in df.columns:
            self.hue = self._setup_hue(df[c["hue"]], c)
        if _ok(c.get("sizeby")) and c["sizeby"] in df.columns:
            self.size = self._setup_size(df[c["sizeby"]], c)
        if _ok(c.get("styleby")) and c["styleby"] in df.columns:
            v = df[c["styleby"]].astype(str).where(df[c["styleby"]].notna(), "–")
            cats = list(dict.fromkeys(v))
            self.style = {"col": c["styleby"], "values": v, "cats": cats,
                          "markers": {k: MARKERS[i % len(MARKERS)] for i, k in enumerate(cats)}}

    @property
    def active(self):
        return any((self.hue, self.size, self.style))

    def _setup_hue(self, v, c):
        continuous = pd.api.types.is_numeric_dtype(v) and not c.get("huecat")
        if continuous:
            vv = v.astype(float)
            lo = _num(c.get("huemin"))
            hi = _num(c.get("huemax"))
            lo = lo if lo is not None else c.get("_hue_lo")     # figure-wide range (multi-panel)
            hi = hi if hi is not None else c.get("_hue_hi")
            if c.get("huerobust"):                              # ignore a few extreme values
                fin = vv[np.isfinite(vv) & ((vv > 0) if c.get("huelog") else True)]
                if len(fin):
                    lo = lo if lo is not None else float(np.percentile(fin, 2))
                    hi = hi if hi is not None else float(np.percentile(fin, 98))
            if c.get("huelog"):
                pos = vv[vv > 0]
                lo = lo if lo is not None else (pos.min() if len(pos) else 1)
                hi = hi if hi is not None else (pos.max() if len(pos) else 10)
                norm = matplotlib.colors.LogNorm(lo, hi)
            else:
                lo = lo if lo is not None else vv.min()
                hi = hi if hi is not None else vv.max()
                if lo == hi:
                    hi = lo + 1
                norm = matplotlib.colors.Normalize(lo, hi)
            return {"col": c["hue"], "continuous": True, "values": vv, "norm": norm,
                    "cmap": _cmap(c.get("cmap", "viridis"))}
        sv = v.astype(str).where(v.notna(), "–")
        cats = list(dict.fromkeys(sv))
        if c.get("_hue_cats"):              # same category -> same colour in every panel
            cats = list(c["_hue_cats"]) + [k for k in cats if k not in c["_hue_cats"]]
        cols = _colors(c.get("huepalette") or c.get("palette", "okabe-ito"), len(cats))
        return {"col": c["hue"], "continuous": False, "values": sv, "cats": cats,
                "colors": dict(zip(cats, cols))}

    def _setup_size(self, v, c):
        smin = _num(c.get("sizemin")) or 2.0
        smax = _num(c.get("sizemax")) or 9.0
        if pd.api.types.is_numeric_dtype(v):
            vv = v.astype(float)
            lo, hi = np.nanmin(vv), np.nanmax(vv)
            return {"col": c["sizeby"], "continuous": True, "values": vv, "lo": lo, "hi": hi,
                    "smin": smin, "smax": smax}
        sv = v.astype(str)
        cats = list(dict.fromkeys(sv))
        sizes = np.linspace(smin, smax, max(len(cats), 2))[:len(cats)]
        return {"col": c["sizeby"], "continuous": False, "values": sv, "cats": cats,
                "sizes": dict(zip(cats, sizes))}

    def colors_for(self, idx, default):
        if not self.hue:
            return [default] * len(idx)
        h = self.hue
        if h["continuous"]:
            vals = h["values"].loc[idx].values
            rgba = h["cmap"](h["norm"](vals))
            rgba[~np.isfinite(vals)] = matplotlib.colors.to_rgba("0.75")
            return list(rgba)
        return [h["colors"][k] for k in h["values"].loc[idx]]

    def sizes_for(self, idx, default):
        if not self.size:
            return np.full(len(idx), float(default))
        s = self.size
        if s["continuous"]:
            vals = s["values"].loc[idx].values
            span = (s["hi"] - s["lo"]) or 1.0
            out = s["smin"] + (vals - s["lo"]) / span * (s["smax"] - s["smin"])
            return np.where(np.isfinite(out), out, s["smin"])
        return np.array([s["sizes"][k] for k in s["values"].loc[idx]])

    def size_of_value(self, v):
        s = self.size
        span = (s["hi"] - s["lo"]) or 1.0
        return s["smin"] + (v - s["lo"]) / span * (s["smax"] - s["smin"])

    def legend_handles(self, base_ms, open_m):
        """Extra legend entries for categorical hue, size and shape."""
        H = []
        title = lambda t: (Rectangle((0, 0), 0, 0, fill=False, edgecolor="none", visible=False), t)
        if self.hue and not self.hue["continuous"]:
            H.append(title(self.hue["col"]))
            for k in self.hue["cats"]:
                col = self.hue["colors"][k]
                H.append((Line2D([], [], linestyle="none", marker="o", markersize=base_ms,
                                 markerfacecolor="white" if open_m else col, markeredgecolor=col), k))
        if self.size:
            s = self.size
            H.append(title(s["col"]))
            if s["continuous"]:
                ticks = matplotlib.ticker.MaxNLocator(4).tick_values(s["lo"], s["hi"])
                ticks = [t for t in ticks if s["lo"] <= t <= s["hi"]] or [s["lo"], s["hi"]]
                for t in ticks:
                    H.append((Line2D([], [], linestyle="none", marker="o", color="0.45",
                                     markersize=self.size_of_value(t)), f"{t:g}"))
            else:
                for k in s["cats"]:
                    H.append((Line2D([], [], linestyle="none", marker="o", color="0.45",
                                     markersize=s["sizes"][k]), k))
        if self.style:
            H.append(title(self.style["col"]))
            for k in self.style["cats"]:
                H.append((Line2D([], [], linestyle="none", marker=self.style["markers"][k],
                                 color="0.3", markerfacecolor="white" if open_m else "0.3",
                                 markersize=base_ms), k))
        return H

    def _extend(self):
        v = self.hue["values"]
        v = v[np.isfinite(v)]
        if not len(v):
            return "neither"
        lo, hi = self.hue["norm"].vmin, self.hue["norm"].vmax
        a, b = v.min() < lo - 1e-12 * abs(lo), v.max() > hi + 1e-12 * abs(hi)
        return {(True, True): "both", (True, False): "min", (False, True): "max"}.get((bool(a), bool(b)), "neither")

    def colorbar(self, fig, ax):
        if self.hue and self.hue["continuous"] and self.c.get("_sharedkey"):
            ax._sp_cbar = self          # drawn once for the whole figure
            return None
        if self.hue and self.hue["continuous"]:
            sm = matplotlib.cm.ScalarMappable(norm=self.hue["norm"], cmap=self.hue["cmap"])
            cb = _colorbar(fig, ax, sm, self.c, extend=self._extend())
            cb.set_label(self.c.get("huelabel") or self.hue["col"])
            if isinstance(self.hue["norm"], matplotlib.colors.LogNorm) and self.c.get("cbarticks") != "none":
                la = cb.ax.xaxis if cb.orientation == "horizontal" else cb.ax.yaxis
                if not _num(self.c.get("cbarnticks")):
                    la.set_major_locator(matplotlib.ticker.LogLocator(subs=(1, 2, 5)))
                la.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
                la.set_minor_formatter(matplotlib.ticker.NullFormatter())
            cb.outline.set_linewidth(0.6)
            return cb
        return None


def _colorbar(fig, ax, sm, c, extend="neither"):
    """A colour bar beside or inside one panel. Position, length, thickness, sides and ticks
    come from the Colour section; with a length / thickness set (and always inside the plot) it
    is sized relative to the panel's own plot area, so it follows a panel with a fixed aspect."""
    ln, th = _num(c.get("cbarlen")), _num(c.get("cbarwid"))
    pos = c.get("cbarpos") or "right"
    polar = ax.name == "polar"
    inside = pos.startswith("in-")
    horiz = pos in ("top", "bottom", "in-top", "in-bottom")
    if ln or th or inside:
        ln = min(max((ln or (40 if inside else 70 if polar else 100)) / 100, 0.05), 1.0)
        th = min(max((th or (3.5 if inside else 4)) / 100, 0.005), 0.5)
        g, m = (0.14 if polar else 0.03), 0.05      # gap outside / margin inside
        box = {"right": [1.0 + g, (1 - ln) / 2, th, ln],
               "left": [-0.24 - th, (1 - ln) / 2, th, ln],           # clear of the Y tick labels/title
               "top": [(1 - ln) / 2, 1.0 + g, ln, th],
               "bottom": [(1 - ln) / 2, -0.30 - th, ln, th],        # clear of the X tick labels/title
               "in-ur": [1 - m - th, 1 - m - ln, th, ln], "in-lr": [1 - m - th, m, th, ln],
               "in-ul": [m, 1 - m - ln, th, ln], "in-ll": [m, m, th, ln],
               "in-top": [(1 - ln) / 2, 1 - m - th, ln, th],
               "in-bottom": [(1 - ln) / 2, m + 0.17, ln, th]}.get(pos, [1.0 + g, (1 - ln) / 2, th, ln])
        cax = ax.inset_axes(box)
        cax._sp_parent = ax
        cb = fig.colorbar(sm, cax=cax, extend=extend, orientation="horizontal" if horiz else "vertical")
        side = {"top": "top", "left": "left", "in-ur": "left", "in-lr": "left", "in-top": "bottom",
                "in-bottom": "bottom"}.get(pos)        # inside: labels face into the plot
        if side:
            _cbar_sides(cb, side, side)
        if inside and c.get("cbarbox"):
            cax.set_zorder(6)
            cb._sp_box = True
    else:
        cb = fig.colorbar(sm, ax=ax, location=pos, pad=0.02 if not horiz else 0.04,
                          fraction=0.05, aspect=25, shrink=0.7 if polar else 1.0, extend=extend)
    _style_cbar(cb, c)
    return cb


def _cbar_boxes(fig):
    """White boxes behind inside colour bars, sized to the bar plus its ticks and title. Kept in
    the parent panel's own coordinates so later layout changes move them with the panel."""
    cbs = getattr(fig, "_sp_boxes", [])
    if not cbs:
        return
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    for cb in cbs:
        parent = getattr(cb.ax, "_sp_parent", None)
        if parent is None:
            continue
        old = getattr(cb, "_sp_patch", None)
        if old is not None:
            old.remove()
        bb = cb.ax.get_tightbbox(r).transformed(parent.transAxes.inverted())
        pad = 0.015
        cb._sp_patch = parent.add_patch(matplotlib.patches.FancyBboxPatch(
            (bb.x0 - pad, bb.y0 - pad), bb.width + 2 * pad, bb.height + 2 * pad,
            boxstyle="round,pad=0,rounding_size=0.01", transform=parent.transAxes, clip_on=False,
            facecolor="white", edgecolor="0.8", linewidth=0.5, alpha=0.92, zorder=5.9))


def _cbar_sides(cb, ticks=None, label=None):
    """Put a colour bar's tick labels and its title on the chosen sides."""
    if cb.orientation == "horizontal":
        if ticks in ("top", "bottom"):
            cb.ax.xaxis.set_ticks_position(ticks)
        if label in ("top", "bottom"):
            cb.ax.xaxis.set_label_position(label)
    else:
        if ticks in ("left", "right"):
            cb.ax.yaxis.set_ticks_position(ticks)
        if label in ("left", "right"):
            cb.ax.yaxis.set_label_position(label)


def _style_cbar(cb, c):
    """Colour bar ticks: shown / labels only / hidden, how many, and which way they point."""
    n = _num(c.get("cbarnticks"))
    if n and n >= 2:
        cb.locator = matplotlib.ticker.MaxNLocator(int(n), min_n_ticks=2)
    mode = c.get("cbarticks") or "shown"
    long_ax = cb.ax.xaxis if cb.orientation == "horizontal" else cb.ax.yaxis
    if mode == "none":
        long_ax.set_major_locator(matplotlib.ticker.NullLocator())
        long_ax.set_minor_locator(matplotlib.ticker.NullLocator())
    elif mode == "labels":
        cb.ax.tick_params(which="both", length=0)
    if c.get("cbartickdir") in ("in", "out", "inout"):
        cb.ax.tick_params(which="both", direction=c["cbartickdir"])
    if c.get("cbarminor"):
        cb.minorticks_on()
    _cbar_sides(cb, c.get("cbartickside") or None, c.get("cbarlabelside") or None)
    if getattr(cb, "_sp_box", False):
        cb.ax.figure._sp_boxes = getattr(cb.ax.figure, "_sp_boxes", []) + [cb]
    cb._sp_styled = True


# ---------------------------------------------------------- reference lines
def _stats(v, err=None):
    """mean/SD, median/robust SD, weighted mean/SE/MSWD of a 1-D array."""
    v = np.asarray(v, float)
    out = {"n": len(v), "mean": v.mean(), "sd": v.std(ddof=1) if len(v) > 1 else np.nan,
           "median": np.median(v),
           "mad": 1.4826 * np.median(np.abs(v - np.median(v)))}
    if err is not None:
        e = np.asarray(err, float)
        ok = np.isfinite(e) & (e > 0)
        if ok.sum() >= 1:
            w = 1 / e[ok] ** 2
            wm = np.sum(w * v[ok]) / np.sum(w)
            out["wmean"] = wm
            out["wse"] = 1 / np.sqrt(np.sum(w))
            out["mswd"] = (np.sum(((v[ok] - wm) / e[ok]) ** 2) / (ok.sum() - 1)) if ok.sum() > 1 else np.nan
            out["wn"] = int(ok.sum())
    return out


def _ref_lines(ax, vals, err, c, color, prefix, axis, msgs):
    """Draw mean / median / weighted-mean lines (and ±kσ bands) for one set of values."""
    k = int(c.get("refband") or 0)
    kk = k or 1
    st = _stats(vals, err)
    rc = c.get("refcolor") or ""
    if rc == "custom":
        rc = c.get("refcustom") or ""
    color = rc or color                       # "" = follow the series colour
    lw = _num(c.get("refwidth")) or 0.9
    band_alpha = _num(c.get("refalpha")) or 0.12
    if ax.name == "polar":
        line, span = _polar_ref(ax, axis)
    else:
        line = ax.axhline if axis == "y" else ax.axvline
        span = ax.axhspan if axis == "y" else ax.axvspan
    sig = "σ" if kk == 1 else f"{kk}σ"
    todo = []
    if c.get("refmean"):
        todo.append(("mean", st["mean"], st["sd"], f"mean = {_fmt_pm(st['mean'], kk * st['sd'])} ({kk} SD)"))
    if c.get("refmedian"):
        todo.append(("median", st["median"], st["mad"],
                     f"median = {_fmt_pm(st['median'], kk * st['mad'])} ({kk} MAD)"))
    if c.get("refwmean"):
        if "wmean" in st:
            todo.append(("wmean", st["wmean"], st["wse"],
                         f"wtd mean = {_fmt_pm(st['wmean'], kk * st['wse'])} ({sig})\n"
                         f"MSWD {st['mswd']:.2f}, n = {st['wn']}"))
        else:
            msgs.append(f"{prefix or 'Weighted mean'}: needs an error column")
    for key, v, e, label in todo:
        line(v, color=color, linestyle=REF_STYLES[key], linewidth=lw, zorder=3,
             label=(prefix + label) if c.get("reflegend", True) else None)
        if k and np.isfinite(e) and e > 0:
            span(v - k * e, v + k * e, color=color, alpha=band_alpha, linewidth=0, zorder=0.5)
        msgs.append(prefix + label)


AGG_FUNCS = {
    "mean": lambda a: float(np.nanmean(a)), "average": lambda a: float(np.nanmean(a)),
    "median": lambda a: float(np.nanmedian(a)),
    "sd": lambda a: float(np.nanstd(a, ddof=1)), "std": lambda a: float(np.nanstd(a, ddof=1)),
    "var": lambda a: float(np.nanvar(a, ddof=1)),
    "se": lambda a: float(np.nanstd(a, ddof=1) / np.sqrt(np.isfinite(a).sum())),
    "min": lambda *a: float(np.nanmin(a[0])) if len(a) == 1 else float(np.nanmin(a)),
    "max": lambda *a: float(np.nanmax(a[0])) if len(a) == 1 else float(np.nanmax(a)),
    "sum": lambda a: float(np.nansum(a)), "count": lambda a: int(np.isfinite(a).sum()),
    "n": lambda a: int(np.isfinite(a).sum()),
    "percentile": lambda a, p: float(np.nanpercentile(a, p)),
    "quantile": lambda a, q: float(np.nanquantile(a, q)),
    "iqr": lambda a: float(np.subtract(*np.nanpercentile(a, [75, 25]))),
    "mad": lambda a: float(1.4826 * np.nanmedian(np.abs(a - np.nanmedian(a)))),
    "mode": lambda a: _mode_kde(np.asarray(a, float)[np.isfinite(a)]),
    "wmean": lambda a, e: float(np.nansum(a / e ** 2) / np.nansum(1 / e ** 2)),
}


def eval_scalar(expr, df, x):
    """Evaluate a reference-line formula to one or more numbers. `x` is the row's column;
    other columns by "name" (or bare word); AGG_FUNCS + FORMULA_FUNCS available.
    Comma-separated expressions give several values."""
    import re
    colset = set(df.columns)
    e = re.sub(r'(["\'])(.+?)\1', lambda m: f"`{m.group(2)}`" if m.group(2) in colset else m.group(0), expr)
    e = e.replace("^", "**").replace("×", "*").replace("÷", "/").replace("−", "-").replace("±", "+-")
    e = re.sub(r"`([^`]+)`", lambda m: f"__c[{m.group(1)!r}]", e)
    ns = dict(FORMULA_FUNCS)
    ns.update(AGG_FUNCS)
    ns["__c"] = {c: pd.to_numeric(df[c], errors="coerce").to_numpy(float) for c in df.columns
                 if pd.api.types.is_numeric_dtype(df[c])}
    for c in df.columns:
        if isinstance(c, str) and c.isidentifier() and c not in ns and c in ns["__c"]:
            ns[c] = ns["__c"][c]
    ns["x"] = np.asarray(x, float)
    out = eval(compile(e, "<line formula>", "eval"), {"__builtins__": {}}, ns)
    vals = list(out) if isinstance(out, (tuple, list)) else [out]
    res = []
    for v in vals:
        v = np.asarray(v, float)
        if v.ndim:                    # an array (e.g. "x"): summarise it is ambiguous -> reject
            raise ValueError("the formula gives a whole column, not a single value – wrap it in mean(), max() …")
        res.append(float(v))
    return res


def _ref_extra(ax, df, c, msgs, force_axis=None):
    """Lines for other columns: refextra = [{col, axis, color, kind, fx, ls, label, err}].
    kind: "stats" (ticked mean/median/wtd mean), "sd1"/"sd2" (mean ± k·SD), "minmax", "formula"."""
    keep = ~df.index.isin([int(r) for r in c.get("excluded", [])])
    any_stat = any(c.get(k) for k in ("refmean", "refmedian", "refwmean"))
    for item in c.get("refextra") or []:
        col = item.get("col")
        if col not in df.columns:
            continue
        v = pd.to_numeric(df.loc[keep, col], errors="coerce").dropna()
        if not len(v):
            continue
        axis = force_axis or item.get("axis", "y")
        kind = item.get("kind") or "stats"
        rc = item.get("color") or (c.get("refcustom") if c.get("refcolor") == "custom" else c.get("refcolor")) or "black"
        if kind == "stats":
            if not any_stat:
                continue
            ecol = item.get("err")
            err = pd.to_numeric(df.loc[v.index, ecol], errors="coerce").values if ecol in df.columns else None
            cc = {**c, "refwmean": bool(c.get("refwmean")) and err is not None, "refcolor": rc, "refcustom": rc}
            _ref_lines(ax, v.values, err, cc, "black", f"{item.get('label') or col}: ", axis, msgs)
            continue
        vals = v.values.astype(float)
        try:
            if kind in ("sd1", "sd2"):
                k = 1 if kind == "sd1" else 2
                m, sd = float(np.mean(vals)), float(np.std(vals, ddof=1))
                lines = [m - k * sd, m, m + k * sd]        # the mean itself is drawn solid
                desc = f"mean ± {k} SD = {_fmt_pm(m, k * sd)}"
            elif kind == "minmax":
                lines = [float(vals.min()), float(vals.max())]
                desc = f"min–max = {lines[0]:.4g} – {lines[1]:.4g}"
            else:
                expr = (item.get("fx") or "").strip()
                if not expr:
                    continue
                lines = eval_scalar(expr, df.loc[keep], vals)
                desc = f"{expr} = " + ", ".join(f"{q:.4g}" for q in lines)
        except Exception as e:
            msgs.append(f"{col} line: {str(e).replace('__c[', '[')}")
            continue
        ls = LINESTYLES.get(item.get("ls") or "--", "--")
        if ax.name == "polar":
            draw, _ = _polar_ref(ax, axis)
        else:
            draw = ax.axhline if axis == "y" else ax.axvline
        label = f"{item.get('label') or col}: {desc}"
        lw = _num(c.get("refwidth")) or 0.9
        for i, q in enumerate(lines):
            if np.isfinite(q):
                centre = kind in ("sd1", "sd2") and i == 1
                draw(q, color=rc, linestyle="-" if centre else ls, linewidth=lw * (1.2 if centre else 1),
                     zorder=3, label=label if (i == 0 and c.get("reflegend", True)) else None)
        msgs.append(label)


def _custom_lines(ax, c, kind):
    if ax.name == "polar":
        line_y, _ = _polar_ref(ax, "y")
        line_x, _ = _polar_ref(ax, "x")
        for v in _nums(c.get("hlines")):
            line_y(v, color="0.35", linewidth=0.8, linestyle="--", zorder=1)
        for v in _nums(c.get("vlines")):
            line_x(v, color="0.35", linewidth=0.8, linestyle="--", zorder=1)
        return
    xl, yl = ax.get_xlim(), ax.get_ylim()
    for v in _nums(c.get("hlines")):
        ax.axhline(v, color="0.35", linewidth=0.8, linestyle="--", zorder=1)
    if kind not in CATEGORICAL:
        for v in _nums(c.get("vlines")):
            ax.axvline(v, color="0.35", linewidth=0.8, linestyle="--", zorder=1)
        if c.get("oneone"):
            lo, hi = min(xl[0], yl[0], xl[1], yl[1]), max(xl[0], yl[0], xl[1], yl[1])
            xx = np.geomspace(max(lo, 1e-300), hi, 200) if (ax.get_xscale() == "log") else np.linspace(lo, hi, 200)
            ax.plot(xx, xx, color="0.35", linewidth=0.8, linestyle=":", zorder=1,
                    label="1:1" if c.get("reflegend", True) else None)
    ax.set_xlim(xl)
    ax.set_ylim(yl)


# ----------------------------------------------------------------- polar
POLAR_ZERO = {"up": "N", "right": "E", "down": "S", "left": "W"}


def _polar_setup(ax, df, c, series):
    """Quarter (or other sector) polar axes: the X column is stretched over the arc."""
    xcol = c.get("x") if c.get("x") in df.columns else None
    xs = pd.to_numeric(df[xcol], errors="coerce") if xcol else pd.Series(np.arange(1, len(df) + 1))
    style = c.get("polstyle", "quarter")
    amin, amax = _num(c.get("polmin")), _num(c.get("polmax"))
    if style == "half":                     # true angles, −90 … +90 with 0 at the start direction
        amin = -90.0 if amin is None else amin
        amax = 90.0 if amax is None else amax
    elif style == "full":                   # true angles round the whole circle
        amin = 0.0 if amin is None else amin
        amax = 360.0 if amax is None else amax
    amin = float(np.nanmin(xs)) if amin is None else amin
    amax = float(np.nanmax(xs)) if amax is None else amax
    if amax == amin:
        amax = amin + 1
    ax.set_theta_zero_location(POLAR_ZERO.get(c.get("polstart", "up"), "N"))
    ax.set_theta_direction(-1 if c.get("poldir", "cw") == "cw" else 1)
    if style in ("half", "full"):
        scale = np.pi / 180
        ax._sp_tx = lambda v: np.asarray(v, float) * scale
        if amax - amin < 360:
            ax.set_thetamin(amin)
            ax.set_thetamax(amax)
        span = amax - amin
    else:                                   # stretch the data range over the chosen arc
        span = float(c.get("polspan") or 90)
        scale = np.radians(span) / (amax - amin)
        ax._sp_tx = lambda v: (np.asarray(v, float) - amin) * scale
        if span < 360:
            ax.set_thetamin(0)
            ax.set_thetamax(span)
    ax._sp_amin, ax._sp_amax, ax._sp_scale, ax._sp_span = amin, amax, scale, span
    ax._sp_pending, ax._sp_final = [], False
    ax.grid(True, color="0.85", linewidth=0.5)
    ax.set_axisbelow(True)


def _polar_finish(ax, c, df, series, xl, yl):
    amin, amax = ax._sp_amin, ax._sp_amax
    if c.get("polstyle", "quarter") in ("half", "full") and amax - amin >= 120:
        step = 30 if amax - amin <= 360 else 60
        ticks = np.arange(np.ceil(amin / step) * step, amax + 1e-9, step)
    else:
        ticks = matplotlib.ticker.MaxNLocator(7).tick_values(amin, amax)
    ticks = [t for t in ticks if amin - 1e-9 <= t <= amax + 1e-9]
    if ax._sp_span >= 360 and len(ticks) > 1 and np.isclose(ticks[-1] - ticks[0], 360):
        ticks = ticks[:-1]                      # full circle: last tick sits on the first
    suffix = c.get("polunit", "°")
    ax.set_thetagrids(np.degrees(ax._sp_tx(ticks)), [f"{t:g}{suffix}" for t in ticks])
    # radial range
    lo, hi = _num(c.get("ymin")), _num(c.get("ymax"))
    if lo is None:
        ys = pd.concat([pd.to_numeric(_series_df(s, df)[s["y"]], errors="coerce") for s in series])
        lo = 0.0 if ys.min() >= 0 else None
    ax.set_ylim(bottom=lo, top=hi)
    if c.get("ylog"):
        ax.set_rscale("log")
    else:
        ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(5))
    r0, r1 = ax.get_ylim()
    ax._sp_final = True
    for fn, a, kw in ax._sp_pending:          # reference rays / bands, now that the limits are known
        fn(*a, **kw)
    ax._sp_pending = []
    ax.set_ylim(r0, r1)
    ax.set_ylabel(yl, labelpad=6)
    ax.set_xlabel("")
    if xl:
        # put the angle label just outside the arc's midpoint, clear of the tick labels
        r0, r1 = ax.get_ylim()
        mid = float(ax._sp_tx((amin + amax) / 2))
        o = ax.transData.transform((0, r0))
        p = ax.transData.transform((mid, r1))
        v = (p - o) / (np.hypot(*(p - o)) or 1)
        fs = plt.rcParams["axes.labelsize"]
        fs = fs if isinstance(fs, (int, float)) else c.get("fontsize", 9)
        fs = _num(c.get("xlabelsize")) or fs
        dist = fs * 3.4 + (_num(c.get("xlabelpad")) or 0)
        rot = np.degrees(np.arctan2(v[0], -v[1])) if abs(v[1]) > 1e-6 or abs(v[0]) > 1e-6 else 0
        rot = rot - 180 if rot > 90 else rot + 180 if rot < -90 else rot
        tr = matplotlib.transforms.offset_copy(ax.transData, fig=ax.figure, x=v[0] * dist,
                                               y=v[1] * dist, units="points")
        ax._sp_xltext = ax.text(mid, r1, xl, rotation=rot, ha="center", va="center",
                                rotation_mode="anchor", transform=tr, fontsize=fs)
    ax.tick_params(axis="x", pad=2)


def _polar_ref(ax, axis):
    """Reference-line drawers for polar axes: Y values -> arcs, X values -> rays."""
    amin, amax, tx = ax._sp_amin, ax._sp_amax, ax._sp_tx
    phis = np.linspace(tx(amin), tx(amax), 200)
    # Rays and bands depend on the final radius limits, so until the axes are finished
    # (limits set) they are queued and drawn by _polar_finish.
    def later(fn):
        def run(*a, **kw):
            if getattr(ax, "_sp_final", False):
                fn(*a, **kw)
            else:
                ax._sp_pending.append((fn, a, kw))
        return run

    def lims():
        r0, r1 = ax.get_ylim()
        return min(r0, r1), max(r0, r1)

    if axis == "y":
        def line(v, **kw):
            r0, r1 = lims()
            if r0 <= v <= r1:
                ax.plot(phis, np.full_like(phis, v), **kw)

        def span(a, b, **kw):
            r0, r1 = lims()
            a, b = max(a, r0), min(b, r1)
            if b > a:
                ax.fill_between(phis, a, b, **kw)
    else:
        def line(v, **kw):
            r0, r1 = lims()
            if amin <= v <= amax:
                ax.plot([tx(v), tx(v)], [r0, r1], **kw)

        def span(a, b, **kw):
            r0, r1 = lims()
            a, b = max(a, amin), min(b, amax)
            if b > a:
                ax.fill_between(np.linspace(tx(a), tx(b), 100), r0, r1, **kw)
    return later(line), later(span)


# ----------------------------------------------------------------- vectors
def _vectors(ax, px, y, idx, df, c, mapping, col, alpha):
    """Draw each point as a short line: direction, length and colour from columns.
    Lengths and widths are in points, so lines keep their on-screen angle on any axes."""
    n = len(idx)
    dcol = c.get("vecdir") if _ok(c.get("vecdir")) and c.get("vecdir") in df.columns else None
    if dcol:
        dirs = pd.to_numeric(df.loc[idx, dcol], errors="coerce").values.astype(float)
    elif ax.name == "polar":
        dirs = np.asarray(px, float) / ax._sp_scale + (ax._sp_amin if c.get("polstyle", "quarter") == "quarter" else 0)
    else:
        dirs = np.zeros(n)
    if c.get("vecdirconv", "up_cw") == "up_cw":          # 0° = up, positive clockwise
        alpha_rad = np.radians(90 - dirs)
    else:                                                 # 0° = right (horizontal), positive anticlockwise
        alpha_rad = np.radians(dirs)
    lmax = _num(c.get("veclenmax")) or 14.0
    lcol = c.get("veclen") if _ok(c.get("veclen")) and c.get("veclen") in df.columns else None
    if lcol:
        lv = pd.to_numeric(df.loc[idx, lcol], errors="coerce").values.astype(float)
        top = np.nanmax(np.abs(pd.to_numeric(df[lcol], errors="coerce"))) or 1.0
        L = lmax * np.abs(lv) / top
    else:
        L = np.full(n, lmax * 0.7)
    L = np.where(np.isfinite(L), L, 0)
    ux, uy = np.cos(alpha_rad) * L, np.sin(alpha_rad) * L
    if c.get("vecstyle", "axial") == "axial":               # line centred on the point (axial data, e.g. Dpar)
        segs = np.stack([np.c_[-ux / 2, -uy / 2], np.c_[ux / 2, uy / 2]], axis=1)
    else:                                                   # from the point outwards
        segs = np.stack([np.c_[np.zeros(n), np.zeros(n)], np.c_[ux, uy]], axis=1)
    cols = mapping.colors_for(idx, col) if (mapping is not None and mapping.hue) else [col] * n
    pts_to_px = matplotlib.transforms.Affine2D().scale(ax.figure.dpi / 72.0)
    lc = matplotlib.collections.LineCollection(
        segs, offsets=np.c_[np.asarray(px, float), np.asarray(y, float)], offset_transform=ax.transData,
        transform=pts_to_px, colors=cols, linewidths=_num(c.get("vecwidth")) or 1.2,
        alpha=alpha, capstyle="round", zorder=2)
    ax.add_collection(lc)
    if c.get("vecdot"):
        ax.scatter(px, y, s=4, color=cols, zorder=2.1, linewidths=0)
    ax.update_datalim(np.c_[np.asarray(px, float), np.asarray(y, float)])
    ax.autoscale_view()
    return lcol, (np.nanmax(np.abs(pd.to_numeric(df[lcol], errors="coerce"))) if lcol else None)


# ----------------------------------------------------------------- x–y
T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160,
       2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056,
       2.052, 2.048, 2.045, 2.042]


def _t95(dof):
    return T95[dof - 1] if 1 <= dof <= 30 else 1.96 + 2.4 / max(dof, 1)


def _mode_kde(v):
    v = np.asarray(v, float)
    if len(v) < 3 or np.std(v) == 0:
        return float(np.median(v))
    bw = 1.06 * np.std(v, ddof=1) * len(v) ** (-1 / 5)
    xs = np.linspace(v.min(), v.max(), 512)
    dens = np.exp(-0.5 * ((xs[:, None] - v[None, :]) / bw) ** 2).sum(1)
    return float(xs[np.argmax(dens)])


def _group_summary(ax, df, c, x, y, xc, x_numeric, col, prefix, tx, msgs, out):
    """One point per group (default: each X value) at the group's mean/median/mode,
    with SD / SE / 95% CI / IQR / range bars."""
    gcol = c.get("groupby") if _ok(c.get("groupby")) and c.get("groupby") in df.columns else xc
    keys = df.loc[y.index, gcol] if gcol else x
    stat = c.get("groupstat", "mean")
    spread = c.get("groupspread", "sd")
    rows = []
    for k, idx in pd.Series(y.index, index=y.index).groupby(keys.values, sort=True):
        yy = y.loc[idx.values].astype(float).values
        if not len(yy):
            continue
        gx = float(k) if (gcol == xc and x_numeric) else (float(x.loc[idx.values].astype(float).mean())
                                                          if x_numeric else str(k))
        n = len(yy)
        mean, med = float(np.mean(yy)), float(np.median(yy))
        sd = float(np.std(yy, ddof=1)) if n > 1 else np.nan
        se = sd / np.sqrt(n) if n > 1 else np.nan
        centre = {"mean": mean, "median": med, "mode": _mode_kde(yy)}[stat]
        q1, q3 = np.percentile(yy, [25, 75])
        lo, hi = {"sd": (centre - sd, centre + sd), "se": (centre - se, centre + se),
                  "ci95": (centre - _t95(n - 1) * se, centre + _t95(n - 1) * se) if n > 1 else (np.nan, np.nan),
                  "iqr": (q1, q3), "range": (yy.min(), yy.max()),
                  "none": (centre, centre)}[spread]
        rows.append(dict(group=k, x=gx, n=n, centre=centre, lo=float(lo), hi=float(hi), mean=mean,
                         median=med, sd=sd, se=se, q1=float(q1), q3=float(q3), min=float(yy.min()),
                         max=float(yy.max())))
    if not rows:
        return
    gx = [r["x"] for r in rows]
    gy = np.array([r["centre"] for r in rows])
    lo = np.array([r["lo"] for r in rows])
    hi = np.array([r["hi"] for r in rows])
    yerr = None if spread == "none" else np.vstack([np.nan_to_num(gy - lo), np.nan_to_num(hi - gy)])
    gc = c.get("groupcolor") or ""
    gcolr = col if gc == "series" else (gc or "white")   # white markers read on any cloud of points
    lcol = col if gc == "series" else (gc or "black")
    ms = _num(c.get("groupsize")) or (c.get("markersize", 4) * 1.8)
    names = {"sd": "± 1 SD", "se": "± 1 SE", "ci95": "± 95% CI", "iqr": "(IQR)", "range": "(range)", "none": ""}
    label = f"{prefix + ': ' if prefix else ''}{stat} {names[spread]}".strip()
    px = tx(np.asarray(gx, float)) if x_numeric else gx
    ax.errorbar(px, gy, yerr=yerr, fmt="-" if c.get("groupline", True) else "none",
                color=lcol, ecolor="black", elinewidth=1.0, capsize=3, linewidth=1.2, zorder=5)
    ax.plot(px, gy, linestyle="none", marker=c.get("groupmarker", "D"), markersize=ms,
            markerfacecolor=gcolr, markeredgecolor="black", markeredgewidth=0.9, zorder=6)
    ax.plot([], [], linestyle="-" if c.get("groupline", True) else "none", color=lcol,
            marker=c.get("groupmarker", "D"), markersize=min(ms, 7), markerfacecolor=gcolr,
            markeredgecolor="black", markeredgewidth=0.9, label=label)     # legend key
    for r in rows:
        r["series"] = prefix or ""
        r["groupby"] = gcol or "row"
        r["statistic"] = stat
        r["spread"] = spread
    out.extend(rows)
    msgs.append(f"{label}: {len(rows)} groups by {gcol or 'X'} (download the table under Export)")


def _xy(ax, fig, df, c, series, colors, xcol, msgs, points, extra, groups_out=None):
    kind = c.get("kind", "scatter")
    excluded = set(int(r) for r in c.get("excluded", []))
    open_m = c.get("markerfill", "filled") == "open"
    vary_m = c.get("varymarkers", True)
    ms = c.get("markersize", 4)
    alpha = _num(c.get("alpha")) or 1.0
    polar = kind == "polar"
    tx = getattr(ax, "_sp_tx", None) or (lambda v: v)        # data -> plotted angle for polar plots
    if polar and xcol and not pd.api.types.is_numeric_dtype(df[xcol]):
        raise ValueError("The angle (X) column of a polar plot must be numeric.")
    jitter = (_num(c.get("poljitter")) or 0) if polar else 0
    jrng = np.random.default_rng(0)
    mapping = Mapping(df, c) if kind in ("scatter", "linemarkers", "polar") else None
    mapped = mapping is not None and mapping.active
    if not mapped and any(_ok(c.get(k)) for k in ("hue", "sizeby", "styleby")) and kind in ("line", "step"):
        msgs.append("Colour/size/shape mapping applies to scatter and line + marker plots")

    main_df = df
    for i, s in enumerate(series):
        main = not s.get("source")
        df = main_df if main else _series_df(s, main_df)
        xc = xcol if main else (s.get("x") if s.get("x") in df.columns else None)
        y = pd.to_numeric(df[s["y"]], errors="coerce")
        if xc:
            x = df[xc]
            x_numeric = pd.api.types.is_numeric_dtype(x)
        else:
            x, x_numeric = pd.Series(np.arange(1, len(df) + 1), index=df.index), True
        if polar and not x_numeric:
            raise ValueError(f"The angle (X) column of series {s['y']} must be numeric.")
        yerr = pd.to_numeric(df[s["yerr"]], errors="coerce") if s.get("yerr") in df.columns else None
        xerr = pd.to_numeric(df[s["xerr"]], errors="coerce") if s.get("xerr") in df.columns else None
        valid = y.notna() & (x.notna() if x_numeric else True)
        excl = valid & df.index.isin(excluded) if main else valid & False
        mask = valid & ~excl
        col = colors[i]
        lbl = s.get("label") or s["y"]
        ms_s = _num(s.get("size")) or ms
        alpha_s = _num(s.get("alpha")) or alpha
        mk = s.get("marker") if s.get("marker") not in (None, "", "auto") else \
            (MARKERS[i % len(MARKERS)] if vary_m else "o")
        ls = LINESTYLES.get(s.get("ls") or "-", "-")

        if excl.any() and x_numeric and c.get("showexcluded", True):
            ax.plot(x_plot_all[excl] if polar else x[excl], y[excl], linestyle="none", marker="x", color="0.6",
                    markersize=ms, markeredgewidth=0.8, zorder=1)
        if polar:
            xj = x.astype(float) + (jrng.uniform(-jitter, jitter, len(x)) if jitter else 0)
            xj = xj.clip(ax._sp_amin, ax._sp_amax) if jitter else xj
            x_plot_all = pd.Series(np.asarray(tx(xj)), index=x.index)
        if x_numeric and main:
            pp = {"label": lbl, "color": col,
                  "pts": [[int(r), float(x[r]), float(y[r]), bool(excl[r])] for r in df.index[valid]]}
            if polar:
                pp["phi"] = [float(x_plot_all[r]) for r in df.index[valid]]
            points.append(pp)

        x, y = x[mask], y[mask]
        yerr = yerr[mask].fillna(0) if yerr is not None else None
        xerr = xerr[mask].fillna(0) if xerr is not None else None
        if polar and xerr is not None:
            xerr = xerr * ax._sp_scale
        if kind in ("line", "step", "linemarkers") and x_numeric and c.get("sortx", True):
            order = np.argsort(x.values, kind="stable")
            x, y = x.iloc[order], y.iloc[order]
            yerr = yerr.iloc[order] if yerr is not None else None
            xerr = xerr.iloc[order] if xerr is not None else None

        px = x_plot_all[x.index] if polar else x
        yd = y                                           # what gets drawn
        if polar and (_num(c.get("ymin")) is not None or _num(c.get("ymax")) is not None):
            rlo = _num(c.get("ymin")) if _num(c.get("ymin")) is not None else -np.inf
            rhi = _num(c.get("ymax")) if _num(c.get("ymax")) is not None else np.inf
            yd = y.where((y >= rlo) & (y <= rhi))        # outside the radius range: not drawn
        vec = c.get("vector") and kind in ("scatter", "polar") and x_numeric and main
        if vec:
            if yerr is not None or xerr is not None:
                ax.errorbar(px, yd, yerr=yerr, xerr=xerr, fmt="none", ecolor="0.6",
                            elinewidth=0.6, alpha=alpha, zorder=1.5)
            lcol, ltop = _vectors(ax, px, yd, x.index, df, c, mapping, col, alpha)
            ax.plot([], [], color="0.3" if (mapping is not None and mapping.hue) else col,
                    linewidth=_num(c.get("vecwidth")) or 1.2, label=lbl)
            if lcol and i == 0:
                extra.append((Rectangle((0, 0), 0, 0, fill=False, edgecolor="none", visible=False),
                              f"line length ∝ {lcol} (longest = {ltop:.3g})"))
        elif mapped and main:
            neutral = "0.55" if mapping.hue else col
            if yerr is not None or xerr is not None:
                ax.errorbar(px, yd, yerr=yerr, xerr=xerr, fmt="none", ecolor=neutral,
                            elinewidth=0.7, alpha=alpha, zorder=1.5)
            if kind == "linemarkers":
                ax.plot(px, yd, color=neutral, linestyle=ls, zorder=1.6, alpha=alpha)
            fc = mapping.colors_for(x.index, col)
            sz = mapping.sizes_for(x.index, ms_s)          # the series' own size, if set
            groups = ([(m, (mapping.style["values"].loc[x.index] == k).values)
                       for k, m in mapping.style["markers"].items()]
                      if mapping.style else [(mk, np.ones(len(x), bool))])
            for m, g in groups:
                if not g.any():
                    continue
                gfc = [fc[j] for j in np.flatnonzero(g)]
                if open_m:
                    ax.scatter(px[g], yd[g], s=sz[g] ** 2, marker=m, facecolors="white",
                               edgecolors=gfc, linewidths=0.9, alpha=alpha_s, zorder=2)
                else:
                    ax.scatter(px[g], yd[g], s=sz[g] ** 2, marker=m, c=gfc,
                               edgecolors=c.get("edgecolor", "black") if c.get("edgecolor", "black") != "none" else "face",
                               linewidths=0.4, alpha=alpha_s, zorder=2)
            # series key (shape + series colour, or grey when colour is mapped)
            hcol = "0.3" if mapping.hue else col
            ax.plot([], [], linestyle="-" if kind == "linemarkers" else "none",
                    marker=None if mapping.style else mk, color=hcol,
                    markerfacecolor="white" if open_m else hcol, markersize=ms_s, label=lbl)
        else:
            mstyle = dict(marker=mk, markeredgecolor=col, markersize=ms_s,
                          markerfacecolor="white" if open_m else col, markeredgewidth=0.8)
            if kind in ("scatter", "polar"):
                fmt = dict(linestyle="none", **mstyle)
            elif kind == "linemarkers":
                fmt = dict(linestyle=ls, **mstyle)
            elif kind == "step":
                fmt = dict(linestyle=ls, drawstyle="steps-mid", marker="none")
            else:
                fmt = dict(linestyle=ls, marker="none")
            if yerr is not None or xerr is not None:
                ax.errorbar(px, yd, yerr=yerr, xerr=xerr, color=col, ecolor=col, alpha=alpha_s,
                            elinewidth=0.7, label=lbl, zorder=2 if main else 4, **fmt)
            else:
                ax.plot(px, yd, color=col, label=lbl, zorder=2 if main else 4, alpha=alpha_s, **fmt)

        # polynomial fit (+ optional seaborn confidence band)
        fit = int(s.get("fit") or 0)
        if fit and x_numeric and len(x) > fit:
            xv, yv = x.values.astype(float), y.values.astype(float)
            w = None
            if yerr is not None and c.get("weightedfit", False) and (yerr > 0).all():
                w = 1.0 / yerr.values.astype(float)
            coef = np.polyfit(xv, yv, fit, w=w)
            pred = np.polyval(coef, xv)
            ss_res = np.sum((yv - pred) ** 2)
            ss_tot = np.sum((yv - yv.mean()) ** 2)
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
            xx = np.linspace(xv.min(), xv.max(), 200)
            ci = int(c.get("ci") or 0)
            if ci and sns is not None and not polar:
                sns.regplot(x=xv, y=yv, order=fit, ci=ci, scatter=False, ax=ax,
                            color=col, truncate=True, seed=0, n_boot=500,
                            line_kws={"linewidth": 0, "zorder": 1})
                if w is not None:
                    msgs.append(f"{lbl}: confidence band is unweighted (bootstrap)")
            ax.plot(tx(xx), np.polyval(coef, xx), color=col, linestyle="--",
                    linewidth=0.9, marker="none", zorder=3,
                    label=_fit_label(coef, fit, r2) if c.get("fitlegend", True) else None)
            msgs.append(f"{lbl}: " + _fit_label(coef, fit, r2)
                        + (f"  [{int(excl.sum())} point(s) excluded]" if excl.any() else ""))
        elif fit and not x_numeric:
            msgs.append(f"{lbl}: fit skipped (X column is not numeric)")

        # mean / median / weighted-mean reference lines
        if any(c.get(k) for k in ("refmean", "refmedian", "refwmean")) and len(y) and c.get("refaxis", "y") != "none":
            prefix = f"{lbl}: " if len(series) > 1 else ""
            axis = c.get("refaxis", "y")
            if axis in ("y", "both"):
                _ref_lines(ax, y.values, yerr.values if yerr is not None else None,
                           c, col, prefix + ("Y " if axis == "both" else ""), "y", msgs)
            if axis in ("x", "both") and x_numeric and (axis == "x" or i == 0 or xcol is None):
                _ref_lines(ax, x.values.astype(float), xerr.values if xerr is not None else None,
                           c if (xerr is not None or axis == "x") else {**c, "refwmean": False},
                           col if axis == "x" else "0.35",
                           (prefix if axis == "x" else "") + ("X " if axis == "both" else ""), "x", msgs)

        if c.get("groupstats") and main and len(y):
            _group_summary(ax, df, c, x, y, xc, x_numeric, col, lbl if len(series) > 1 else "",
                           tx, msgs, groups_out)
    df = main_df
    _ref_extra(ax, df, c, msgs)

    if mapped:
        mapping.colorbar(fig, ax)
        extra.extend(mapping.legend_handles(ms, open_m))
    elif c.get("vector") and mapping is not None and mapping.hue:
        mapping.colorbar(fig, ax)


# ----------------------------------------------------------------- histogram
def _hist_edges(vals, c):
    allv = np.concatenate([v for v in vals if len(v)]) if any(len(v) for v in vals) else np.array([0.0, 1.0])
    lo = _num(c.get("binmin"))
    hi = _num(c.get("binmax"))
    lo = np.nanmin(allv) if lo is None else lo
    hi = np.nanmax(allv) if hi is None else hi
    if hi <= lo:
        hi = lo + 1
    mode = c.get("binmode", "auto")
    if mode == "width" and (_num(c.get("binwidth")) or 0) > 0:
        w = _num(c["binwidth"])
        start = lo if _num(c.get("binmin")) is not None else np.floor(lo / w) * w
        edges = np.arange(start, hi + w * 1.0001, w)
        if len(edges) > 2000:
            raise ValueError("Bin width is too small for this data range (over 2000 bins).")
        return edges
    if mode == "count" and str(c.get("bins", "")).strip().isdigit():
        return np.linspace(lo, hi, int(c["bins"]) + 1)
    return np.histogram_bin_edges(allv[(allv >= lo) & (allv <= hi)], bins="auto", range=(lo, hi))


def _kde_style(c, default):
    """KDE line colour, width and style (Plot type → KDE line)."""
    col = c.get("kdecolor") or ""
    col = (c.get("kdecustom") or "#000000") if col == "custom" else (col or default)
    lw = _num(c.get("kdewidth")) or c.get("linewidth", 1.2)
    return col, lw, LINESTYLES.get(c.get("kdels") or "-", "-")


def _kde_curve(v, edges, bw_adjust, scale):
    v = np.asarray(v, float)
    if len(v) < 2 or np.std(v) == 0:
        return None, None
    bw = 1.06 * np.std(v, ddof=1) * len(v) ** (-1 / 5) * bw_adjust
    xs = np.linspace(edges[0], edges[-1], 300)
    dens = np.exp(-0.5 * ((xs[:, None] - v[None, :]) / bw) ** 2).sum(axis=1) / (len(v) * bw * np.sqrt(2 * np.pi))
    return xs, dens * scale


def _hist(ax, fig, df, c, series, colors, msgs):
    keep = ~df.index.isin([int(r) for r in c.get("excluded", [])])
    vals = [pd.to_numeric(df.loc[keep, s["y"]], errors="coerce").dropna() for s in series]
    edges = _hist_edges([v.values for v in vals], c)
    widths = np.diff(edges)
    density = c.get("density", False)
    colorby = c.get("histcolor") if _ok(c.get("histcolor")) and c.get("histcolor") in df.columns else None
    if colorby and len(series) > 1:
        msgs.append("Colouring bins by a variable uses the first series only")
    ec = c.get("binedge", "black")
    ec = None if ec == "none" else ec
    for i, (s, v) in enumerate(zip(series, vals)):
        lbl = s.get("label") or s["y"]
        col = colors[i]
        counts, _ = np.histogram(v.values, edges)
        h = counts / (counts.sum() * widths) if density and counts.sum() else counts.astype(float)
        if colorby and i == 0:
            cv = pd.to_numeric(df.loc[v.index, colorby], errors="coerce").values
            which = np.clip(np.searchsorted(edges, v.values, side="right") - 1, 0, len(widths) - 1)
            fn = {"mean": np.nanmean, "median": np.nanmedian, "min": np.nanmin,
                  "max": np.nanmax, "std": np.nanstd}[c.get("histstat", "mean")]
            stat = np.full(len(widths), np.nan)
            for b in range(len(widths)):
                sel = cv[which == b]
                sel = sel[np.isfinite(sel)]
                if len(sel):
                    stat[b] = fn(sel)
            ok = np.isfinite(stat)
            lo, hi = _num(c.get("huemin")), _num(c.get("huemax"))
            lo = np.nanmin(stat) if lo is None and ok.any() else (lo if lo is not None else 0)
            hi = np.nanmax(stat) if hi is None and ok.any() else (hi if hi is not None else 1)
            norm = matplotlib.colors.Normalize(lo, hi if hi > lo else lo + 1)
            cmap = _cmap(c.get("cmap", "viridis"))
            fc = [cmap(norm(sv)) if np.isfinite(sv) else "0.85" for sv in stat]
            ax.bar(edges[:-1], h, width=widths, align="edge", color=fc, edgecolor=ec,
                   linewidth=0.5, label=lbl, zorder=2)
            sm = matplotlib.cm.ScalarMappable(norm=norm, cmap=cmap)
            cb = _colorbar(fig, ax, sm, c)
            cb.set_label(c.get("huelabel") or f"{c.get('histstat', 'mean')} {colorby}")
            cb.outline.set_linewidth(0.6)
        elif len(series) == 1 or c.get("histmulti", "step") == "overlay":
            ax.bar(edges[:-1], h, width=widths, align="edge", color=col, edgecolor=ec,
                   linewidth=0.5, alpha=1 if len(series) == 1 else 0.5, label=lbl, zorder=2)
        else:
            ax.stairs(h, edges, color=col, linewidth=c.get("linewidth", 1.2), label=lbl, zorder=2)
        if c.get("histkde"):
            scale = 1.0 if density else counts.sum() * widths.mean()
            xs, ys = _kde_curve(v.values, edges, _num(c.get("bw")) or 1, scale)
            if xs is not None:
                kc, klw, kls = _kde_style(c, col if not colorby else "black")
                ax.plot(xs, ys, color=kc, linewidth=klw, linestyle=kls, zorder=3)
        if any(c.get(k) for k in ("refmean", "refmedian")) and len(v) and c.get("refaxis", "y") != "none":
            prefix = f"{lbl}: " if len(series) > 1 else ""
            _ref_lines(ax, v.values, None, {**c, "refwmean": False},
                       "black" if colorby else (col if (len(series) > 1 or col != "#000000") else "#c8372d"),
                       prefix, "x", msgs)
    _ref_extra(ax, df, c, msgs, force_axis="x")
    msgs.append(f"{len(widths)} bins, width {widths.mean():.4g}" +
                ("" if np.allclose(widths, widths[0]) else " (mean)"))


# ----------------------------------------------------------------- bar
def _bar(ax, df, c, series, colors, xcol):
    xs = df[xcol] if xcol else pd.Series(np.arange(1, len(df) + 1))
    pos = np.arange(len(df))
    n = len(series)
    w = 0.8 / n
    for i, s in enumerate(series):
        err = df[s["yerr"]] if s.get("yerr") in df.columns else None
        ax.bar(pos + (i - (n - 1) / 2) * w, df[s["y"]], width=w, yerr=err,
               color=colors[i], edgecolor="black", linewidth=0.6, alpha=_num(c.get("alpha")) or 1,
               label=s.get("label") or s["y"], error_kw={"elinewidth": 0.8})
    ax.set_xticks(pos)
    ax.set_xticklabels([_fmt_cell(v) for v in xs], rotation=c.get("xrot", 0) or 0,
                       ha="right" if c.get("xrot") else "center")
    ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())


def _distribution(ax, df, c, series, colors, xcol, msgs):
    """KDE / box / violin / strip / swarm plots (seaborn)."""
    if sns is None:
        raise ValueError("This plot type needs seaborn (pip install seaborn).")
    kind = c["kind"]
    keep = ~df.index.isin([int(r) for r in c.get("excluded", [])])
    if kind == "kde":
        for i, s in enumerate(series):
            y = pd.to_numeric(df.loc[keep, s["y"]], errors="coerce").dropna()
            if y.nunique() < 2:
                msgs.append(f"{s['y']}: not enough distinct values for a KDE")
                continue
            kc, klw, kls = _kde_style(c, colors[i])
            sns.kdeplot(x=y, ax=ax, color=kc, fill=c.get("kdefill", True), linestyle=kls,
                        alpha=0.25 if c.get("kdefill", True) else 1, linewidth=klw,
                        bw_adjust=_num(c.get("bw")) or 1, label=s.get("label") or s["y"])
            if c.get("rug"):
                sns.rugplot(x=y, ax=ax, color=colors[i], height=0.04, linewidth=0.6)
        return

    # long-form table: one row per value, grouped by X categories or by series
    grouped = xcol and not pd.api.types.is_numeric_dtype(df[xcol]) and len(series) == 1
    if grouped:
        s = series[0]
        long = pd.DataFrame({"group": df.loc[keep, xcol].astype(str),
                             "value": pd.to_numeric(df.loc[keep, s["y"]], errors="coerce")})
        order = list(dict.fromkeys(long["group"]))
        pal = _colors(c.get("palette", "okabe-ito"), len(order))
    else:
        long = pd.concat([pd.DataFrame({"group": s.get("label") or s["y"],
                                        "value": pd.to_numeric(df.loc[keep, s["y"]], errors="coerce")})
                          for s in series], ignore_index=True)
        order = [s.get("label") or s["y"] for s in series]
        pal = colors
        if xcol and len(series) > 1 and not pd.api.types.is_numeric_dtype(df[xcol]):
            msgs.append("Grouping by X needs a single series; grouped by series instead")
    long = long.dropna()
    common = dict(data=long, x="group", y="value", order=order, hue="group", hue_order=order,
                  palette=pal, legend=False, ax=ax)
    lw = 0.8
    if kind == "box":
        sns.boxplot(**common, width=0.6, linewidth=lw, fliersize=c.get("markersize", 4) * 0.8,
                    showfliers=not c.get("distpoints"),
                    boxprops={"edgecolor": "black"}, whiskerprops={"color": "black"},
                    capprops={"color": "black"}, medianprops={"color": "black"},
                    flierprops={"marker": "o", "markerfacecolor": "none", "markeredgecolor": "0.3"})
    elif kind == "violin":
        sns.violinplot(**common, inner=c.get("violininner", "box"), linewidth=lw,
                       cut=0, density_norm="width", linecolor="black")
    elif kind == "strip":
        sns.stripplot(**common, jitter=0.2, size=c.get("markersize", 4),
                      edgecolor="black", linewidth=0.4)
    elif kind == "swarm":
        sns.swarmplot(**common, size=c.get("markersize", 4), edgecolor="black", linewidth=0.4)
    if c.get("distpoints") and kind in ("box", "violin"):
        sns.stripplot(data=long, x="group", y="value", order=order, color="black",
                      size=max(1.5, c.get("markersize", 4) * 0.6), jitter=0.15, alpha=0.6,
                      ax=ax, legend=False)
    ax.set_xlabel("")


def _legend(fig, ax, c, series, extra):
    leg = c.get("legend", "best")
    h, l = ax.get_legend_handles_labels()
    names = [s.get("label") or s["y"] for s in series]
    order = sorted(range(len(l)), key=lambda k: (l[k] not in names,
                                                l[k].startswith("y = "),
                                                names.index(l[k]) if l[k] in names else k))
    h, l = [h[k] for k in order], [l[k] for k in order]
    if len(series) == 1 and not c.get("legendsingle") and l and l[0] == names[0]:
        h, l = h[1:], l[1:]      # a lone series doesn't need its own entry
    if c.get("_sharedkey"):
        # one key for the whole figure: series, groups and mapped colour/size/shape go there;
        # entries carrying this panel's own numbers (fits, "mean = …") stay on the panel
        ax._sp_keys = ([(hh, ll) for hh, ll in zip(h, l) if "=" not in ll]
                       + [e for e in extra if "=" not in e[1]])
        keep = [k for k, ll in enumerate(l) if "=" in ll]
        h, l = [h[k] for k in keep], [l[k] for k in keep]
        extra = [e for e in extra if "=" in e[1]]    # e.g. "line length ∝ Dpar (longest = 3.87)"
        if leg in ("below", "outside"):
            leg = "best"
    h += [e[0] for e in extra]
    l += [e[1] for e in extra]
    if c.get("_multi") and leg in ("below", "outside") or (c.get("_multi") and leg == "best" and (extra or ax.name == "polar")):
        if not h:
            return
        if leg == "outside":
            L = ax.legend(h, l, loc="upper left", bbox_to_anchor=(1.02, 1.0), borderaxespad=0)
        else:
            L = ax.legend(h, l, loc="upper center", bbox_to_anchor=(0.5, -0.18), ncols=min(len(h), 2))
        for txt, lab in zip(L.get_texts(), l):
            if any(lab == e[1] and isinstance(e[0], Rectangle) for e in extra):
                txt.set_fontweight("bold")
        return
    if leg == "best" and (extra or ax.name == "polar"):
        leg = "below"            # keys too big for the data area; sector plots have no free corner
    if leg != "off" and h and leg == "below":
        n_base = len(h) - len(extra)
        groups, cur = [], []
        for e in extra:
            if isinstance(e[0], Rectangle) and cur:
                groups.append(cur)
                cur = []
            cur.append(e)
        if cur:
            groups.append(cur)
        if not groups:                 # nothing mapped: plain legend underneath
            groups = [list(zip(h, l))]
            n_base = 0
        elif n_base:                   # series / fit / reference lines stay on the axes
            LB = ax.legend(h[:n_base], l[:n_base], loc="best")
            LB.set_in_layout(False)
            ax.add_artist(LB)
        # long keys wrap into columns of at most 4 rows so the plot isn't squeezed
        wrapped = []
        for g in groups:
            wrapped += [g[i:i + 4] for i in range(0, len(g), 4)]
        groups = wrapped
        rows = max(len(g) for g in groups)
        blank = (Rectangle((0, 0), 0, 0, fill=False, edgecolor="none", visible=False), "")
        hh, ll = [], []
        for g in groups:
            g = g + [blank] * (rows - len(g))
            hh += [e[0] for e in g]
            ll += [e[1] for e in g]
        fs = plt.rcParams["legend.fontsize"]
        fs = fs if isinstance(fs, (int, float)) else c.get("fontsize", 9) - 1
        for size in (fs, fs - 1, fs - 2, fs - 3):
            L = fig.legend(hh, ll, loc="outside lower center", ncols=len(groups),
                           columnspacing=1.0, handletextpad=0.3, fontsize=max(size, 4.5))
            fig.canvas.draw()
            if L.get_window_extent().width <= fig.bbox.width * 0.98 or size <= 4.5:
                break
            L.remove()
        h, l = hh, ll
    elif leg != "off" and h:
        if leg == "outside":
            L = fig.legend(h, l, loc="outside right upper")
        elif ax.name == "polar" and leg == "best":
            # the corner opposite the arc's centre is empty in a sector plot
            L = ax.legend(h, l, loc="upper right", bbox_to_anchor=(1.08, 1.08))
            L.set_in_layout(False)
        else:
            L = ax.legend(h, l, loc=leg)
            L.set_in_layout(False)
            fig.canvas.draw()
            if L.get_window_extent().width > ax.get_window_extent().width * 0.95:
                L.remove()                      # too wide to sit inside the axes: put it underneath
                import textwrap
                fs = plt.rcParams["legend.fontsize"]
                fs = fs if isinstance(fs, (int, float)) else c.get("fontsize", 9) - 1
                for width, size in ((60, fs), (48, fs), (40, fs - 1), (34, fs - 2)):
                    lw = ["\n".join(textwrap.wrap(t, width)) or t for t in l]
                    if c.get("_multi"):
                        L = ax.legend(h, lw, loc="upper center", bbox_to_anchor=(0.5, -0.18), ncols=1, fontsize=size)
                        break
                    L = fig.legend(h, lw, loc="outside lower center", ncols=1, fontsize=size)
                    fig.canvas.draw()
                    if L.get_window_extent().width <= fig.bbox.width * 0.98:
                        break
                    L.remove()
                l = lw
    if leg != "off" and h:
        for txt, lab in zip(L.get_texts(), l):
            if any(lab == e[1] and isinstance(e[0], Rectangle) for e in extra):
                txt.set_fontweight("bold")


def _finish_legend(fig, ax, c, series, extra, kind, msgs, points):
    _legend(fig, ax, c, series, extra)
    if c.get("panel"):
        ax.text(-0.02, 1.02, c["panel"], transform=ax.transAxes, ha="right", va="bottom",
                fontweight="bold", fontsize=c.get("fontsize", 9) + 2)
    return fig, msgs, points


def _series_df(s, df):
    """The table a series draws from: the main table, or another sheet of the workbook."""
    src = s.get("source") or ""
    return DATA.get("tables", {}).get(src, pd.DataFrame()) if src else df


def _panel_series(c, df, msgs):
    series = [s for s in c.get("series", []) if s.get("y") in _series_df(s, df).columns]
    kind = c.get("kind", "scatter")
    if any(s.get("source") for s in series) and kind not in ("scatter", "linemarkers", "line", "step", "polar"):
        msgs.append("Series from other sheets are drawn in x–y and polar plots only")
        series = [s for s in series if not s.get("source")] or series[:0]
    return series


def _text_style(ax, c):
    """Per-panel size, position and spacing of the title, axis titles and tick labels."""
    def num(k, lo=None):
        v = _num(c.get(k))
        return None if v is None or (lo is not None and v < lo) else v
    if c.get("title"):
        kw = {"loc": c.get("titleloc") or "center"}
        if num("titlesize", 1):
            kw["fontsize"] = num("titlesize")
        if num("titlepad") is not None:
            kw["pad"] = num("titlepad")
        if c.get("titlebold"):
            kw["fontweight"] = "bold"
        ax.set_title("")
        ax.set_title(c["title"], **kw)
    polar = ax.name == "polar"
    if not polar:
        if num("xlabelsize", 1):
            ax.xaxis.label.set_fontsize(num("xlabelsize"))
        if num("xlabelpad") is not None:
            ax.xaxis.labelpad = num("xlabelpad")
        if c.get("xlabelloc") in ("left", "right"):
            ax.set_xlabel(ax.get_xlabel(), loc=c["xlabelloc"])
    if num("ylabelsize", 1):
        ax.yaxis.label.set_fontsize(num("ylabelsize"))
    if num("ylabelpad") is not None:
        ax.yaxis.labelpad = num("ylabelpad")
    if not polar:
        if c.get("ylabelloc") in ("bottom", "top"):
            ax.set_ylabel(ax.get_ylabel(), loc=c["ylabelloc"])
        if c.get("ylabelside") == "right":
            ax.yaxis.set_label_position("right")
    if num("ticksize", 1):
        ax.tick_params(axis="both", which="both", labelsize=num("ticksize"))


def _hide_axes(ax, c):
    """Per-panel axis hiding (useful in multi-panel figures)."""
    if c.get("hideaxes"):
        ax.set_axis_off()
        return
    if c.get("hidex"):
        ax.tick_params(axis="x", which="both", bottom=False, top=False, labelbottom=False, labeltop=False)
        ax.set_xlabel("")
    if c.get("hidey"):
        ax.tick_params(axis="y", which="both", left=False, right=False, labelleft=False, labelright=False)
        ax.set_ylabel("")


def draw_panel(fig, ax, c, df, groups):
    """Draw one plot (all of a panel's settings) into existing axes. Returns (msgs, points)."""
    msgs, points, extra = [], [], []
    series = _panel_series(c, df, msgs)
    kind = c.get("kind", "scatter")
    polar = kind == "polar"
    if polar:
        _polar_setup(ax, df, c, series)
    if not series:
        ax.text(0.5, 0.5, "Choose a Y column to plot", ha="center", va="center",
                transform=ax.transAxes, color="0.5")
        _text_style(ax, c)
        _hide_axes(ax, c)
        return msgs, points

    pal = _colors(c.get("palette", "okabe-ito"), len(series))
    colors = [s.get("color") or pal[i] for i, s in enumerate(series)]
    xcol = c.get("x") if c.get("x") in df.columns else None

    if kind == "bar":
        _bar(ax, df, c, series, colors, xcol)
    elif kind == "hist":
        _hist(ax, fig, df, c, series, colors, msgs)
    elif kind in ("kde", "box", "violin", "strip", "swarm"):
        _distribution(ax, df, c, series, colors, xcol, msgs)
    else:
        _xy(ax, fig, df, c, series, colors, xcol, msgs, points, extra, groups)
    if polar:
        xl = c.get("xlabel") or (xcol or "Index")
        yl = c.get("ylabel") or (series[0]["y"] if len(series) == 1 else "")
        _polar_finish(ax, c, df, series, xl, yl)
        if any(c.get(k) for k in ("hlines", "vlines")):
            _custom_lines(ax, c, kind)
        if c.get("title"):
            ax.set_title(c["title"])
        _legend(fig, ax, c, series, extra)
        if c.get("panel") and not c.get("_multi"):
            ax.text(-0.02, 1.02, c["panel"], transform=ax.transAxes, ha="right", va="bottom",
                    fontweight="bold", fontsize=c.get("fontsize", 9) + 2)
        _text_style(ax, c)
        _hide_axes(ax, c)
        return msgs, points

    # ---- labels
    xl, yl = c.get("xlabel"), c.get("ylabel")
    dist_grouped = kind in ("box", "violin", "strip", "swarm") and xcol \
        and not pd.api.types.is_numeric_dtype(df[xcol]) and len(series) == 1
    if not xl:
        if kind in ("hist", "kde"):
            xl = series[0]["y"] if len(series) == 1 else ""
        elif kind in ("box", "violin", "strip", "swarm"):
            xl = xcol if dist_grouped else ""
        else:
            xl = xcol or "Index"
    if not yl:
        if kind == "hist":
            yl = "Density" if c.get("density") else "Count"
        elif kind == "kde":
            yl = "Density"
        else:
            yl = series[0]["y"] if len(series) == 1 else ""
    ax.set_xlabel(xl)
    ax.set_ylabel(yl)
    if c.get("title"):
        ax.set_title(c["title"])

    # ---- scales and limits
    if c.get("xlog") and kind not in CATEGORICAL:
        ax.set_xscale("log")
    if c.get("ylog"):
        ax.set_yscale("log")
    lo, hi = _num(c.get("xmin")), _num(c.get("xmax"))
    if (lo is not None or hi is not None) and kind not in CATEGORICAL:
        ax.set_xlim(left=lo, right=hi)
    lo, hi = _num(c.get("ymin")), _num(c.get("ymax"))
    if lo is not None or hi is not None:
        ax.set_ylim(bottom=lo, top=hi)
    _custom_lines(ax, c, kind)
    if c.get("xinvert"):
        ax.invert_xaxis()
    if c.get("yinvert"):
        ax.invert_yaxis()
    if c.get("minor", True):
        if kind not in CATEGORICAL and ax.get_xscale() == "linear":
            ax.xaxis.set_minor_locator(AutoMinorLocator())
        if ax.get_yscale() == "linear":
            ax.yaxis.set_minor_locator(AutoMinorLocator())
    grid = c.get("grid", "none")
    if grid != "none":
        ax.grid(True, which="major", color="0.85", linewidth=0.5, zorder=0)
        if grid == "both":
            ax.grid(True, which="minor", color="0.93", linewidth=0.4, zorder=0)
        ax.set_axisbelow(True)

    # ---- legend
    _legend(fig, ax, c, series, extra)

    if kind in CATEGORICAL:
        ax.tick_params(axis="x", which="both", top=False, bottom=kind == "bar")
        if kind != "bar" and c.get("xrot"):
            plt.setp(ax.get_xticklabels(), rotation=c["xrot"], ha="right")
    if c.get("despine"):
        if sns is not None:
            sns.despine(ax=ax)
        else:
            ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(which="both", top=False, right=False)

    if c.get("panel") and not c.get("_multi"):
        ax.text(-0.02, 1.02, c["panel"], transform=ax.transAxes, ha="right", va="bottom",
                fontweight="bold", fontsize=c.get("fontsize", 9) + 2)
    _text_style(ax, c)
    _hide_axes(ax, c)
    return msgs, points


def make_figure(c, df=None):
    df = DATA["df"] if df is None else df
    if df is None:
        raise ValueError("No data loaded.")
    groups = DATA.setdefault("groups", [])
    groups.clear()
    size = FIG_SIZES.get(c.get("size", "single"), None)
    if size is None:
        size = (_num(c.get("width")) or 3.5, _num(c.get("height")) or 2.6)
    with plt.rc_context(_style(c)):
        fig = plt.figure(figsize=size, layout="constrained")
        ax = fig.add_subplot(projection="polar" if c.get("kind") == "polar" else None)
        msgs, points = draw_panel(fig, ax, c, df, groups)
    return fig, msgs, points


def _geometry(fig, ax, points=None, draw=True):
    """Axes position (fractions of the figure, origin top-left) and data limits.
    For polar axes, each point's pixel position is attached instead (key "px")."""
    if draw:
        fig.canvas.draw()
    p = ax.get_position()
    if ax.name == "polar":
        W, H = fig.bbox.width, fig.bbox.height
        for pp in points or []:
            phi = np.asarray(pp.pop("phi", []), float)
            r = np.asarray([q[2] for q in pp["pts"]], float)
            xy = ax.transData.transform(np.c_[phi, r]) if len(phi) else np.zeros((0, 2))
            r0, r1 = sorted(ax.get_ylim())
            t0, t1 = float(ax._sp_tx(ax._sp_amin)), float(ax._sp_tx(ax._sp_amax))
            inside = (r >= r0) & (r <= r1) & (phi >= min(t0, t1) - 1e-9) & (phi <= max(t0, t1) + 1e-9)
            pp["px"] = [[float(a / W), float(1 - b / H)] if ok else [None, None]
                        for (a, b), ok in zip(xy, inside)]
        return {"polar": True, "x0": p.x0, "x1": p.x1, "y0": 1 - p.y1, "y1": 1 - p.y0}
    return {"x0": p.x0, "x1": p.x1, "y0": 1 - p.y1, "y1": 1 - p.y0,
            "xlim": list(map(float, ax.get_xlim())), "ylim": list(map(float, ax.get_ylim())),
            "xlog": ax.get_xscale() == "log", "ylog": ax.get_yscale() == "log"}


META_KEY = {"png": "SciPlot", "svg": "Description", "pdf": "Subject"}


def render(cfg_json, fmt="png", dpi=200, meta=""):
    """Render the figure; returns JSON {data (base64), msgs, geom, points, colors, swatches}.
    `meta` (the plot setup, "SciPlot1:<base64>!") is embedded in the file's metadata so an
    exported figure can be dropped back onto the site to reopen it."""
    c = json.loads(cfg_json)
    fig, msgs, points = make_figure(c)
    buf = io.BytesIO()
    with plt.rc_context(_style(c)):
        geom = _geometry(fig, fig.axes[0], points) if fmt == "png" else None
        md = {META_KEY[fmt]: meta} if meta and fmt in META_KEY else None
        _cbar_boxes(fig)
        fig.savefig(buf, format=fmt, dpi=dpi, metadata=md)
    for pp in points:
        pp.pop("phi", None)
    plt.close(fig)
    pal = _colors(c.get("palette", "okabe-ito"), len(c.get("series", [])))
    return _dumps({"data": base64.b64encode(buf.getvalue()).decode("ascii"), "msgs": msgs,
                       "geom": geom, "points": points,
                       "colors": [s.get("color") or pal[i] for i, s in enumerate(c.get("series", []))],
                       "swatches": _colors(c.get("palette", "okabe-ito"), 8),
                       "ngroups": len(DATA.get("groups") or [])})


def group_table_csv():
    """The group summary from the last render, as CSV."""
    rows = DATA.get("groups") or []
    if not rows:
        return ""
    cols = ["series", "groupby", "group", "x", "n", "statistic", "centre", "spread", "lo", "hi",
            "mean", "median", "sd", "se", "q1", "q3", "min", "max"]
    d = pd.DataFrame(rows)
    if "panel" in d:
        cols = ["panel"] + cols
    return d.reindex(columns=cols).to_csv(index=False, float_format="%.6g")


# ------------------------------------------------------------ multi-panel figures
LAYOUT_WIDTHS = {"single": 3.5, "onehalf": 5.5, "double": 7.2, "screen": 10.0}


def _panel_label(i, style):
    a, A = chr(97 + i % 26), chr(65 + i % 26)
    return {"(a)": f"({a})", "a": a, "a)": f"{a})", "A": A, "(A)": f"({A})", "A)": f"{A})",
            "a.": f"{a}.", "none": ""}.get(style, f"({a})")


def _share_limits(cells, which, group):
    """Give panels in the same group (all / row / column) a common X or Y range."""
    groups = {}
    for ax, pc, r, col in cells:
        if ax.name == "polar" or pc.get("kind") in CATEGORICAL or (which == "x" and pc.get("kind") in ("bar",)):
            continue
        key = "all" if group == "all" else (col if group == "col" else r)
        # only panels showing the same variable on that axis are linked
        if which == "x":
            var = (pc.get("series") or [{}])[0].get("y") if pc.get("kind") in ("hist", "kde") else pc.get("x")
        else:
            var = "count" if pc.get("kind") == "hist" else ("density" if pc.get("kind") == "kde"
                                                             else (pc.get("series") or [{}])[0].get("y"))
        groups.setdefault((key, var), []).append(ax)
    for axs in groups.values():
        if len(axs) < 2:
            continue
        get = (lambda a: a.get_xlim()) if which == "x" else (lambda a: a.get_ylim())
        lims = [get(a) for a in axs]
        flipped = [l[0] > l[1] for l in lims]
        lo = min(min(l) for l in lims)
        hi = max(max(l) for l in lims)
        for a, f in zip(axs, flipped):
            (a.set_xlim if which == "x" else a.set_ylim)((hi, lo) if f else (lo, hi))


def _panel_frame(sheet):
    """The table a panel plots: the loaded sheet, or another sheet of the same workbook read
    with the same header settings and calculated columns (cached)."""
    ld = DATA.get("load") or {}
    if not sheet or ld.get("allsheets") or sheet == DATA.get("sheet") or sheet not in (DATA.get("sheets") or []):
        return DATA["df"]
    cache = DATA.setdefault("sheetdf", {})
    if sheet in cache:
        return cache[sheet]
    header, headerrow = ld.get("header", "auto"), max(1, int(ld.get("headerrow") or 1))
    raw = pd.ExcelFile(ld["path"]).parse(sheet, header=None)
    if header == "row":
        raw = raw.iloc[headerrow - 1:]
    df = _apply_header(raw.reset_index(drop=True), "yes" if header == "row" else header)
    if ld.get("skipafter") and header == "row":
        df = df.iloc[int(ld["skipafter"]):]
    df = _coerce(df)
    for item in json.loads(DATA.get("calc") or "[]"):
        name, expr = str(item.get("name", "")).strip(), str(item.get("expr", "")).strip()
        if name and expr:
            try:
                df[name] = eval_formula(expr, df)
            except Exception:
                pass
    cache[sheet] = df
    return df


def _common_hue(panels):
    """Panels colouring by the same column share one colour scale: the same range for a
    numeric column and the same colour for each category."""
    out = [dict(p) for p in panels]
    bycol = {}
    for i, pc in enumerate(out):
        if _ok(pc.get("hue")):
            bycol.setdefault((pc["hue"], bool(pc.get("huecat")), bool(pc.get("huelog"))), []).append(i)
    for (col, cat, log), idx in bycol.items():
        if len(idx) < 2:
            continue
        vals = []
        for i in idx:
            d = _panel_frame(out[i].get("sheet"))
            if col in d.columns:
                vals.append(d[col])
        if not vals:
            continue
        v = pd.concat(vals, ignore_index=True)
        if pd.api.types.is_numeric_dtype(v) and not cat:
            vv = v.astype(float)
            vv = vv[np.isfinite(vv) & ((vv > 0) if log else True)]
            if len(vv):
                robust = any(out[i].get("huerobust") for i in idx)
                lo, hi = (np.percentile(vv, 2), np.percentile(vv, 98)) if robust else (vv.min(), vv.max())
                for i in idx:
                    out[i]["_hue_lo"], out[i]["_hue_hi"] = float(lo), float(hi)
        else:
            cats = list(dict.fromkeys(v.astype(str).where(v.notna(), "–")))
            for i in idx:
                out[i]["_hue_cats"] = cats
    return out


def _shared_key(fig, axes, mode, base):
    """One legend and one colour bar per mapped variable for the whole figure."""
    seen, H, L = set(), [], []
    head = ""
    for ax in axes:
        head, gi = "", 0
        for h, l in getattr(ax, "_sp_keys", []):
            if isinstance(h, Rectangle):          # a group title (colour by …, size by …, shape by …)
                head, gi = l, gi + 1
            key = (gi, head, l)
            if key in seen:
                continue
            seen.add(key)
            H.append(h)
            L.append(l)
    # panels share a colour bar only when they show the same column on the same scale;
    # a panel with its own scale keeps its own bar
    cbars = {}
    for ax in axes:
        m = getattr(ax, "_sp_cbar", None)
        if m is not None:
            nm = m.hue["norm"]
            key = (m.hue["col"], m.c.get("huelabel") or m.hue["col"], round(float(nm.vmin), 9),
                   round(float(nm.vmax), 9), getattr(m.hue["cmap"], "name", ""),
                   isinstance(nm, matplotlib.colors.LogNorm))
            cbars.setdefault(key, [m, []])[1].append(ax)
    loc = "right" if mode == "right" else "bottom"
    for key, (m, group) in cbars.items():
        lab = key[1]
        sm = matplotlib.cm.ScalarMappable(norm=m.hue["norm"], cmap=m.hue["cmap"])
        if len(group) == 1 and len(cbars) > 1:
            cb = _colorbar(fig, group[0], sm, m.c, extend=m._extend())
        else:
            ln, th = _num(base.get("cbarlen")), _num(base.get("cbarwid"))
            cb = fig.colorbar(sm, ax=group if len(cbars) > 1 else axes, location=loc, pad=0.02,
                              fraction=0.04, aspect=max(5, 140 / th) if th else 35,
                              shrink=min(1.0, ln / 100) if ln else 0.8, extend=m._extend())
        cb.set_label(lab)
        if not getattr(cb, "_sp_styled", False):
            _style_cbar(cb, base)
        if isinstance(m.hue["norm"], matplotlib.colors.LogNorm):
            (cb.ax.xaxis if cb.orientation == "horizontal" else cb.ax.yaxis).set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        cb.outline.set_linewidth(0.6)
    if not H:
        return
    if mode == "right":
        Lg = fig.legend(H, L, loc="outside right upper")
    else:
        # one column per group (colour / size / shape), wrapped at 4 rows
        groups, cur = [], []
        for h, l in zip(H, L):
            if isinstance(h, Rectangle) and cur:
                groups.append(cur)
                cur = []
            cur.append((h, l))
        groups.append(cur)
        wrapped = []
        for g in groups:
            wrapped += [g[i:i + 4] for i in range(0, len(g), 4)]
        rows = max(len(g) for g in wrapped)
        blank = (Rectangle((0, 0), 0, 0, fill=False, edgecolor="none", visible=False), "")
        hh, ll = [], []
        for g in wrapped:
            g = g + [blank] * (rows - len(g))
            hh += [e[0] for e in g]
            ll += [e[1] for e in g]
        Lg = fig.legend(hh, ll, loc="outside lower center", ncols=len(wrapped),
                        columnspacing=1.0, handletextpad=0.3)
        H, L = hh, ll
    for txt, h in zip(Lg.get_texts(), H):
        if isinstance(h, Rectangle) and txt.get_text():
            txt.set_fontweight("bold")


def _nudge_gaps(fig, cells, rows, cols, gx, gy):
    """Fine-tune the gaps between panels after the automatic layout (gx, gy in inches; negative
    pulls panels together). The figure shrinks or grows to match, so nothing is cut off."""
    fig.canvas.draw()
    fig.set_layout_engine("none")
    W, H = fig.get_size_inches()
    def box(a):                       # (left, top, width, height) in inches from the top-left
        p = a.get_position()
        return p.x0 * W, (1 - p.y1) * H, p.width * W, p.height * H
    pan = [(box(a), r, c) for a, _, r, c in cells]
    def bounds(k, n):                 # midpoints between neighbouring columns / rows
        lo = [min(b[k] for b, *rc in pan if rc[1 if k == 0 else 0] == j) for j in range(n)
              if any(rc[1 if k == 0 else 0] == j for _, *rc in pan)]
        hi = [max(b[k] + b[k + 2] for b, *rc in pan if rc[1 if k == 0 else 0] == j) for j in range(n)
              if any(rc[1 if k == 0 else 0] == j for _, *rc in pan)]
        return [(hi[j - 1] + lo[j]) / 2 for j in range(1, len(lo))]
    bx, by = bounds(0, cols), bounds(1, rows)
    pw = np.median([b[2] for b, *_ in pan]) if pan else W
    ph = np.median([b[3] for b, *_ in pan]) if pan else H
    nx, ny = len(bx) + 1, len(by) + 1
    W2, H2 = W + (nx - 1) * gx, H + (ny - 1) * gy
    if W2 < 1 or H2 < 1:
        return
    for a in fig.axes:
        x, y, w, h = box(a)
        if w > 1.5 * pw and nx > 1:   # spans several columns (shared colour bar): stretch it
            w = w * (W2 / W)
            x = x * (W2 / W)
        else:
            j = sum(x + w / 2 > b for b in bx)
            x += j * gx
        if h > 1.5 * ph and ny > 1:
            h = h * (H2 / H)
            y = y * (H2 / H)
        else:
            i = sum(y + h / 2 > b for b in by)
            y += i * gy
        a.set_position([x / W2, 1 - (y + h) / H2, w / W2, h / H2])
    fig.set_size_inches(W2, H2)


def render_layout(payload_json, fmt="png", dpi=200, meta=""):
    """Several panels in a grid. payload = {layout, panels: [cfg...], active, excluded}."""
    import math
    P = json.loads(payload_json)
    L = P.get("layout", {})
    panels = P.get("panels") or []
    active = int(P.get("active") or 0)
    df = DATA["df"]
    if df is None:
        raise ValueError("No data loaded.")
    groups = DATA.setdefault("groups", [])
    groups.clear()
    n = max(len(panels), 1)
    cols = int(_num(L.get("cols")) or math.ceil(math.sqrt(n)))
    cols = max(1, min(cols, max(n, max([int(_num(q.get("spanc")) or 1) for q in panels] or [1]))))
    # place panels row by row; a panel may span several columns / rows (patchwork-style)
    spans, taken = [], set()
    for q in panels:
        sc = max(1, min(int(_num(q.get("spanc")) or 1), cols))
        sr = max(1, int(_num(q.get("spanr")) or 1))
        k = 0
        while True:
            r0, c0 = divmod(k, cols)
            if c0 + sc <= cols and not any((r0 + a, c0 + b) in taken for a in range(sr) for b in range(sc)):
                break
            k += 1
        taken |= {(r0 + a, c0 + b) for a in range(sr) for b in range(sc)}
        spans.append((r0, c0, sr, sc))
    rows = max([r0 + sr for r0, _, sr, _ in spans] or [1])
    width = LAYOUT_WIDTHS.get(L.get("size", "double")) or _num(L.get("width")) or 7.2
    if L.get("size") == "custom":
        width = _num(L.get("width")) or 7.2
    aspect = _num(L.get("aspect")) or 0.75
    # each row is as tall as its tallest panel; polar sectors have their own shape
    def _shape(pc):
        if pc.get("kind") != "polar":
            return aspect
        return {"half": 0.58}.get(pc.get("polstyle", "quarter"), 1.0)
    def _ratios(txt, k):
        try:
            v = [float(t) for t in str(txt or "").replace(";", ",").split(",") if t.strip()]
        except ValueError:
            v = []
        v = [x for x in v if x > 0]
        return (v + [v[-1]] * k)[:k] if v else None
    wr = _ratios(L.get("colwidths"), cols) or [1.0] * cols
    hr = _ratios(L.get("rowheights"), rows)
    if hr is None:          # auto: each row as tall as its tallest single-row panel needs
        unit = width / sum(wr)
        hr = []
        for r in range(rows):
            need = []
            for q, (r0, c0, sr, sc) in zip(panels, spans):
                if r0 == r and sr == 1:
                    pa = _num(q.get("paspect")) or _shape(q)
                    need.append(pa * sum(wr[c0:c0 + sc]) / (sum(wr) / cols))
            hr.append(max(need or [aspect]))
        ratios = hr
        height_auto = unit * (sum(wr) / cols) * sum(ratios) + 0.25
    else:
        ratios = hr
        height_auto = (width / cols) * sum(ratios) * aspect + 0.25
    height = _num(L.get("height")) if L.get("size") == "custom" and _num(L.get("height")) else height_auto
    base = panels[active] if panels else {}
    cells, results = [], []
    legmode = L.get("legend", "each")
    shared = legmode in ("right", "below")
    panels = _common_hue(panels)
    with plt.rc_context(_style(base)):
        fig = plt.figure(figsize=(width, height), layout="constrained")
        sp = _num(L.get("spacing"))
        sp = 0.02 if sp is None else sp
        fig.get_layout_engine().set(wspace=sp, hspace=sp, w_pad=0.03, h_pad=0.03)
        gs = fig.add_gridspec(rows, cols, height_ratios=ratios, width_ratios=wr)
    for i, pc in enumerate(panels):
        r, col, sr, sc = spans[i]
        sheet = pc.get("sheet") or DATA.get("sheet")
        ex = P.get("excluded", [])
        if isinstance(ex, dict):
            ex = ex.get(sheet or "", [])
        pc = {**pc, "_multi": True, "excluded": ex, "_sharedkey": shared}
        n0 = len(groups)
        with plt.rc_context(_style(pc)):
            ax = fig.add_subplot(gs[r:r + sr, col:col + sc], projection="polar" if pc.get("kind") == "polar" else None)
            ax._sp_cell = (sr, sc)
            if _num(pc.get("paspect")) and pc.get("kind") != "polar":
                ax.set_box_aspect(_num(pc.get("paspect")))       # this panel's own height ÷ width
            try:
                msgs, pts = draw_panel(fig, ax, pc, _panel_frame(pc.get("sheet")), groups)
            except Exception as e:
                ax.clear()
                ax.text(0.5, 0.5, f"Panel {_panel_label(i, '(a)')}: {e}", ha="center", va="center",
                        transform=ax.transAxes, color="#c8372d", wrap=True, fontsize=7)
                msgs, pts = [f"error: {e}"], []
        for g in groups[n0:]:
            g["panel"] = _panel_label(i, "a")
        cells.append((ax, pc, r, col))
        results.append({"msgs": msgs, "points": pts if i == active else []})

    # ---- figure-wide options
    if shared:
        with plt.rc_context(_style(base)):
            _shared_key(fig, [cl[0] for cl in cells], legmode, base)
    if L.get("sharex", "none") != "none":
        _share_limits(cells, "x", L["sharex"])
    if L.get("sharey", "none") != "none":
        _share_limits(cells, "y", L["sharey"])
    occupied = set()
    for ax, _, r, col in cells:
        sr, sc = getattr(ax, "_sp_cell", (1, 1))
        occupied |= {(r + a, col + b) for a in range(sr) for b in range(sc)}
    inner = L.get("inner", "keep")
    for ax, pc, r, col in cells:
        if ax.name == "polar":
            continue
        sr, sc = getattr(ax, "_sp_cell", (1, 1))
        below = any((r + sr, col + b) in occupied for b in range(sc))
        left_of = col > 0
        last_in_row = (r, col + sc) not in occupied
        my_ = L.get("mirrory", "none")
        mirrored = (my_ == "right" and last_in_row and col > 0) or my_ == "all"
        if inner in ("titles", "all"):
            if below:
                ax.set_xlabel("")
            if left_of and not (mirrored and last_in_row):
                ax.set_ylabel("")
        if inner == "all":
            if below:
                ax.tick_params(axis="x", which="both", labelbottom=False)
            if left_of:
                ax.tick_params(axis="y", which="both", labelleft=False)
        last_in_row = (r, col + sc) not in occupied
        my = L.get("mirrory", "none")
        if (my == "right" and last_in_row and col > 0) or my == "all":
            # Y axis (tick labels + title) on the right-hand side of this panel
            ax.yaxis.set_label_position("right")
            show = not (inner == "all" and my == "all" and not last_in_row)
            ax.tick_params(axis="y", which="both", labelleft=False, labelright=show)
            if not show:
                ax.set_ylabel("")
        mx = L.get("mirrorx", "none")
        if (mx == "top" and r == 0 and rows > 1) or mx == "all":
            ax.xaxis.set_label_position("top")
            ax.tick_params(axis="x", which="both", labelbottom=False, labeltop=True)
    style = L.get("labels", "(a)")
    for i, (ax, pc, r, col) in enumerate(cells):
        text = pc.get("panel") or _panel_label(i, style)
        if not text:
            continue
        fs = (pc.get("fontsize", 9) or 9) + 2
        if L.get("labelpos", "outside") == "inside":
            ax.text(0.03, 0.97, text, transform=ax.transAxes, ha="left", va="top", fontweight="bold", fontsize=fs,
                    zorder=10, bbox=dict(boxstyle="square,pad=0.15", fc="white", ec="none", alpha=0.8))
        else:
            xy = (-0.10, 1.06) if ax.name == "polar" else (-0.02, 1.02)
            ax.text(*xy, text, transform=ax.transAxes, ha="right", va="bottom", fontweight="bold", fontsize=fs)

    gx, gy = _num(L.get("gapx")) or 0.0, _num(L.get("gapy")) or 0.0
    if gx or gy:
        with plt.rc_context(_style(base)):
            _nudge_gaps(fig, cells, rows, cols, gx / 25.4, gy / 25.4)

    buf = io.BytesIO()
    with plt.rc_context(_style(base)):
        geoms = []
        if fmt == "png":
            fig.canvas.draw()
            for i, (ax, pc, r, col) in enumerate(cells):
                g = _geometry(fig, ax, results[i]["points"] if i == active else [], draw=False)
                geoms.append(g)
        md = {META_KEY[fmt]: meta} if meta and fmt in META_KEY else None
        _cbar_boxes(fig)
        fig.savefig(buf, format=fmt, dpi=dpi, metadata=md)
    plt.close(fig)
    for res in results:
        for pp in res["points"]:
            pp.pop("phi", None)
    act = panels[active] if panels else {}
    pal = _colors(act.get("palette", "okabe-ito"), len(act.get("series", [])))
    return _dumps({"data": base64.b64encode(buf.getvalue()).decode("ascii"),
                   "msgs": results[active]["msgs"] if results else [],
                   "geom": geoms[active] if geoms else None, "geoms": geoms,
                   "points": results[active]["points"] if results else [],
                   "rows": rows, "cols": cols, "active": active,
                   "colors": [s.get("color") or pal[k] for k, s in enumerate(act.get("series", []))],
                   "swatches": _colors(act.get("palette", "okabe-ito"), 8),
                   "ngroups": len(groups)})


# ------------------------------------------------------------ subsampling (running mean / 2σ)
SS_SIZES = {**FIG_SIZES, "wide": (10.0, 5.5)}


def running_mean(v):
    return np.cumsum(v) / np.arange(1, len(v) + 1)


def running_2sd(v):
    """2 × sample SD (ddof=1) of the first n values, n = 1..N (n = 1 is NaN)."""
    v = np.asarray(v, float)
    v = v - v.mean()                                   # centre for numerical stability
    n = np.arange(1, len(v) + 1)
    s1, s2 = np.cumsum(v), np.cumsum(v ** 2)
    with np.errstate(invalid="ignore", divide="ignore"):
        var = (s2 - s1 ** 2 / n) / (n - 1)
    return 2 * np.sqrt(np.clip(var, 0, None))


def _smooth(v, w):
    """Centred moving average (window w, shrinking at the ends)."""
    w = int(w or 1)
    v = np.asarray(v, float)
    if w <= 1 or len(v) < 3:
        return v
    w = min(w, len(v) if len(v) % 2 else len(v) - 1)
    k = np.ones(w)
    ok = np.isfinite(v)
    num = np.convolve(np.where(ok, v, 0), k, "same")
    den = np.convolve(ok.astype(float), k, "same")
    with np.errstate(invalid="ignore", divide="ignore"):
        return num / den


def _settle(run, ref, tol, start, absolute=False):
    """Smallest n after which the running value stays within ±tol of ref: tol is a fraction of
    ref, or (absolute) a value in the data's own units. run[k] belongs to n = k + 1; values
    before n = start are ignored."""
    if len(run) < start or not np.isfinite(ref) or (ref == 0 and not absolute):
        return None
    seg = run[start - 1:]
    band = tol if absolute else tol * abs(ref)
    out = np.where(~(np.abs(seg - ref) <= band))[0]
    return start if len(out) == 0 else int(out[-1] + start + 1)


def _ss_data(c):
    """Selected values (after filters) and the same before the Dpar-range cut."""
    ss = c.get("ss", {})
    src = ss.get("source") or ""
    df = DATA.get("tables", {}).get(src) if src else DATA["df"]
    if df is None:
        raise ValueError("No data loaded.")
    val = ss.get("value")
    if val not in df.columns:
        raise ValueError("Choose the column to sample (Value).")
    keep = pd.Series(True, index=df.index)
    if not src and c.get("excluded"):
        keep &= ~df.index.isin([int(r) for r in c["excluded"]])
    wc, wv = ss.get("wherecol"), ss.get("whereval")
    if _ok(wc) and wc in df.columns and wv not in (None, ""):
        col = df[wc]
        if pd.api.types.is_numeric_dtype(col) and _num(wv) is not None:
            keep &= np.isclose(col.astype(float), _num(wv))
        else:
            keep &= col.astype(str) == str(wv)
    labels = []
    ac = ss.get("anglecol")
    if _ok(ac) and ac in df.columns:
        a = pd.to_numeric(df[ac], errors="coerce")
        lo, hi = _num(ss.get("amin")), _num(ss.get("amax"))
        if lo is not None:
            keep &= a >= lo
        if hi is not None:
            keep &= a <= hi
        if lo is not None or hi is not None:
            labels.append(f"{ac} {'' if lo is None else f'{lo:g}'}–{'' if hi is None else f'{hi:g}'}")
    ref_keep = keep.copy()
    dc = ss.get("dparcol")
    cut = False
    if _ok(dc) and dc in df.columns:
        d = pd.to_numeric(df[dc], errors="coerce")
        lo, hi = _num(ss.get("dmin")), _num(ss.get("dmax"))
        if lo is not None:
            keep &= d >= lo
        if hi is not None:
            keep &= d <= hi
        if lo is not None or hi is not None:
            cut = True
            labels.append(f"{dc} {'' if lo is None else f'{lo:g}'}–{'' if hi is None else f'{hi:g}'}")
    x = pd.to_numeric(df.loc[keep, val], errors="coerce").dropna().to_numpy(float)
    x_ref = pd.to_numeric(df.loc[ref_keep, val], errors="coerce").dropna().to_numpy(float)
    where = f"{wc} = {wv}" if (_ok(wc) and wv not in (None, "")) else (f"sheet {src}" if src else "")
    return x, x_ref, cut, labels, where


def _ss_runs(c, x):
    ss = c.get("ss", {})
    mode = ss.get("mode", "mean")
    f = running_mean if mode == "mean" else running_2sd
    N = len(x)
    rng = np.random.default_rng(int(_num(ss.get("seed")) or 0))
    run = f(x[rng.permutation(N)]) if N else np.array([])
    ghosts = [f(x[rng.permutation(N)]) for _ in range(int(_num(ss.get("ghosts")) or 0))] if N else []
    return run, ghosts


def _ss_look(c, L):
    """Page settings with the Subsampling tab's own theme / font size, if set there."""
    c = dict(c)
    if L.get("theme"):
        c["theme"] = L["theme"]
    if _num(L.get("fontsize")):
        c["fontsize"] = _num(L.get("fontsize"))
    return c


def _ss_bg(fig, ax, L):
    if L.get("bgon"):
        ax.set_facecolor(L.get("bg") or "#ffffff")


def _ss_size(L):
    """Figure size (inches) for the Subsampling tab: a preset, or custom W × H; an optional
    height ÷ width overrides the height."""
    if L.get("size") == "custom":
        w, h = _num(L.get("width")) or 7.2, _num(L.get("height")) or 4.0
    else:
        w, h = SS_SIZES.get(L.get("size") or "double", SS_SIZES["double"])
    a = _num(L.get("aspect"))
    if a:
        h = w * a
    return max(w, 1.0), max(h, 0.8)


def _ss_draw(fig, ax, c, rc):
    """One running mean / running 2σ plot on ax. Returns (messages, sizes)."""
    ss = c.get("ss", {})
    mode = ss.get("mode", "mean")
    x, x_ref, cut, labels, where = _ss_data(c)
    N = len(x)
    run, ghosts = _ss_runs(c, x)
    start = 1 if mode == "mean" else 2
    n = int(_num(ss.get("n")) or N)
    n = max(min(n, N), start) if N else 0
    absolute = ss.get("tolunit") == "abs"            # ± in the tracked value's own units
    tol = _num(ss.get("tol")) or (2.0 if mode == "mean" else 10.0)
    if not absolute:
        tol /= 100
    tol_txt = f"±{tol:g}" if absolute else f"±{tol * 100:g}%"
    msgs = []
    if N < start + 1:
        ax.text(0.5, 0.5, f"Need at least {start + 1} values in this selection (have {N})",
                ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
    else:
        ns = np.arange(1, N + 1)
        val = ss.get("value")
        run_col = ss.get("runcolor") or "#7B3294"
        ref_col = ss.get("refcolor") or "#1f77b4"
        sty = lambda k, col, w: (ss.get(k + "color") or col, _num(ss.get("w" + k)) or w)
        sd_col, sd_w = sty("sd", ref_col, 1)
        fun_col, fun_w = sty("fun", "#808080", 1)
        cut_col, cut_w = sty("cut", "#D55E00", 1.2)
        gh_col, gh_w = sty("ghost", "#8c8c8c", 0.8)
        line_col, line_w = sty("line", run_col, 1.6)
        ref_w = _num(ss.get("wref")) or 2
        vals = ss.get("lgvalues", True)              # numbers in the legend labels
        def lab(key, full, short):
            """legend label, or None when this element is left out of the legend"""
            return (full if vals else short) if ss.get("lg" + key, key != "ghost") else None
        if mode == "mean":
            ref = float(x.mean())
            sd = float(x.std(ddof=1)) if N > 1 else np.nan
            ax.axhline(ref, color=ref_col, lw=ref_w, zorder=2,
                       label=lab("ref", f"Mean of all selected data ({ref:.3f}, N = {N})", "Mean of all selected data"))
            if np.isfinite(sd) and ss.get("sdlines", True):
                ax.axhline(ref + sd, color=sd_col, lw=sd_w, ls=":", zorder=2)
                ax.axhline(ref - sd, color=sd_col, lw=sd_w, ls=":", zorder=2,
                           label=lab("sd", f"± 1 SD of the data ({sd:.3f})", "± 1 SD of the data"))
            if cut and ss.get("showcut", True) and len(x_ref) and len(x_ref) != N:
                mref = float(x_ref.mean())
                ax.axhline(mref, color=cut_col, lw=cut_w, ls="-.", zorder=2,
                           label=lab("cut", f"Mean before the {ss.get('dparcol')} filter ({mref:.3f}, N = {len(x_ref)})",
                                     f"Mean before the {ss.get('dparcol')} filter"))
            if ss.get("funnel", True) and np.isfinite(sd):
                m = ns
                fpc = np.sqrt(np.clip((N - m) / max(N - 1, 1), 0, 1))
                half = 2 * sd / np.sqrt(m) * fpc
                ax.plot(m, ref + half, "--", color=fun_col, lw=fun_w, zorder=2)
                ax.plot(m, ref - half, "--", color=fun_col, lw=fun_w, zorder=2,
                        label=lab("fun", "Expected 95% range of the running mean (± 2·SD/√n)",
                                  "Expected 95% range of the running mean"))
            what, unit_ref = "mean", ref
        else:
            ref = float(2 * x.std(ddof=1))
            ax.axhline(ref, color=ref_col, lw=ref_w, zorder=2,
                       label=lab("ref", f"2σ of all selected data ({ref:.3f}, N = {N})", "2σ of all selected data"))
            if cut and ss.get("showcut", True) and len(x_ref) > 1 and len(x_ref) != N:
                r0 = float(2 * x_ref.std(ddof=1))
                ax.axhline(r0, color=cut_col, lw=cut_w, ls="-.", zorder=2,
                           label=lab("cut", f"2σ before the {ss.get('dparcol')} filter ({r0:.3f}, N = {len(x_ref)})",
                                     f"2σ before the {ss.get('dparcol')} filter"))
            if ss.get("funnel", True):
                m = ns[1:]
                fpc = np.sqrt(np.clip((N - m) / max(N - 1, 1), 0, 1))
                half = 2 * ref / np.sqrt(2 * (m - 1)) * fpc
                ax.plot(m, ref + half, "--", color=fun_col, lw=fun_w, zorder=2)
                ax.plot(m, np.clip(ref - half, 0, None), "--", color=fun_col, lw=fun_w, zorder=2,
                        label=lab("fun", "Expected 95% spread of 2σ (normal approx.)", "Expected 95% spread of 2σ"))
            what, unit_ref = "2σ", ref
        if ss.get("tolband"):
            band = tol if absolute else tol * abs(ref)
            ax.axhspan(ref - band, ref + band, color=ss.get("refcolor") or "#1f77b4", alpha=0.12, lw=0, zorder=1,
                       label=lab("band", f"Tolerance {tol_txt}", "Tolerance band"))
        lo_i = start - 1
        for gi, g in enumerate(ghosts):
            ax.plot(ns[lo_i:n], g[lo_i:n], color=gh_col, alpha=0.25, lw=gh_w, zorder=1,
                    label=lab("ghost", f"Other random orders ({len(ghosts)})", "Other random orders") if gi == 0 else None)
        w = int(_num(ss.get("smooth")) or 1)
        if ss.get("line", True):
            ax.plot(ns[lo_i:n], _smooth(run[lo_i:n], w), "-", color=line_col, lw=line_w, alpha=0.9, zorder=3,
                    label=lab("line", f"Running {what} line" + (f" (smoothed, window {w})" if w > 1 else ""),
                              f"Running {what} line"))
        ax.plot(ns[lo_i:n], run[lo_i:n], "o", ms=_num(ss.get("dotsize")) or 3.5, color=run_col, alpha=0.6,
                zorder=3, label=lab("dots", f"Running {what} (this random order)", f"Running {what}"))
        ax.plot(ns[n - 1], run[n - 1], "o", ms=9, mfc="none", mec="k", zorder=4)
        allv = np.concatenate([run[lo_i:]] + [g[lo_i:] for g in ghosts] + [[ref]])
        if mode == "mean" and np.isfinite(sd):
            allv = np.concatenate([allv, [ref - sd, ref + sd]])
        allv = allv[np.isfinite(allv)]
        pad = 0.05 * (np.ptp(allv) or 1)
        ylo = allv.min() - pad
        ax.set_ylim(max(0, ylo) if mode != "mean" else ylo, allv.max() + pad)
        if _num(ss.get("ymin")) is not None or _num(ss.get("ymax")) is not None:
            ax.set_ylim(bottom=_num(ss.get("ymin")), top=_num(ss.get("ymax")))
        ax.set_xlim(0, N + 1)
        ax.set_xlabel(ss.get("xlabel") or "Number of measurements, n")
        ax.set_ylabel(ss.get("ylabel") or (f"Running mean of {val}" if mode == "mean" else f"2σ of {val}"))
        if ss.get("title", True):
            t = f"Running {what} of randomly drawn {val}"
            bits = [b for b in [where] + labels if b]
            if c.get("_multi") and bits:          # in a multi-panel figure: just what this panel selects
                ax.set_title("  |  ".join(bits), fontsize=rc["font.size"])
            else:
                ax.set_title(t + ("  |  " + "  |  ".join(bits) if bits else ""), fontsize=rc["font.size"])
        ax.grid(alpha=0.3)
        ax.minorticks_on()
        lg = ss.get("legend", "lower right")
        has = any(not h.get_label().startswith("_") for h in ax.get_lines())
        if lg != "off" and has:
            if lg == "outside":
                ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=rc["legend.fontsize"],
                          framealpha=0.9, frameon=True)
            else:
                ax.legend(loc=lg, fontsize=rc["legend.fontsize"], framealpha=0.9, frameon=True).set_in_layout(False)
        s_n = _settle(run, unit_ref, tol, start, absolute)
        dev = round(100 * (run[n - 1] - unit_ref) / unit_ref, 1) + 0.0 if unit_ref else np.nan
        # with a ± tolerance in units, the difference is shown in units too
        dev_txt = f"{run[n - 1] - unit_ref:+.3g}" if absolute else f"{dev:+.1f}%"
        readout = (f"n = {n}:  {what} = {run[n - 1]:.3f}  ({dev_txt} from full-data {what})\n"
                   f"This run stays within {tol_txt} of the full-data {what} from n = {s_n}")
        settles = []
        if ghosts:
            settles = [v for v in (_settle(r, unit_ref, tol, start, absolute) for r in [run] + list(ghosts)) if v is not None]
        # the readout box shows only the pieces chosen in Look → Readout box
        first = []
        if ss.get("rdvalue", True):
            first.append(f"n = {n}:  {what} = {run[n - 1]:.3f}")
        if ss.get("rddev", True):
            first.append(f"({dev_txt} from full-data {what})" if first else f"{dev_txt} from full-data {what}")
        rows = ["  ".join(first)] if first else []
        if ss.get("rdfull"):
            rows.append(f"Full-data {what} = {unit_ref:.3f}")
        if ss.get("rdN"):
            rows.append(f"N selected = {N}")
        if ss.get("rdsettle", True):
            rows.append(f"This run stays within {tol_txt} of the full-data {what} from n = {s_n}")
        if ss.get("rdorders") and settles:
            rows.append(f"{len(settles)} orders: median n = {int(np.median(settles))}, "
                        f"90th pct = {int(np.percentile(settles, 90))}")
        if ss.get("readout", True) and rows:
            pos = ss.get("readoutpos", "upper left")
            top, right = pos.startswith("upper"), pos.endswith("right")
            ax.text(0.99 if right else 0.01, 0.98 if top else 0.02, "\n".join(rows), transform=ax.transAxes,
                    fontsize=_num(ss.get("rdsize")) or rc["legend.fontsize"], va="top" if top else "bottom",
                    ha="right" if right else "left",
                    bbox=dict(boxstyle="round", fc="white", ec="0.7", alpha=0.9), zorder=6).set_in_layout(False)
        msgs.append(readout.replace("\n", "  ·  "))
        if ghosts:
            if settles:
                msgs.append(f"Across {len(settles)} random orders, n needed to stay within {tol_txt} of the "
                            f"full-data {what}: median {int(np.median(settles))}, "
                            f"90th percentile {int(np.percentile(settles, 90))}")
    return msgs, {"N": int(N), "Nref": int(len(x_ref)), "n": int(n), "start": start}


def subsample_render(cfg_json, fmt="png", dpi=150, meta=""):
    """Running mean or running 2σ of randomly drawn values against the number measured."""
    c = json.loads(cfg_json)
    L = {**{"size": c.get("ss", {}).get("size", "double")}, **(c.get("sslay") or {})}
    c = _ss_look(c, L)
    rc = _style(c)
    with plt.rc_context(rc):
        fig, ax = plt.subplots(figsize=_ss_size(L), layout="constrained")
        _ss_bg(fig, ax, L)
        msgs, sizes = _ss_draw(fig, ax, c, rc)
        buf = io.BytesIO()
        md = {META_KEY[fmt]: meta} if meta and fmt in META_KEY else None
        fig.savefig(buf, format=fmt, dpi=dpi, metadata=md)
    plt.close(fig)
    return _dumps({"data": base64.b64encode(buf.getvalue()).decode("ascii"), "msgs": msgs, **sizes})


def subsample_layout(payload_json, fmt="png", dpi=150, meta=""):
    """Several Subsampling plots in one figure: shared size, grid, linked axes, one set of
    axis titles and panel letters. payload = {base: page settings, layout, panels: [ss…], active}."""
    P = json.loads(payload_json)
    base, L, panels = P.get("base", {}), P.get("layout", {}), P.get("panels") or [{}]
    active = min(int(P.get("active", 0)), len(panels) - 1)
    n = len(panels)
    cols = max(1, min(int(_num(L.get("cols")) or math.ceil(math.sqrt(n))), n))
    rows = math.ceil(n / cols)
    base = _ss_look(base, L)
    rc = _style(base)
    W, H = _ss_size(L)
    pa = _num(L.get("paspect"))
    if pa and not _num(L.get("aspect")) and L.get("size") != "custom":
        H = (W / cols) * pa * rows + 0.4
    out_msgs, sizes, axes = [], {}, []
    with plt.rc_context(rc):
        fig = plt.figure(figsize=(W, H), layout="constrained")
        sp = _num(L.get("spacing"))
        fig.get_layout_engine().set(wspace=sp if sp is not None else 0.02, hspace=sp if sp is not None else 0.02)
        gs = fig.add_gridspec(rows, cols)
        for i, ss in enumerate(panels):
            r, col = divmod(i, cols)
            ax = fig.add_subplot(gs[r, col])
            ss = dict(ss)
            lo = L.get("legendon", "first")             # legends repeat across panels: show once by default
            if lo == "none" or (lo == "first" and i > 0):
                ss["legend"] = "off"
            if L.get("readouton") == "none":
                ss["readout"] = False
            _ss_bg(fig, ax, L)
            m, s = _ss_draw(fig, ax, {**base, "ss": ss, "_multi": True}, rc)
            axes.append((ax, r, col))
            if i == active:
                out_msgs, sizes = m, s
        # linked ranges
        def share(axis, mode):
            if mode in (None, "", "none"):
                return
            groups = {}
            for ax, r, col in axes:
                if not ax.lines:
                    continue
                key = "all" if mode == "all" else (r if mode == "row" else col)
                groups.setdefault(key, []).append(ax)
            for g in groups.values():
                lims = [a.get_xlim() if axis == "x" else a.get_ylim() for a in g]
                lo, hi = min(l[0] for l in lims), max(l[1] for l in lims)
                for a in g:
                    (a.set_xlim if axis == "x" else a.set_ylim)(lo, hi)
        share("x", L.get("sharex"))
        share("y", L.get("sharey"))
        occupied = {(r, col) for _, r, col in axes}
        titles = L.get("titles", "each")
        xt = [a.get_xlabel() for a, _, _ in axes]
        yt = [a.get_ylabel() for a, _, _ in axes]
        for ax, r, col in axes:
            bottom = (r + 1, col) not in occupied
            if L.get("inner") == "ticks" or titles == "outer":
                if not bottom and L.get("inner") == "ticks":
                    ax.tick_params(labelbottom=False)
                if col > 0 and L.get("inner") == "ticks":
                    ax.tick_params(labelleft=False)
            if titles == "outer":
                if not bottom:
                    ax.set_xlabel("")
                if col > 0:
                    ax.set_ylabel("")
            elif titles == "shared":
                ax.set_xlabel("")
                ax.set_ylabel("")
            if L.get("paneltitles") == "off":
                ax.set_title("")
        if titles == "shared":
            fig.supxlabel(L.get("xtitle") or (xt[active] if xt else ""), fontsize=rc["axes.labelsize"])
            fig.supylabel(L.get("ytitle") or (yt[active] if yt else ""), fontsize=rc["axes.labelsize"])
        if L.get("labels", "(a)") != "none" and n > 1:
            for i, (ax, _, _) in enumerate(axes):
                a = chr(97 + i % 26)
                txt = {"(a)": f"({a})", "a": a, "a)": f"{a})", "A": a.upper(), "(A)": f"({a.upper()})"}.get(L.get("labels", "(a)"), f"({a})")
                if L.get("labelpos") == "inside":
                    ax.text(0.02, 0.97, txt, transform=ax.transAxes, fontweight="bold", va="top", ha="left", zorder=7,
                            bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8))
                else:
                    ax.text(-0.02, 1.02, txt, transform=ax.transAxes, fontweight="bold", va="bottom", ha="right")
        buf = io.BytesIO()
        md = {META_KEY[fmt]: meta} if meta and fmt in META_KEY else None
        fig.savefig(buf, format=fmt, dpi=dpi, metadata=md)
        fig.canvas.draw()
        geoms = []
        for ax, _, _ in axes:
            bb = ax.get_position()
            geoms.append({"x0": bb.x0, "x1": bb.x1, "y0": 1 - bb.y1, "y1": 1 - bb.y0})
    plt.close(fig)
    return _dumps({"data": base64.b64encode(buf.getvalue()).decode("ascii"), "msgs": out_msgs, "geoms": geoms,
                   "active": active, **sizes})


def subsample_csv(cfg_json):
    c = json.loads(cfg_json)
    x, *_ = _ss_data(c)
    run, ghosts = _ss_runs(c, x)
    w = int(_num(c.get("ss", {}).get("smooth")) or 1)
    out = pd.DataFrame({"n": np.arange(1, len(x) + 1), "running": run, "running_smoothed": _smooth(run, w)})
    for k, g in enumerate(ghosts, 1):
        out[f"ghost_{k}"] = g
    return out.to_csv(index=False, float_format="%.6g")


def ss_unique(col, src=""):
    """Distinct values of a column (for the subset menu), up to 300."""
    df = DATA.get("tables", {}).get(src) if src else DATA["df"]
    if df is None or col not in df.columns:
        return _dumps([])
    v = df[col].dropna().unique()
    try:
        v = sorted(v)
    except TypeError:
        pass
    return _dumps([_fmt_cell(x) for x in v[:300]])


# ------------------------------------------------- standalone use (offline)
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("usage: python plotter.py DATAFILE SETTINGS.json [out.pdf]")
        sys.exit(1)
    data_file, cfg_file = sys.argv[1], sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else "figure.pdf"
    with open(cfg_file) as f:
        cfg = json.load(f)
    load_file(data_file, data_file, cfg.get("header", "auto"), cfg.get("sheet"),
              cfg.get("headerrow", 1), cfg.get("skipafter", 0))
    fig, msgs, _ = make_figure(cfg)
    with plt.rc_context(_style(cfg)):
        fig.savefig(out, dpi=cfg.get("dpi", 600))
    for m in msgs:
        print(m)
    print("saved", out)
