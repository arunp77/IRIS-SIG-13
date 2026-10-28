IRIS-SIG-13 technical documentation
===================================

IRIS-SIG-13_documentation.pdf   the finished document (6 pages, A4)
main.tex                        the LaTeX source (plain article class, one black-and-white TikZ figure)

The figure of the loaded points is taken from ../docs/figures/quicklook.png.

Rebuild after editing main.tex:

    cd documentation
    latexmk -pdf -outdir=build main.tex
    cp build/main.pdf IRIS-SIG-13_documentation.pdf

Needs a normal TeX Live (tested with TeX Live 2025): geometry, booktabs, tabularx, listings,
tikz, needspace, hyperref, url. No bibliography, no external fonts.

The numbers come from the full run of 27 October 2026 (data/sample/, docs/quality_report.md). If the data
or the code changes, update Sections 1, 6 and 7.
