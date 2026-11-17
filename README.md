# SciPlot

**Publication-ready plots from CSV, TXT or Excel files, made with matplotlib in your browser.**

A static site that runs real Python (numpy, pandas, matplotlib) in the browser via [Pyodide](https://pyodide.org). No server is involved, and your data never leaves your computer.

Live: https://malcmcmreal.github.io/sciplot/

## What it does

- **Data:** drop in a `.csv`, `.tsv`, `.txt`, `.dat`, `.xlsx` or `.xls` file, or paste cells copied from Excel. It detects the delimiter and the header row, skips `#`/`%` comment lines, and lets you pick a sheet in Excel files. A data table shows every row.
- **Plots:** scatter, line, line + markers, step, grouped bar and histogram, with any number of series and X/Y error bars.
- **Fits:** linear, quadratic or cubic per series, optionally weighted by 1/σ. The equation and R² appear in the legend and below the plot.
- **Interactive preview:**
  - Hover to read off coordinates and see the nearest point (sample name, row and values).
  - Drag a box to zoom (this fills in the axis ranges). Double-click to reset.
  - Use the **Exclude points** tool to click points, or click row numbers in the table, to drop outliers from the plot and the fit. Excluded points can be shown as grey ×.
- **Publication style:** journal figure sizes (89 / 140 / 183 mm), inward ticks on all four sides, minor ticks, and fonts in sans, STIX serif (Times-like) or Computer Modern (LaTeX). Colour palettes include Okabe–Ito (colour-blind safe), viridis and greyscale. Markers can be open or filled with a different shape per series, and you can add panel labels such as "(a)". Axis labels support maths text (`$\delta^{18}$O`).
- **Export:** PDF (TrueType fonts embedded), SVG (text stays editable) and PNG at 300/600/1200 dpi. Exported figures are exactly the chosen size.
- **Settings:** your last settings are remembered. **Copy link** puts the settings in the URL so you can bookmark or share a style, and settings can be saved and loaded as JSON (useful as a house style for a paper).

## Reproducing a figure offline

**Download Python + settings** saves `plotter.py` plus a `*_plot.json` settings file. With numpy, pandas, matplotlib and openpyxl installed:

```sh
python plotter.py data.csv data_plot.json figure.pdf
```

`plotter.py` is the same code the site runs, so the output matches.

## Files

| File | Purpose |
| --- | --- |
| `index.html` | Page layout and controls |
| `app.js` | UI, file loading, interactive overlay (hover, zoom, exclude) |
| `plotter.py` | All data reading and plotting (matplotlib) – runs in Pyodide or normal Python |
| `style.css` | Styling |
| `examples/` | Example data files offered in the "Or try" menu |

To add an example, put the file in `examples/` and add an `<option>` to the `#example` menu in `index.html`.

## Running locally

The site has no build step, but it loads `plotter.py` with `fetch`, so you need a local server rather than opening the file directly:

```sh
python3 -m http.server
# then open http://localhost:8000
```

## Deploying changes

GitHub Pages serves files with a short browser cache. After changing `app.js` or `style.css`, bump the `?v=` number in `index.html`. After changing `plotter.py`, bump `PY_VERSION` at the top of `app.js` as well. Both changes make visitors get the new version straight away.

## Licence

MIT
