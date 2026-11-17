"""
plotter.py — publication-style plotting core.

Runs in the browser via Pyodide (loaded by index.html), and also runs
unchanged in a normal Python install, so any figure made on the site can
be reproduced from a script.
"""
import io
import csv
import json
import base64

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import AutoMinorLocator

try:                      # seaborn: palettes, themes, distribution plots, CI bands
    import seaborn as sns
except ImportError:       # plotter.py still works without it (matplotlib-only features)
    sns = None

DATA = {"df": None}

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
SNS_THEMES = {"ticks", "white", "whitegrid", "darkgrid"}
CATEGORICAL = {"bar", "box", "violin", "strip", "swarm"}
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "<", ">", "h"]

FIG_SIZES = {          # inches (w, h)
    "single": (3.5, 2.6),     # single journal column (~89 mm)
    "onehalf": (5.5, 3.8),    # 1.5 column (~140 mm)
    "double": (7.2, 4.4),     # full page width (~183 mm)
    "screen": (8.0, 5.0),
}


# ----------------------------------------------------------------- loading
def _read_text_table(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        lines = [ln.rstrip("\r\n") for ln in f]
    lines = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith(("#", "%", "//"))]
    if not lines:
        raise ValueError("File contains no data rows.")
    sample = "\n".join(lines[:50])
    try:
        sep = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        sep = None
    if sep is None or all(ln.count(sep) == 0 for ln in lines[:20]):
        sep = r"\s+"  # whitespace-delimited (typical .txt / instrument output)
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
            df[c] = df[c].astype(str).str.strip()
    return df.reset_index(drop=True)


