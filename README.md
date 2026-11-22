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
- **Reference lines:** mean (± SD), median (± MAD) and, for x–y data with an error column, the inverse-variance weighted mean (± σ, with MSWD and n). Lines can be drawn for Y, X or both, with optional ±1σ or ±2σ shading and their values in the legend. In histograms they are vertical lines on the binned variable. Also custom horizontal and vertical lines and a 1:1 line. Lines can also come from **other columns** – their mean/median, mean ± 1 or 2 SD, min and max, or any **formula** such as `mean(x) + 2*sd(x)`, `percentile(x, 95)` or `max("Dper")` (commas give several lines) –  (each with its own column, horizontal or vertical placement, optional error column for a weighted mean, and colour), with or without the plotted series' own lines; the line colour, width and band opacity are set independently of the points.
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

## Where things are

Settings are grouped by the part of the figure they change, the way GraphPad Prism, Origin and Plotly organise theirs. There are six tabs:

| Tab | What's in it |
|---|---|
| **Data** | load a file, sheets and header rows · export (file name, PDF/SVG/PNG) · saved plots and projects |
| **Plot** | plot type, X and series (Y, error bars, per-series style) · type options (bins, KDE, polar, violin) · markers, size by, shape by, vectors |
| **Stats** | mean / median / SD / formula lines · fixed lines · fits · group summaries |
| **Axes** | title, panel label, axis titles and their size/position · ranges, log, invert, ticks, grid, despine, hide axes |
| **Colour** | palette · colour by a column · colour map, range and colour bar · legend, including one shared key for multi-panel figures |
| **Figure** | figure size, theme, font · panels: grid, spacing, shared axes, panel letters, this panel's size |

The sidebar opens on Plot once data is loaded and remembers the last tab. The tabs stay anchored at the top while you scroll. Less-used settings sit in small fold-out menus. Calculated columns have their own panel under the figure.

## Titles and axis titles

Under **Axes → Titles & labels → Title & axis-title size and position**, each panel can set its own sizes for the title, X title, Y title and tick labels. The title can sit left, centre or right and be bold, the X title left, centre or right, and the Y title bottom, middle or top, on the left or right side. Each has a gap (in pt) that moves it closer to or further from the plot.

## Group summaries and the legend

**Stats → Group summary → Group points, line & error bars** styles the mean/median points:
- **Points:** fill (series colour, presets or custom), shape and size, outline colour and width, and opacity. **Colour points by** a column colours each group's point by the group's mean of it, with its own colour map, range and colour bar.
- **Line:** whether to join the points, plus the line's colour, width and style.
- **Error bars:** colour, width and cap size.
- **Legend label:** custom text for the group entry.

**Colour → Legend → Items in the legend** lists every entry of the active panel; untick one to leave it out. Entries are matched ignoring their numbers, so a hidden "mean = …" stays hidden when the data change.

## Calculated columns

The **Calculated columns** panel sits under the figure, above the data table. Each column has a name and a formula, and shows its result as you type (count, mean and range, or the error). Columns can be moved up or down (a formula can use columns above it), duplicated or removed. They appear in every column menu and in the data table (orange headings), and are saved with the plot.

- **From a recipe…** starts common columns: ratio, difference, sum, row mean, z-score, 0–1 scaling, log₁₀, a value minus its group's mean (e.g. per sheet), group mean, percentile rank, folding angles to ±90°, a 0/1 flag, or binning into classes.
- **Insert columns & functions** lists every column and function as buttons that type into the formula you're editing:
  - maths and angle functions (`sind`, `wrap90`, …)
  - whole-column (`mean`, `zscore`, `norm01`, `rank`, `cumsum`, `diff`)
  - across columns per row (`rowmean`, `rowsum`, `rowmin`, `rowmax`)
  - per group (`groupmean("Dpar", "Sheet")`, `groupmedian`, `groupstd`, `groupmin`, `groupmax`, `groupn`)
  - logic (`where`, `isnan`)

## Multi-panel figures

Use **+ Duplicate** or **+ Blank** above the figure to add panels. Duplicating copies the active panel's full setup so you only change what differs; a blank panel starts empty (no columns chosen) but keeps the figure's fonts, theme and palette. Click a chip, e.g. **(b)**, or click directly on a panel to make it active. The sidebar then edits only that panel, and a dashed outline marks it. The **←/→** buttons reorder panels and **Remove** deletes the active one.

