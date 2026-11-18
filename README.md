# SciPlot

**Publication-ready plots from CSV, TXT or Excel files, made with matplotlib in your browser.**

A static site that runs real Python (numpy, pandas, matplotlib, seaborn) in the browser via [Pyodide](https://pyodide.org). No server is involved, and your data never leaves your computer.

Live: https://malcmcmreal.github.io/sciplot/

## What it does

- **Data:** drop in a `.csv`, `.tsv`, `.txt`, `.dat`, `.xlsx` or `.xls` file, or paste cells copied from Excel. It detects the delimiter and the header row, skips `#`/`%` comment lines and metadata blocks above the table, and lets you pick a sheet in Excel files. A data table shows every row.
- **Header row:** choose the header row yourself (click it in the **Raw file** view, or type its number). Rows above it are ignored, and you can skip rows straight after it, such as a units row.
- **Plots:** scatter, line, line + markers, step, grouped bar and histogram, with any number of series and X/Y error bars.
- **Colour, size and shape by variable:** in scatter and line + marker plots, colour points by any column (numeric columns use a colour map with a colour bar; text columns use the palette with a legend), size them by a column, and set marker shape by a column. Colour range, log colour scale and colour-bar label are adjustable. Hovering over a point shows its mapped values.
- **Per-series styling:** marker shape, colour (or from the palette) and line style per series, plus global marker size, opacity and marker edge colour.
- **Histograms:** automatic bins, a fixed bin width, or a set number of bins, plus a bin range and bin outline colour. Bars can be coloured by the mean, median, minimum, maximum or standard deviation of another column within each bin (e.g. mean eU per age bin), with a colour bar. Optional KDE curve.
- **Reference lines:** mean (± SD), median (± MAD) and, for x–y data with an error column, the inverse-variance weighted mean (± σ, with MSWD and n). Lines can be drawn for Y, X or both, with optional ±1σ or ±2σ shading and their values in the legend. In histograms they are vertical lines on the binned variable. Also custom horizontal and vertical lines and a 1:1 line.
- **Series from other sheets:** each series can draw from the main table or from any other sheet in the workbook (e.g. a summary sheet), with its own X and Y columns, size and opacity – e.g. raw pits from the combined sheets with the per-tilt medians from an `all` sheet on top.
- **Group summary:** one point per group (default: each X value, e.g. each grain orientation after combining sheets, or any other column) at the group's mean, median or mode, with ± SD, ± SE, ± 95% CI, interquartile-range or min–max bars, optionally joined by a line. The table (n, mean, median, SD, SE, quartiles, range per group) downloads as CSV.
- **Polar plots:** X column → angle, Y column → distance from the centre. *Quarter* stretches any angle range (e.g. grain tilt 0–30°) over a 90° (or 180/270/360°) arc with 0 pointing up; *Half* uses true angles from −90° (left) through 0° (up) to +90° (right), e.g. for Dpar directions; *Full* covers the whole circle. Start direction, clockwise/anticlockwise, angle jitter for stacked values, arcs for Y reference lines and rays for X reference lines.
- **Vectors:** draw points as short lines whose direction, length and colour each come from a column (e.g. pit direction, Dpar, grain tilt), on polar or x–y plots. Lines can be centred on the point (axial data) or start at it.
- **Combine all sheets:** stack every sheet of a workbook into one table with a *Sheet* column (sheet names that are numbers become numbers, e.g. one sheet per grain tilt).
- **Formulas anywhere:** every column menu (X, Y, error bars, colour, size, shape, vector direction and length, histogram colour) has **ƒ Formula…**. Write maths on columns in place, e.g. `"Angle to Caxis" - 90`, `Dpar + Dper`, `"Angle to Caxis" + "Grain Orientation"`. Column names go in "quotes" (plain-word names can be bare); `+ - * / ^`; functions `sin cos tan` (radians), `sind cosd tand` (degrees), `arctan2 degrees radians sqrt abs log ln log10 exp round min max clip where mod`, `wrap90` (fold an axial angle into −90…+90), `wrap180`, `wrap360`, and `mean median std` of a column. Formulas are saved with the plot and also listed under **Calculated columns**, where you can add your own named ones.
- **Combine all sheets** can name the new column (e.g. *Grain Orientation* instead of *Sheet*).
- **Distributions (seaborn):** histogram (optional KDE overlay), KDE with rug, box, violin, strip and swarm plots. These compare the Y series side by side, or split one series by a text column (e.g. sample or lithology).
- **Fits:** linear, quadratic or cubic per series, optionally weighted by 1/σ, with an optional 68/95/99% confidence band (seaborn `regplot`, bootstrapped). The equation and R² appear in the legend and below the plot.
- **Interactive preview:**
  - Hover to read off coordinates and see the nearest point (sample name, row and values).
  - Drag a box to zoom (this fills in the axis ranges). Double-click to reset.
  - Use the **Exclude points** tool to click points, or click row numbers in the table, to drop outliers from the plot and the fit. Excluded points can be shown as grey ×.
- **Publication style:** journal figure sizes (89 / 140 / 183 mm), inward ticks on all four sides, minor ticks, and fonts in sans, STIX serif (Times-like) or Computer Modern (LaTeX). Colour palettes include Okabe–Ito and all the main seaborn palettes (colorblind, deep, muted, pastel, Set2, rocket, mako, flare, crest, vlag and more). Themes: a boxed publication style or seaborn's ticks / white / whitegrid / darkgrid, with optional despine. Markers can be open or filled with a different shape per series, and you can add panel labels such as "(a)". Axis labels support maths text (`$\delta^{18}$O`).
- **Export:** PDF (TrueType fonts embedded), SVG (text stays editable) and PNG at 300/600/1200 dpi. Exported figures are exactly the chosen size.
- **Saving plots:**
  - The current plot (settings *and* data) is kept in the browser automatically and reopens next time you visit; **New plot** clears it.
  - **Saved plots** keeps any number of named plots in the browser, with thumbnails. Click one to reopen it.
  - **Download project file** saves a `.sciplot` file (settings + data) you can keep with the paper, move to another computer or send to a colleague; open it with **Open project or figure** or drop it on the page.
  - Every exported PNG/SVG/PDF carries its setup in its metadata, so dropping the figure back onto SciPlot reopens it for editing. Without data, SciPlot asks for the data file and rebuilds the plot; tick *Also store the data inside exported figures* to make the figure fully self-contained (avoid for figures sent to journals if the data are private).
  - **Save style / Apply style** keep settings only, for reusing a look on other data; **Copy link** puts the settings in the URL.
  - Everything stays on your computer – nothing is uploaded.

## Reproducing a figure offline

**Download Python + settings** saves `plotter.py` plus a `*_plot.json` settings file. With numpy, pandas, matplotlib, seaborn and openpyxl installed:

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