def load_file(path, name, header="auto", sheet=None):
    """Load csv / tsv / txt / dat / xlsx / xls into the active DataFrame."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    sheets = []
    if ext in ("xlsx", "xlsm", "xls", "ods"):
        xl = pd.ExcelFile(path)
        sheets = xl.sheet_names
        sheet = sheet if sheet in sheets else sheets[0]
        raw = xl.parse(sheet, header=None)
    else:
        raw = _read_text_table(path)
        sheet = None
    df = _coerce(_apply_header(raw, header))
    DATA["df"] = df
    head = df.head(2000)
    preview = head.astype(object).where(head.notna(), "").values.tolist()
    return json.dumps({
        "columns": list(df.columns),
        "numeric": [bool(pd.api.types.is_numeric_dtype(df[c])) for c in df.columns],
        "nrows": int(len(df)),
        "preview": [[_fmt_cell(v) for v in r] for r in preview],
        "rowlabels": _row_labels(df),
        "sheets": sheets,
        "sheet": sheet,
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


# --------------------------------------------------------------- plotting
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


def _fit_label(coef, order, r2, xname="x"):
    terms = []
    for p, a in zip(range(order, -1, -1), coef):
        s = f"{a:.3g}"
        terms.append(s if p == 0 else f"{s}{xname}" if p == 1 else f"{s}{xname}$^{p}$")
    eq = " + ".join(terms).replace("+ -", "− ")
    return f"y = {eq}  (R² = {r2:.3f})"


def _num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


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
            sns.kdeplot(x=y, ax=ax, color=colors[i], fill=c.get("kdefill", True),
                        alpha=0.25 if c.get("kdefill", True) else 1, linewidth=c.get("linewidth", 1.2),
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


def make_figure(c, df=None):
    df = DATA["df"] if df is None else df
    if df is None:
        raise ValueError("No data loaded.")
    msgs, points = [], []
    series = [s for s in c.get("series", []) if s.get("y") in df.columns]
    kind = c.get("kind", "scatter")
    size = FIG_SIZES.get(c.get("size", "single"), None)
    if size is None:
        size = (_num(c.get("width")) or 3.5, _num(c.get("height")) or 2.6)

    with plt.rc_context(_style(c)):
        fig, ax = plt.subplots(figsize=size, layout="constrained")
        if not series:
            ax.text(0.5, 0.5, "Choose a Y column to plot", ha="center", va="center",
                    transform=ax.transAxes, color="0.5")
            return fig, msgs, points

        colors = _colors(c.get("palette", "okabe-ito"), len(series))
        xcol = c.get("x") if c.get("x") in df.columns else None
        open_m = c.get("markerfill", "filled") == "open"
        vary_m = c.get("varymarkers", True)

        if kind == "bar":
            xs = df[xcol] if xcol else pd.Series(np.arange(1, len(df) + 1))
            pos = np.arange(len(df))
            n = len(series)
            w = 0.8 / n
            for i, s in enumerate(series):
                err = df[s["yerr"]] if s.get("yerr") in df.columns else None
                ax.bar(pos + (i - (n - 1) / 2) * w, df[s["y"]], width=w, yerr=err,
                       color=colors[i], edgecolor="black", linewidth=0.6,
                       label=s.get("label") or s["y"], error_kw={"elinewidth": 0.8})
            ax.set_xticks(pos)
            ax.set_xticklabels([_fmt_cell(v) for v in xs],
                               rotation=c.get("xrot", 0) or 0,
                               ha="right" if c.get("xrot") else "center")
            ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
            ax.tick_params(axis="x", which="both", top=False)
        elif kind == "hist" and c.get("histkde") and sns is not None:
            bins = int(c["bins"]) if str(c.get("bins", "")).isdigit() else "auto"
            stat = "density" if c.get("density") else "count"
            for i, s in enumerate(series):
                y = pd.to_numeric(df[s["y"]], errors="coerce")
                y = y[~df.index.isin([int(r) for r in c.get("excluded", [])])].dropna()
                sns.histplot(x=y, bins=bins, stat=stat, kde=True, ax=ax, color=colors[i],
                             element="step" if len(series) > 1 else "bars",
                             fill=len(series) == 1, edgecolor="black" if len(series) == 1 else None,
                             linewidth=0.6, alpha=0.6 if len(series) == 1 else 1,
                             label=s.get("label") or s["y"], line_kws={"linewidth": 1.2})
        elif kind == "hist":
            bins = int(c["bins"]) if str(c.get("bins", "")).isdigit() else "auto"
            for i, s in enumerate(series):
                y = pd.to_numeric(df[s["y"]], errors="coerce")
                y = y[~df.index.isin([int(r) for r in c.get("excluded", [])])].dropna()
                lbl = s.get("label") or s["y"]
                if len(series) == 1:
                    ax.hist(y, bins=bins, color=colors[i], edgecolor="black",
                            linewidth=0.6, label=lbl, density=c.get("density", False))
                else:
                    ax.hist(y, bins=bins, histtype="step", color=colors[i], linewidth=1.2,
                            label=lbl, density=c.get("density", False))
        elif kind in ("kde", "box", "violin", "strip", "swarm"):
            _distribution(ax, df, c, series, colors, xcol, msgs)
        else:
            excluded = set(int(r) for r in c.get("excluded", []))
            for i, s in enumerate(series):
                y = pd.to_numeric(df[s["y"]], errors="coerce")
                if xcol:
                    x = df[xcol]
                    x_numeric = pd.api.types.is_numeric_dtype(x)
                else:
                    x, x_numeric = pd.Series(np.arange(1, len(df) + 1)), True
                yerr = pd.to_numeric(df[s["yerr"]], errors="coerce") if s.get("yerr") in df.columns else None
                xerr = pd.to_numeric(df[s["xerr"]], errors="coerce") if s.get("xerr") in df.columns else None
                valid = y.notna() & (x.notna() if x_numeric else True)
                excl = valid & df.index.isin(excluded)
                mask = valid & ~excl
                col = colors[i]
                lbl = s.get("label") or s["y"]

                if excl.any() and x_numeric and c.get("showexcluded", True):
                    ax.plot(x[excl], y[excl], linestyle="none", marker="x", color="0.6",
                            markersize=c.get("markersize", 4), markeredgewidth=0.8, zorder=1)
                if x_numeric:
                    points.append({"label": lbl, "color": col,
                                   "pts": [[int(r), float(x[r]), float(y[r]), bool(excl[r])]
                                           for r in df.index[valid]]})

                x, y = x[mask], y[mask]
                yerr = yerr[mask].fillna(0) if yerr is not None else None
                xerr = xerr[mask].fillna(0) if xerr is not None else None
                if kind in ("line", "step", "linemarkers") and x_numeric and c.get("sortx", True):
                    order = np.argsort(x.values, kind="stable")
                    x, y = x.iloc[order], y.iloc[order]
                    yerr = yerr.iloc[order] if yerr is not None else None
                    xerr = xerr.iloc[order] if xerr is not None else None

                mk = MARKERS[i % len(MARKERS)] if vary_m else "o"
                mstyle = dict(marker=mk, markeredgecolor=col,
                              markerfacecolor="white" if open_m else col,
                              markeredgewidth=0.8)
                if kind == "scatter":
                    fmt = dict(linestyle="none", **mstyle)
                elif kind == "linemarkers":
                    fmt = dict(linestyle="-", **mstyle)
                elif kind == "step":
                    fmt = dict(linestyle="-", drawstyle="steps-mid", marker="none")
                else:
                    fmt = dict(linestyle="-", marker="none")
                if yerr is not None or xerr is not None:
                    ax.errorbar(x, y, yerr=yerr, xerr=xerr, color=col, ecolor=col,
                                elinewidth=0.7, label=lbl, zorder=2, **fmt)
                else:
                    ax.plot(x, y, color=col, label=lbl, zorder=2, **fmt)

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
                    if ci and sns is not None:
                        sns.regplot(x=xv, y=yv, order=fit, ci=ci, scatter=False, ax=ax,
                                    color=col, truncate=True, seed=0, n_boot=500,
                                    line_kws={"linewidth": 0, "zorder": 1})
                        if w is not None:
                            msgs.append(f"{lbl}: confidence band is unweighted (bootstrap)")
                    ax.plot(xx, np.polyval(coef, xx), color=col, linestyle="--",
                            linewidth=0.9, marker="none", zorder=3,
                            label=_fit_label(coef, fit, r2) if c.get("fitlegend", True) else None)
                    msgs.append(f"{lbl}: " + _fit_label(coef, fit, r2)
                                + (f"  [{int(excl.sum())} point(s) excluded]" if excl.any() else ""))
                elif fit and not x_numeric:
                    msgs.append(f"{lbl}: fit skipped (X column is not numeric)")

        # ---- axes
        xl = c.get("xlabel")
        yl = c.get("ylabel")
        dist_grouped = kind in ("box", "violin", "strip", "swarm") and xcol \
            and not pd.api.types.is_numeric_dtype(df[xcol]) and len(series) == 1
        if xl is None or xl == "":
            if kind in ("hist", "kde"):
                xl = series[0]["y"] if len(series) == 1 else ""
            elif kind in ("box", "violin", "strip", "swarm"):
                xl = xcol if dist_grouped else ""
            else:
                xl = xcol or "Index"
        if yl is None or yl == "":
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

        leg = c.get("legend", "best")
        if leg != "off" and (len(series) > 1 or any(int(s.get("fit") or 0) for s in series)
                             or c.get("legendsingle", False)):
            h, l = ax.get_legend_handles_labels()
            names = [s.get("label") or s["y"] for s in series]
            order = sorted(range(len(l)), key=lambda k: (l[k].startswith("y = "),
                                                        names.index(l[k]) if l[k] in names else k))
            h, l = [h[k] for k in order], [l[k] for k in order]
            if h and leg == "outside":
                fig.legend(h, l, loc="outside right upper")
            elif h:
                ax.legend(h, l, loc=leg)

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

        if c.get("panel"):
            ax.text(-0.02, 1.02, c["panel"], transform=ax.transAxes, ha="right", va="bottom",
                    fontweight="bold", fontsize=c.get("fontsize", 9) + 2)
    return fig, msgs, points


def _geometry(fig, ax):
    """Axes position (fractions of the figure, origin top-left) and data limits."""
    fig.canvas.draw()
    p = ax.get_position()
    return {"x0": p.x0, "x1": p.x1, "y0": 1 - p.y1, "y1": 1 - p.y0,
            "xlim": list(map(float, ax.get_xlim())), "ylim": list(map(float, ax.get_ylim())),
            "xlog": ax.get_xscale() == "log", "ylog": ax.get_yscale() == "log"}


def render(cfg_json, fmt="png", dpi=200):
    """Render the figure; returns JSON {data (base64), msgs, geom, points}."""
    c = json.loads(cfg_json)
    fig, msgs, points = make_figure(c)
    buf = io.BytesIO()
    with plt.rc_context(_style(c)):
        geom = _geometry(fig, fig.axes[0]) if fmt == "png" else None
        fig.savefig(buf, format=fmt, dpi=dpi)
    plt.close(fig)
    return json.dumps({"data": base64.b64encode(buf.getvalue()).decode("ascii"), "msgs": msgs,
                       "geom": geom, "points": points,
                       "colors": _colors(c.get("palette", "okabe-ito"), len(c.get("series", []))),
                       "swatches": _colors(c.get("palette", "okabe-ito"), 8)})


# ------------------------------------------------- standalone use (offline)
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("usage: python plotter.py DATAFILE CONFIG.json [out.pdf]")
        sys.exit(1)
    data_file, cfg_file = sys.argv[1], sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else "figure.pdf"
    with open(cfg_file) as f:
        cfg = json.load(f)
    load_file(data_file, data_file, cfg.get("header", "auto"), cfg.get("sheet"))
    fig, msgs, _ = make_figure(cfg)
    with plt.rc_context(_style(cfg)):
        fig.savefig(out, dpi=cfg.get("dpi", 600))
    for m in msgs:
        print(m)
    print("saved", out)