Each panel can plot a different sheet of the workbook: activate the panel, then pick its sheet. Header settings, combined sheets and calculated columns are shared by all panels, and excluded points are remembered per sheet. Overall controls are under **Figure → Panels**:

- columns
- figure size: journal single, 1.5 or double column, or custom
- panel aspect and spacing, plus column/row gaps in mm that can go negative to pull panels together once inner labels are hidden (polar panels get rows shaped to fit them)
- shared X/Y limits, per row, column or across all panels (panels are only linked when they plot the same variable)
- hiding inner tick labels or titles
- mirroring axes to the right/top
- **Colour bar** options (Colour tab): position (right, left, top, bottom), length as % of the panel's height and thickness as % of its width (so the bar follows a panel with its own size or aspect), ticks shown / labels only / hidden, number of ticks, tick direction and minor ticks
- **Ignore extreme values** (Colour tab) sets a numeric colour range to the 2nd–98th percentile, so a few outliers don't squash every other point into one colour
- one shared legend / colour key for the whole figure (right or below) instead of one per panel; panels colouring by the same column always share one colour scale
- automatic panel letters, e.g. (a) / a / A, outside or inside each panel

Each panel can also hide its own X axis, Y axis or both, and set its own size (Figure → Panels → *This panel's size*): how many columns and rows it spans (e.g. one wide panel across the top) and a fixed height ÷ width. **Column widths** and **Row heights** under Figure layout take relative values such as `2, 1`. Exports, saved plots and `.sciplot` projects store the whole figure. The group-summary CSV gains a `panel` column.

## Subsampling tab

A second tab for "how many measurements do I need?" figures. Values are drawn in a random order (without replacement) and the plot shows the **running mean** or **running 2σ** (2 × sample SD) of the first *n* as measurements are added:

- reference line for the full selection (mean ± 1 SD, or 2σ), and the expected 95% spread for a sample of *n* (± 2·SD/√n for the mean; σ/√(2(n−1)) normal approximation for 2σ), both with a finite-population correction;
- filters: a subset (e.g. *Grain Orientation* = 10 after combining sheets, or another sheet), an angle range and a Dpar range – with a dash-dot line showing the value *before* the Dpar cut so any bias is visible;
- seed / new random order, ghost runs (other random orders) with the median and 90th-percentile *n* needed to stay within ±tolerance % of the full-data value, smoothing of the connecting line, readout box;
- an *n* slider and Play button to watch the run build up; export as PDF/SVG/PNG (reopens this tab) and the running values as CSV.

It uses the data loaded on the Plot tab, including combined sheets and calculated columns, and is saved with the plot.


**Look → Lines & colours** has one row per figure element: running dots, running line, full-data value, ± 1 SD, expected 95% range, the value before a cut, and ghost runs. Each row sets that element's colour and width (dot size for the dots) and whether it appears in the legend. *Numbers in legend labels* turns the values, N and SD in the labels on or off. The legend can also go outside the plot.

The Subsampling sidebar has its own tabs: **Plotting** (data, what to track, filters, random draws), **Appearance** (size, theme, colour scheme, panels, lines and colours, legend, readout, labels) and **Save** (export PDF/SVG/PNG/CSV with its own file name, saved plots and project files; saving here reopens the Subsampling figure, panels included).

**Figure & panels** (Appearance tab):
- **Size:** a preset or custom W × H, plus an optional height ÷ width.
- **Look:** the tab's own theme (or the Plot tab's), a one-click colour scheme (seaborn deep, colorblind, muted, bright, dark, Okabe–Ito, greyscale), a custom plot background and font size.
- **Panels:** **+ Duplicate** above the plot adds panels (e.g. one per orientation), each with its own settings. Click a panel or its chip to edit it. With several panels you can set columns, same X/Y range, axis titles on every panel / outer panels only / one shared X and Y title, hidden inner tick labels, panel titles, panel letters, the legend on the first panel only, readout boxes on or off, and spacing.

**Tolerance** (Random draws) is either a % of the full-data value or a fixed ± in the tracked value's own units, e.g. ±1 for an angle. *Shade the tolerance band* draws it around the full-data value.

**Look → Readout box** chooses what the box shows: the value at n, the difference from the full data, where the run stays within the tolerance, total N, the full-data value, and a ghost-run summary. It also sets the box's corner and text size.

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
